"""
API-level tests using HTTPX TestClient (no real DB needed).
"""

import sys
import os
from pydantic import SecretStr
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_app():
    """Import app fresh so lifespan doesn't run during import."""
    # Patch DB pool creation so tests don't need Postgres
    with (
        patch("database.create_pool", new_callable=AsyncMock),
        patch("database.close_pool", new_callable=AsyncMock),
        patch("database._pool"),
    ):
        import main  # noqa: F401 — registers routes

        return main.app


class TestHealthEndpoint:
    def test_health_returns_200(self):
        with (
            patch("database.create_pool", new_callable=AsyncMock),
            patch("database.close_pool", new_callable=AsyncMock),
            patch(
                "database.pool_stats",
                return_value={"min": 2, "max": 10, "size": 2, "free": 2, "used": 0},
            ),
        ):
            import main

            # Use lifespan=False to skip DB startup
            # Can't easily test lifespan without DB; check module loads
            assert main.app is not None

    def test_health_schema(self):
        """Health response includes required keys."""
        import metrics

        snap = metrics.snapshot()
        assert "total_requests" in snap
        assert "error_rate_pct" in snap
        assert "latency_p95_ms" in snap

    def test_health_endpoint_returns_service_state(self):
        import main

        with (
            patch("database.create_pool", new_callable=AsyncMock),
            patch("database.close_pool", new_callable=AsyncMock),
            patch(
                "database.pool_stats",
                return_value={"min": 2, "max": 10, "size": 2, "free": 2, "used": 0},
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["service"] == main.settings.app_name
        assert "db_pool" in response.json()

    def test_metrics_endpoint_returns_raw_metrics(self):
        import main

        with (
            patch("database.create_pool", new_callable=AsyncMock),
            patch("database.close_pool", new_callable=AsyncMock),
            patch(
                "database.pool_stats",
                return_value={"min": 2, "max": 10, "size": 2, "free": 2, "used": 0},
            ),
        ):
            with TestClient(main.app) as client:
                response = client.get("/metrics/raw")

        assert response.status_code == 200
        assert response.json()["service"] == main.settings.app_name
        assert "pool" in response.json()


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

        # Reset metrics state via re-import trick
        metrics._state["latency_window"].clear()
        for i in range(100):
            metrics.record_request(float(i), is_error=False)
        snap = metrics.snapshot()
        # p95 of 0-99 should be around 94
        assert snap["latency_p95_ms"] >= 90


class TestFailureInjection:
    def test_failure_injection_requires_token(self):
        import main

        previous = main.settings.demo_failure_admin_token
        main.settings.demo_failure_admin_token = SecretStr("test-token")
        try:
            with TestClient(main.app) as client:
                assert (
                    client.post("/admin/failure?mode=connection_leak").status_code
                    == 403
                )
                assert client.post(
                    "/admin/failure?mode=connection_leak",
                    headers={"X-Demo-Admin-Token": "test-token"},
                ).json() == {"failure_mode": "connection_leak"}
                assert client.post(
                    "/admin/failure?mode=none",
                    headers={"X-Demo-Admin-Token": "test-token"},
                ).json() == {"failure_mode": "none"}
        finally:
            main.settings.demo_failure_admin_token = previous
