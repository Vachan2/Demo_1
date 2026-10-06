"""
detector/monitor.py

Receives metric payloads from the order-api and decides whether to open
an incident.  Thresholds come from Sentinel's config.

Detection rules (any one triggers HIGH severity):
  1. error_rate_pct   > error_rate_threshold_pct
  2. latency_p95_ms   > latency_p95_threshold_ms
  3. db pool used/max > db_pool_utilisation_threshold_pct

A new incident is only opened if there is no currently open (non-resolved)
incident for the same service.
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from models import Incident, IncidentSeverity, MetricsPayload
import incident_store

logger = logging.getLogger("sentinel.detector")


def _pool_utilisation_pct(pool: dict) -> float:
    max_size = pool.get("max", 0)
    used = pool.get("used", 0)
    if max_size == 0:
        return 0.0
    return (used / max_size) * 100.0


def _detect_symptoms(payload: MetricsPayload) -> list[str]:
    symptoms = []
    m = payload.metrics
    p = payload.pool

    error_rate = m.get("error_rate_pct", 0)
    if error_rate > settings.error_rate_threshold_pct:
        symptoms.append(
            f"HTTP 5xx spike: error rate {error_rate:.1f}% "
            f"(threshold {settings.error_rate_threshold_pct}%)"
        )

    p95 = m.get("latency_p95_ms", 0)
    if p95 > settings.latency_p95_threshold_ms:
        symptoms.append(
            f"High P95 latency: {p95:.0f}ms "
            f"(threshold {settings.latency_p95_threshold_ms:.0f}ms)"
        )

    pool_pct = _pool_utilisation_pct(p)
    if pool_pct >= settings.db_pool_utilisation_threshold_pct:
        symptoms.append(
            f"Database connection pool exhaustion: "
            f"{p.get('used', 0)}/{p.get('max', 0)} connections used "
            f"({pool_pct:.0f}%)"
        )

    return symptoms


def _has_open_incident(service: str) -> bool:
    from models import IncidentStatus
    closed = {IncidentStatus.RESOLVED, IncidentStatus.FAILED, IncidentStatus.VERIFICATION_PASSED}
    for inc in incident_store.all_incidents():
        if inc.service == service and inc.status not in closed:
            return True
    return False


def evaluate(payload: MetricsPayload) -> Incident | None:
    """
    Evaluate a metrics payload.
    Returns a new Incident if one should be opened, else None.
    """
    symptoms = _detect_symptoms(payload)
    if not symptoms:
        return None

    if _has_open_incident(payload.service):
        return None  # already tracking this service

    inc_id = incident_store.next_id()
    incident = Incident(
        incident_id=inc_id,
        service=payload.service,
        severity=IncidentSeverity.HIGH,
        symptoms=symptoms,
        triggering_metrics={
            "version": payload.version,
            "error_rate_pct": payload.metrics.get("error_rate_pct"),
            "latency_p95_ms": payload.metrics.get("latency_p95_ms"),
            "db_pool_used": payload.pool.get("used"),
            "db_pool_max": payload.pool.get("max"),
            "failure_mode": payload.failure_mode,
        },
    )

    incident_store.save(incident)

    logger.warning(
        "incident_opened",
        extra={
            "incident_id": inc_id,
            "service": payload.service,
            "severity": incident.severity,
            "symptoms": symptoms,
        },
    )
    return incident
