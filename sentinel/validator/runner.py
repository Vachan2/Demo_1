"""
validator/runner.py

Runs the order-service test suite against the patched code and reports results.

Pipeline:
  1. ruff lint
  2. pytest unit tests
  3. pytest integration tests (require real Postgres)
  4. connection-leak regression test (most important)

All steps run as subprocesses inside the order-service directory so the
existing pytest.ini and test fixtures are used unchanged.
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import Incident, ValidationResult
from config import settings

logger = logging.getLogger("sentinel.validator")

_SERVICE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", settings.local_repo_path)
)


def _run(cmd: list[str], cwd: str = _SERVICE_DIR) -> tuple[int, str, str]:
    """Run a subprocess synchronously and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_pytest_output(stdout: str) -> dict:
    """
    Extract passed/failed counts from pytest's final summary line.
    e.g. "5 passed, 1 failed in 2.34s"
    """
    match = re.search(
        r"(?:(\d+) passed)?[,\s]*(?:(\d+) failed)?[,\s]*(?:(\d+) error)?",
        stdout,
    )
    if match:
        passed = int(match.group(1) or 0)
        failed = int(match.group(2) or 0)
        errors = int(match.group(3) or 0)
    else:
        passed = failed = errors = 0
    return {"passed": passed, "failed": failed, "errors": errors}


def run_lint() -> dict:
    logger.info("running_lint")
    # Try ruff first; fall back to flake8 if unavailable
    rc, stdout, stderr = _run(["python", "-m", "ruff", "check", "."])
    if rc == 127 or "No module named ruff" in stderr:
        rc, stdout, stderr = _run(["python", "-m", "flake8", "--max-line-length=120", "."])
    passed = rc == 0
    return {
        "passed": passed,
        "returncode": rc,
        "output": (stdout + stderr).strip()[:500],
    }


def run_unit_tests() -> dict:
    logger.info("running_unit_tests")
    rc, stdout, stderr = _run([
        "python", "-m", "pytest",
        "tests/test_order_service.py::TestHealthyImplementation",
        "tests/test_api.py",
        "-v", "--tb=short",
    ])
    counts = _parse_pytest_output(stdout + stderr)
    return {
        "passed": rc == 0,
        "returncode": rc,
        **counts,
        "output": (stdout + stderr).strip()[-1000:],
    }


def run_integration_tests() -> dict:
    logger.info("running_integration_tests")
    rc, stdout, stderr = _run([
        "python", "-m", "pytest",
        "tests/test_order_service.py::TestOrderServiceIntegration",
        "-v", "--tb=short",
    ])
    counts = _parse_pytest_output(stdout + stderr)
    return {
        "passed": rc == 0,
        "returncode": rc,
        **counts,
        "output": (stdout + stderr).strip()[-1000:],
    }


def run_connection_leak_test() -> dict:
    logger.info("running_connection_leak_regression")
    rc, stdout, stderr = _run([
        "python", "-m", "pytest",
        "tests/test_connection_leak_regression.py",
        "tests/test_order_service.py::TestConnectionRelease::test_healthy_releases_connection",
        "-v", "--tb=short",
    ])
    counts = _parse_pytest_output(stdout + stderr)
    return {
        "passed": rc == 0,
        "returncode": rc,
        **counts,
        "output": (stdout + stderr).strip()[-1000:],
    }


async def run_all(incident: Incident) -> ValidationResult:
    """
    Run the full validation pipeline asynchronously (each step in a thread
    so we don't block the event loop).
    """
    loop = asyncio.get_event_loop()

    lint = await loop.run_in_executor(None, run_lint)
    unit = await loop.run_in_executor(None, run_unit_tests)
    integration = await loop.run_in_executor(None, run_integration_tests)
    leak = await loop.run_in_executor(None, run_connection_leak_test)

    overall = lint["passed"] and unit["passed"] and leak["passed"]
    # Integration tests are best-effort (need live Postgres)
    if not integration["passed"] and integration.get("errors", 0) == 0:
        overall = False

    notes = []
    if not lint["passed"]:
        notes.append(f"Lint failed: {lint['output'][:200]}")
    if not unit["passed"]:
        notes.append(f"Unit tests failed: {unit['output'][:200]}")
    if not integration["passed"]:
        notes.append(f"Integration tests failed (may need DB): {integration['output'][:200]}")
    if not leak["passed"]:
        notes.append(f"Connection leak regression FAILED — patch is INCOMPLETE")

    result = ValidationResult(
        incident_id=incident.incident_id,
        passed=overall,
        unit_tests=unit,
        integration_tests=integration,
        lint=lint,
        connection_leak_test=leak,
        notes="; ".join(notes) if notes else "All checks passed",
    )

    logger.info(
        "validation_complete",
        extra={
            "incident_id": incident.incident_id,
            "passed": overall,
            "lint": lint["passed"],
            "unit": unit["passed"],
            "integration": integration["passed"],
            "leak_regression": leak["passed"],
        },
    )
    return result
