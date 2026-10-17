"""Tests for the Demo 1 → Sentinel incident reporter."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Response:
    status_code = 201

    def __init__(self, body: dict[str, Any], error: Exception | None = None) -> None:
        self._body = body
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error

    def json(self) -> dict[str, Any]:
        return self._body


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.posts: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> _Response:
        self.posts.append(json)
        self.headers.append(headers or {})
        return self.responses[min(len(self.posts) - 1, len(self.responses) - 1)]


@pytest.mark.asyncio
async def test_incident_payload_contains_demo_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    monkeypatch.setattr(main.settings, "app_name", "order-api")
    monkeypatch.setattr(main.settings, "app_version", "commit-123")
    monkeypatch.setattr(main.settings, "image_version", "image-456")
    monkeypatch.setattr(main.settings, "db_pool_min", 2)
    monkeypatch.setattr(main.settings, "db_pool_max", 10)
    payload = main._build_incident_payload(
        metrics={"total_errors": 7, "error_rate_pct": 18.0},
        pool={"min": 2, "max": 10, "size": 10, "free": 0, "used": 10},
        failure_mode="connection_leak",
        received_at="2026-01-01T00:00:00+00:00",
    )

    assert payload["correlation_id"].startswith("demo1-order-api-connection_leak-")
    assert payload["metadata"]["image_version"] == "image-456"
    assert payload["metadata"]["pool_config"] == {"min": 2, "max": 10}
    assert payload["signals"][0]["raw_payload"]["failure_mode"] == "connection_leak"
    assert payload["metadata"]["error_information"]["total_errors"] == 7


@pytest.mark.asyncio
async def test_reporting_retries_after_failed_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    client = _Client(
        [
            _Response(
                {},
                httpx.HTTPStatusError(
                    "failed",
                    request=httpx.Request("POST", "http://sentinel"),
                    response=httpx.Response(503),
                ),
            ),
            _Response({"incident": {"incident_id": "inc-1"}, "is_duplicate": False}),
        ]
    )
    sleeps = 0

    async def stop_after_two_reports(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_two_reports)
    monkeypatch.setattr(main.settings, "sentinel_url", "http://sentinel")
    monkeypatch.setattr(
        main.settings,
        "sentinel_incident_api_key",
        SecretStr("test-incident-key"),
    )
    monkeypatch.setattr(main.settings, "metrics_push_interval", 0.0)
    monkeypatch.setattr(main.settings, "demo_failure_mode", "connection_leak")
    monkeypatch.setattr(
        main, "snapshot", lambda: {"total_errors": 1, "error_rate_pct": 1.0}
    )
    monkeypatch.setattr(
        main,
        "pool_stats",
        lambda: {"min": 2, "max": 10, "size": 10, "free": 0, "used": 10},
    )

    with pytest.raises(asyncio.CancelledError):
        await main._push_metrics()

    assert len(client.posts) == 2
    assert all(
        headers == {"X-Sentinel-Incident-Key": "test-incident-key"}
        for headers in client.headers
    )
