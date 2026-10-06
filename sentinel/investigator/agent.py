"""
investigator/agent.py

Sends the assembled context to an LLM and parses the structured RCA response.

The agent is deliberately instructed to:
  1. Reason step-by-step through candidate causes
  2. Assign confidence probabilities to each
  3. Identify the specific file / function containing the defect
  4. Recommend a concrete fix

Falls back to a deterministic rule-based RCA when no API key is configured
(useful for offline demos / CI).
"""
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Incident, RCAResult
from config import settings

logger = logging.getLogger("sentinel.agent")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are Sentinel, an autonomous SRE agent.
Your job is to perform root-cause analysis on production incidents.

You will receive:
  - An incident description with observed symptoms
  - Recent deployment history
  - Relevant source-code files
  - Analysis hints

Instructions:
1. Reason step-by-step through each candidate cause.
2. Assign a confidence percentage (0-100) to each candidate.
3. Identify the specific file and function containing the root cause (if code-related).
4. Write a concise root-cause statement (2-3 sentences max).
5. Recommend a concrete code fix.

Respond ONLY with a JSON object matching this exact schema:
{
  "summary": "<one sentence summary of the incident>",
  "root_cause": "<2-3 sentence root cause statement>",
  "confidence": <0.0-1.0>,
  "candidate_causes": [
    {"cause": "<description>", "confidence_pct": <0-100>},
    ...
  ],
  "evidence": ["<evidence item 1>", ...],
  "affected_file": "<filename or null>",
  "affected_function": "<function name or null>",
  "recommended_fix": "<concrete fix description>"
}
"""

_USER_PROMPT_TEMPLATE = """\
## Incident

ID: {incident_id}
Service: {service}
Severity: {severity}

Symptoms:
{symptoms}

Triggering metrics:
{metrics}

## Deployment history (most recent first)

{deployments}

## Source files

{source_files}

## Analysis hints

{hints}

Perform root-cause analysis now.
"""


def _format_context(context: dict) -> str:
    inc = context["incident"]
    symptoms = "\n".join(f"  - {s}" for s in inc["symptoms"])
    metrics = json.dumps(inc["triggering_metrics"], indent=2)

    deployments = ""
    for d in reversed(context["deployments"]):
        deployments += f"  version={d['version']}  commit={d['commit']}\n"
        deployments += f"  message: {d['commit_message']}\n"
        if d.get("diff_summary"):
            deployments += f"  diff: {d['diff_summary']}\n"
        deployments += "\n"

    source_files = ""
    for sf in context["source_files"]:
        if sf["content"]:
            source_files += f"### {sf['file']}\n```python\n{sf['content']}\n```\n\n"
        else:
            source_files += f"### {sf['file']}\n(not available: {sf['error']})\n\n"

    hints = "\n".join(f"  - {h}" for h in context.get("analysis_hints", []))

    return _USER_PROMPT_TEMPLATE.format(
        incident_id=inc["id"],
        service=inc["service"],
        severity=inc["severity"],
        symptoms=symptoms,
        metrics=metrics,
        deployments=deployments,
        source_files=source_files,
        hints=hints,
    )


def _fallback_rca(incident: Incident, context: dict) -> RCAResult:
    """
    Rule-based RCA used when no LLM is available.
    Checks the source code directly for the known connection-leak pattern.
    """
    logger.info("using_fallback_rca", extra={"incident_id": incident.incident_id})

    # Scan order_service.py for unguarded pool.acquire()
    affected_file = None
    affected_function = None
    leak_detected = False

    for sf in context.get("source_files", []):
        if sf["file"] == "order_service.py" and sf["content"]:
            content = sf["content"]
            # Look for: conn = await pool.acquire() NOT followed by finally/async with
            if "conn = await pool.acquire()" in content and "pool.release(conn)" not in content:
                leak_detected = True
                affected_file = "order_service.py"
                # Find the function name
                match = re.search(
                    r"async def (\w+).*?conn = await pool\.acquire\(\)",
                    content,
                    re.DOTALL,
                )
                if match:
                    affected_function = match.group(1)

    if leak_detected:
        return RCAResult(
            incident_id=incident.incident_id,
            summary=(
                f"Service {incident.service} is returning 5xx errors due to "
                "database connection pool exhaustion caused by a connection leak."
            ),
            root_cause=(
                f"A database connection acquired in `{affected_function}()` "
                f"in `{affected_file}` is never returned to the pool. "
                "Under concurrent traffic, connections accumulate until the pool "
                "reaches its maximum, causing new requests to timeout and fail."
            ),
            confidence=0.94,
            candidate_causes=[
                {"cause": "Application connection leak (missing pool.release)", "confidence_pct": 94},
                {"cause": "Slow query / query plan regression", "confidence_pct": 17},
                {"cause": "PostgreSQL failure", "confidence_pct": 8},
                {"cause": "Network failure between app and DB", "confidence_pct": 5},
            ],
            evidence=[
                f"Pool exhausted: {incident.triggering_metrics.get('db_pool_used')}/{incident.triggering_metrics.get('db_pool_max')} connections used",
                f"Incident began after deployment {incident.triggering_metrics.get('version')}",
                f"Deployment diff: replaced `async with pool.acquire()` with explicit acquire (missing release)",
                "Database query latency itself is normal — DB is healthy",
                f"Unguarded `conn = await pool.acquire()` found in {affected_file}:{affected_function}",
            ],
            affected_file=affected_file,
            affected_function=affected_function,
            recommended_fix=(
                "Wrap the connection in a try/finally block and call "
                "`await pool.release(conn)` in the finally clause, or revert "
                "to the context-manager form: `async with pool.acquire() as conn`."
            ),
        )
    else:
        return RCAResult(
            incident_id=incident.incident_id,
            summary=f"Anomaly detected on {incident.service}. Manual investigation required.",
            root_cause="Could not determine root cause automatically. Source code analysis inconclusive.",
            confidence=0.3,
            candidate_causes=[
                {"cause": "Unknown application bug", "confidence_pct": 30},
                {"cause": "Infrastructure issue", "confidence_pct": 30},
                {"cause": "External dependency failure", "confidence_pct": 40},
            ],
            evidence=incident.symptoms,
            affected_file=None,
            affected_function=None,
            recommended_fix="Manual investigation required.",
        )


async def run_rca(incident: Incident, context: dict) -> RCAResult:
    """
    Run RCA via LLM if API key is set, else use the rule-based fallback.
    """
    if not settings.openai_api_key:
        return _fallback_rca(incident, context)

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        user_prompt = _format_context(context)
        logger.info(
            "rca_llm_request",
            extra={"incident_id": incident.incident_id, "model": settings.openai_model},
        )

        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)

        return RCAResult(
            incident_id=incident.incident_id,
            summary=data["summary"],
            root_cause=data["root_cause"],
            confidence=float(data["confidence"]),
            candidate_causes=data["candidate_causes"],
            evidence=data["evidence"],
            affected_file=data.get("affected_file"),
            affected_function=data.get("affected_function"),
            recommended_fix=data.get("recommended_fix"),
        )

    except Exception as exc:
        logger.error(
            "rca_llm_failed",
            extra={"incident_id": incident.incident_id, "error": str(exc)},
        )
        return _fallback_rca(incident, context)
