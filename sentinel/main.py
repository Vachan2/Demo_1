"""
sentinel/main.py

Sentinel API — the autonomous SRE orchestrator.

Endpoints:
  POST /api/v1/metrics           — receive metrics from order-api
  POST /api/v1/incidents         — manually open an incident
  GET  /api/v1/incidents         — list all incidents
  GET  /api/v1/incidents/{id}    — get a single incident
  POST /api/v1/incidents/{id}/investigate  — trigger RCA pipeline
  POST /api/v1/incidents/{id}/remediate    — generate & PR the fix
  POST /api/v1/incidents/{id}/validate     — run test suite on patch
  POST /api/v1/incidents/{id}/verify       — run post-fix load test
  POST /api/v1/incidents/{id}/run          — run full pipeline end-to-end
  GET  /api/v1/status            — overall system status

The pipeline runs automatically when metrics are pushed:
  receive metrics → detect anomaly → investigate → remediate → validate → verify
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pythonjsonlogger import jsonlogger

import incident_store
from config import settings
from detector.monitor import evaluate
from models import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    MetricsPayload,
)

# JSON structured logging
handler = logging.StreamHandler()
handler.setFormatter(
    jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("sentinel")


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

async def _pipeline(incident: Incident) -> None:
    """
    Full autonomous pipeline: investigate → remediate → validate → verify.
    Runs in the background after an incident is opened.
    """
    from investigator.context_builder import build
    from investigator.agent import run_rca
    from github_tool.remediation import create_fix
    from validator.runner import run_all
    from verifier.engine import verify

    inc_id = incident.incident_id
    logger.info("pipeline_start", extra={"incident_id": inc_id})

    try:
        # Stage 1 — Investigate
        incident.transition(IncidentStatus.INVESTIGATING)
        incident_store.save(incident)

        context = build(incident)
        rca = await run_rca(incident, context)

        incident.rca = rca.root_cause
        incident.rca_confidence = rca.confidence
        incident.candidate_causes = rca.candidate_causes
        incident.transition(IncidentStatus.ROOT_CAUSE_IDENTIFIED)
        incident_store.save(incident)
        logger.info("rca_complete", extra={
            "incident_id": inc_id,
            "confidence": rca.confidence,
            "affected_file": rca.affected_file,
        })

        # Stage 2 — Remediate
        pr = await create_fix(incident, rca)
        incident.fix_branch = pr.branch
        incident.fix_pr_url = pr.pr_url
        incident.fix_pr_number = pr.pr_number
        incident.transition(IncidentStatus.REMEDIATION_CREATED)
        incident_store.save(incident)
        logger.info("remediation_complete", extra={"incident_id": inc_id, "pr": pr.pr_url})

        # Stage 3 — Validate
        validation = await run_all(incident)
        incident.validation_result = validation.model_dump()
        if validation.passed:
            incident.transition(IncidentStatus.PATCH_VALIDATED)
        incident_store.save(incident)
        logger.info("validation_complete", extra={
            "incident_id": inc_id,
            "passed": validation.passed,
        })

        if not validation.passed:
            logger.error("validation_failed_stopping_pipeline", extra={"incident_id": inc_id})
            incident.transition(IncidentStatus.FAILED)
            incident_store.save(incident)
            return

        # Stage 4 — Verify
        incident.transition(IncidentStatus.DEPLOYED)
        incident_store.save(incident)

        verification = await verify(incident)
        logger.info("verification_complete", extra={
            "incident_id": inc_id,
            "resolved": verification["resolved"],
        })

    except Exception as exc:
        logger.error("pipeline_error", extra={"incident_id": inc_id, "error": str(exc)})
        incident.transition(IncidentStatus.FAILED)
        incident_store.save(incident)
        raise


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sentinel_startup", extra={"version": "1.0.0"})
    yield
    logger.info("sentinel_shutdown")


app = FastAPI(title="Sentinel — Autonomous SRE", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/metrics", status_code=202)
async def receive_metrics(payload: MetricsPayload, background_tasks: BackgroundTasks):
    """
    Called by the order-api every 2 seconds.
    If an anomaly is detected, opens an incident and kicks off the pipeline.
    """
    incident = evaluate(payload)
    if incident:
        background_tasks.add_task(_pipeline, incident)
        return {
            "status": "incident_opened",
            "incident_id": incident.incident_id,
            "symptoms": incident.symptoms,
        }
    return {"status": "ok"}


@app.post("/api/v1/incidents", status_code=201)
async def open_incident(
    service: str,
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    symptoms: list[str] | None = None,
    background_tasks: BackgroundTasks = None,
):
    """Manually open an incident (for demo / testing)."""
    inc_id = incident_store.next_id()
    incident = Incident(
        incident_id=inc_id,
        service=service,
        severity=severity,
        symptoms=symptoms or ["Manually triggered"],
        triggering_metrics={},
    )
    incident_store.save(incident)
    if background_tasks:
        background_tasks.add_task(_pipeline, incident)
    return incident


@app.get("/api/v1/incidents")
async def list_incidents():
    return {"incidents": [i.model_dump() for i in incident_store.all_incidents()]}


@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(incident_id: str):
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc.model_dump()


@app.post("/api/v1/incidents/{incident_id}/investigate")
async def investigate(incident_id: str, background_tasks: BackgroundTasks):
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    from investigator.context_builder import build
    from investigator.agent import run_rca

    async def _run():
        inc.transition(IncidentStatus.INVESTIGATING)
        incident_store.save(inc)
        context = build(inc)
        rca = await run_rca(inc, context)
        inc.rca = rca.root_cause
        inc.rca_confidence = rca.confidence
        inc.candidate_causes = rca.candidate_causes
        inc.transition(IncidentStatus.ROOT_CAUSE_IDENTIFIED)
        incident_store.save(inc)

    background_tasks.add_task(_run)
    return {"status": "investigating", "incident_id": incident_id}


@app.post("/api/v1/incidents/{incident_id}/remediate")
async def remediate(incident_id: str, background_tasks: BackgroundTasks):
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    from investigator.context_builder import build
    from investigator.agent import run_rca
    from github_tool.remediation import create_fix

    async def _run():
        context = build(inc)
        rca = await run_rca(inc, context)
        pr = await create_fix(inc, rca)
        inc.fix_branch = pr.branch
        inc.fix_pr_url = pr.pr_url
        inc.fix_pr_number = pr.pr_number
        inc.transition(IncidentStatus.REMEDIATION_CREATED)
        incident_store.save(inc)

    background_tasks.add_task(_run)
    return {"status": "remediating", "incident_id": incident_id}


@app.post("/api/v1/incidents/{incident_id}/validate")
async def validate(incident_id: str, background_tasks: BackgroundTasks):
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    from validator.runner import run_all

    async def _run():
        result = await run_all(inc)
        inc.validation_result = result.model_dump()
        if result.passed:
            inc.transition(IncidentStatus.PATCH_VALIDATED)
        incident_store.save(inc)

    background_tasks.add_task(_run)
    return {"status": "validating", "incident_id": incident_id}


@app.post("/api/v1/incidents/{incident_id}/verify")
async def verify_incident(incident_id: str, background_tasks: BackgroundTasks):
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    from verifier.engine import verify

    background_tasks.add_task(verify, inc)
    return {"status": "verifying", "incident_id": incident_id}


@app.post("/api/v1/incidents/{incident_id}/run")
async def run_full_pipeline(incident_id: str, background_tasks: BackgroundTasks):
    """Re-run the full pipeline on an existing incident."""
    inc = incident_store.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    background_tasks.add_task(_pipeline, inc)
    return {"status": "pipeline_started", "incident_id": incident_id}


@app.get("/api/v1/status")
async def status():
    incidents = incident_store.all_incidents()
    open_count = sum(
        1 for i in incidents
        if i.status not in (IncidentStatus.RESOLVED, IncidentStatus.FAILED, IncidentStatus.VERIFICATION_PASSED)
    )
    return {
        "sentinel": "operational",
        "total_incidents": len(incidents),
        "open_incidents": open_count,
        "latest": incident_store.latest().model_dump() if incident_store.latest() else None,
        "thresholds": {
            "error_rate_pct": settings.error_rate_threshold_pct,
            "latency_p95_ms": settings.latency_p95_threshold_ms,
            "db_pool_utilisation_pct": settings.db_pool_utilisation_threshold_pct,
        },
    }
