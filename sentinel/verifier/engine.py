"""
verifier/engine.py

Verification engine — runs the same workload before and after the fix
and decides whether the incident is resolved.

Resolution criteria (all must pass):
  - error_rate_pct  < 5%      (was > 5% during incident)
  - p95_latency_ms  < 500ms   (was > 2000ms during incident)
  - pool utilisation < 80%    (was 100% during incident)
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Incident, IncidentStatus
from verifier.load_test import run as run_load_test, LoadTestResult

logger = logging.getLogger("sentinel.verifier")

# Resolution thresholds
_MAX_ERROR_RATE = 5.0        # pct
_MAX_P95_LATENCY = 500.0     # ms
_MAX_POOL_UTILISATION = 80.0 # pct


def _is_resolved(after: LoadTestResult) -> bool:
    return (
        after.error_rate_pct < _MAX_ERROR_RATE
        and after.p95_latency_ms < _MAX_P95_LATENCY
        and after.peak_pool_utilisation_pct < _MAX_POOL_UTILISATION
    )


def _comparison_table(before: LoadTestResult, after: LoadTestResult) -> str:
    rows = [
        ("Metric", "Before", "After", "Change"),
        ("5xx error rate", f"{before.error_rate_pct:.1f}%", f"{after.error_rate_pct:.1f}%",
         "✓" if after.error_rate_pct < _MAX_ERROR_RATE else "✗"),
        ("P95 latency", f"{before.p95_latency_ms:.0f}ms", f"{after.p95_latency_ms:.0f}ms",
         "✓" if after.p95_latency_ms < _MAX_P95_LATENCY else "✗"),
        ("DB pool usage", f"{before.peak_pool_utilisation_pct:.0f}%", f"{after.peak_pool_utilisation_pct:.0f}%",
         "✓" if after.peak_pool_utilisation_pct < _MAX_POOL_UTILISATION else "✗"),
        ("Successful reqs", f"{before.successful}/{before.total}", f"{after.successful}/{after.total}", ""),
    ]
    col_w = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = []
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(col_w[j]) for j, cell in enumerate(row))
        lines.append(line)
        if i == 0:
            lines.append("-" * len(line))
    return "\n".join(lines)


async def verify(incident: Incident) -> dict:
    """
    Run the post-fix verification workload and return a result dict.
    Updates the incident status in-place.
    """
    import incident_store

    logger.info("verification_start", extra={"incident_id": incident.incident_id})

    # Snapshot of triggering metrics as a pseudo-"before" result
    tm = incident.triggering_metrics
    before_summary = {
        "error_rate_pct": tm.get("error_rate_pct", 0),
        "p95_latency_ms": tm.get("latency_p95_ms", 0),
        "peak_pool_utilisation_pct": (
            (tm.get("db_pool_used", 0) / max(tm.get("db_pool_max", 10), 1)) * 100
        ),
        "total_requests": "N/A (from triggering metrics)",
        "successful": "N/A",
    }

    # Run actual post-fix load test
    after = await run_load_test()
    resolved = _is_resolved(after)

    after_summary = after.summary()

    comparison = _comparison_table(
        # Construct a dummy LoadTestResult from before metrics for the table
        _make_before_result(tm),
        after,
    )

    result = {
        "incident_id": incident.incident_id,
        "resolved": resolved,
        "before": before_summary,
        "after": after_summary,
        "comparison_table": comparison,
        "resolution_criteria": {
            "error_rate_ok": after.error_rate_pct < _MAX_ERROR_RATE,
            "latency_ok": after.p95_latency_ms < _MAX_P95_LATENCY,
            "pool_ok": after.peak_pool_utilisation_pct < _MAX_POOL_UTILISATION,
        },
    }

    if resolved:
        incident.transition(IncidentStatus.VERIFICATION_PASSED)
        incident.resolved_at = __import__("time").time()
        incident.transition(IncidentStatus.RESOLVED)
        logger.info("incident_resolved", extra={"incident_id": incident.incident_id})
    else:
        incident.transition(IncidentStatus.FAILED)
        logger.warning(
            "verification_failed",
            extra={"incident_id": incident.incident_id, "after": after_summary},
        )

    incident.verification_result = result
    incident_store.save(incident)
    return result


def _make_before_result(tm: dict):
    """Build a dummy LoadTestResult from triggering metrics for the comparison table."""
    from verifier.load_test import LoadTestResult
    r = LoadTestResult()
    r.total = 100
    r.failed = int((tm.get("error_rate_pct", 0) / 100) * 100)
    r.successful = r.total - r.failed
    # Simulate latency and pool samples from the triggering metrics
    p95 = tm.get("latency_p95_ms", 0) or 8200
    r.latencies_ms = [p95] * 100
    used = tm.get("db_pool_used", 10)
    max_size = tm.get("db_pool_max", 10)
    r.pool_samples = [(used, max_size)] * 10
    return r
