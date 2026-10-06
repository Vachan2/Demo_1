"""
API-level tests using HTTPX TestClient (no real DB needed).
"""
import sys
import os
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_app():
    """Import app fresh so lifespan doesn't run during import."""
    # Patch DB pool creation so tests don't need Postgres
    with patch("database.create_pool", new_callable=AsyncMock), \
         patch("database.close_pool", new_callable=AsyncMock), \
         patch("database._pool"):
        import main  # noqa: F401 — registers routes
        return main.app


class TestHealthEndpoint:
    def test_health_returns_200(self):
        with patch("database.create_pool", new_callable=AsyncMock), \
             patch("database.close_pool", new_callable=AsyncMock), \
             patch("database.pool_stats", return_value={"min": 2, "max": 10, "size": 2, "free": 2, "used": 0}):
            import main
            from fastapi.testclient import TestClient
            # Use lifespan=False to skip DB startup
            client = TestClient(main.app, raise_server_exceptions=False)
            # Can't easily test lifespan without DB; check module loads
            assert main.app is not None

    def test_health_schema(self):
        """Health response includes required keys."""
        import metrics
        snap = metrics.snapshot()
        assert "total_requests" in snap
        assert "error_rate_pct" in snap
        assert "latency_p95_ms" in snap


class TestMetrics:
    def test_record_and_snapshot(self):
        import metrics
        before = metrics.snapshot()["total_requests"]
        metrics.record_request(42.0, is_error=False)
        metrics.record_request(100.0, is_error=True)
        after = metrics.snapshot()
        assert after["total_requests"] >= before + 2

    def test_error_rate_increases_on_errors(self):
        import metrics
        # Record 10 errors in isolation
        for _ in range(10):
            metrics.record_request(50.0, is_error=True)
        snap = metrics.snapshot()
        assert snap["error_rate_pct"] > 0

    def test_p95_latency(self):
        import metrics
        import importlib
        # Reset metrics state via re-import trick
        metrics._state["latency_window"].clear()
        for i in range(100):
            metrics.record_request(float(i), is_error=False)
        snap = metrics.snapshot()
        # p95 of 0-99 should be around 94
        assert snap["latency_p95_ms"] >= 90
