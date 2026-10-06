"""
investigator/context_builder.py

Aggregates all available signals for an incident into a structured context
dict that the agent can reason over:

  - triggering metrics snapshot
  - deployment history (simulated; would be real CI/CD metadata in prod)
  - recent log lines (pulled from order-api's structured log stream)
  - source files of interest from the local repo
"""
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Incident
from config import settings

logger = logging.getLogger("sentinel.context_builder")

# Files the agent should always read for this service
_SOURCE_FILES_OF_INTEREST = [
    "order_service.py",
    "database.py",
    "main.py",
    "config.py",
]

# Simulated deployment history (in a real system, fetched from CI/CD)
_DEPLOYMENT_HISTORY = [
    {
        "version": "1.0.0",
        "deployed_at": "2026-10-05T09:00:00Z",
        "commit": "a1b2c3d",
        "commit_message": "Initial release",
        "deployed_by": "ci-bot",
    },
    {
        "version": "1.1.0",
        "deployed_at": "2026-10-06T14:22:00Z",
        "commit": "f7e8d9c",
        "commit_message": 'release v1.1.0 — "optimize order lookup"',
        "deployed_by": "ci-bot",
        "diff_summary": (
            "order_service.py: replaced `async with pool.acquire()` with "
            "explicit `conn = await pool.acquire()` to reduce context-manager overhead."
        ),
    },
]


def _read_source_file(filename: str) -> dict:
    repo_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", settings.local_repo_path)
    )
    filepath = os.path.join(repo_path, filename)
    try:
        with open(filepath, "r") as fh:
            content = fh.read()
        return {"file": filename, "content": content, "error": None}
    except FileNotFoundError:
        return {"file": filename, "content": None, "error": "file not found"}
    except Exception as exc:
        return {"file": filename, "content": None, "error": str(exc)}


def build(incident: Incident) -> dict:
    """
    Build and return a full context dict for the given incident.
    """
    logger.info("building_context", extra={"incident_id": incident.incident_id})

    source_files = [_read_source_file(f) for f in _SOURCE_FILES_OF_INTEREST]

    context = {
        "incident": {
            "id": incident.incident_id,
            "service": incident.service,
            "severity": incident.severity,
            "symptoms": incident.symptoms,
            "triggering_metrics": incident.triggering_metrics,
            "created_at": datetime.utcfromtimestamp(incident.created_at).isoformat() + "Z",
        },
        "deployments": _DEPLOYMENT_HISTORY,
        "latest_deployment": _DEPLOYMENT_HISTORY[-1],
        "source_files": source_files,
        "analysis_hints": [
            "Compare the latest deployment diff against the symptoms.",
            "Check whether database connections are acquired without a corresponding release.",
            "Consider whether the database itself is the problem or the application layer.",
            "Look for missing `finally` blocks or missing context-manager usage around pool.acquire().",
        ],
    }

    return context
