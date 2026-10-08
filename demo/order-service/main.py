"""
main.py — FastAPI application entry point.

Endpoints:
  GET  /health           — liveness + readiness + metrics snapshot
  GET  /orders/{id}      — fetch a single order
  GET  /orders           — list recent orders
  POST /admin/failure    — toggle failure mode at runtime (demo helper)
  GET  /metrics/raw      — machine-readable metrics for Sentinel
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from config import settings
from database import create_pool, close_pool, pool_stats
from metrics import record_request, snapshot, set_version
from order_service import get_order, list_recent_orders


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
handler = logging.StreamHandler()
handler.setFormatter(
    jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    )
)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("order-api")


# ---------------------------------------------------------------------------
# Background metric pusher (to Sentinel)
# ---------------------------------------------------------------------------
async def _push_metrics() -> None:
    """Report pool-exhaustion incidents to the deployed Sentinel."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        incident_reported = False
        while True:
            await asyncio.sleep(settings.metrics_push_interval)
            try:
                metrics = snapshot()
                pool = pool_stats()
                failure_mode = settings.demo_failure_mode

                pool_exhausted = (
                    pool["max"] > 0
                    and pool["free"] == 0
                    and pool["used"] >= pool["max"]
                )

                should_report = pool_exhausted and (
                    failure_mode == "connection_leak"
                    or metrics["error_rate_pct"] > 0
                )

                if not should_report:
                    incident_reported = False
                    continue

                if incident_reported:
                    continue

                from datetime import datetime, timezone

                now = datetime.now(timezone.utc).isoformat()

                correlation_id = (
                    f"demo1-{settings.app_name}-{failure_mode}-pool-exhausted"
                )

                payload = {
                    "title": f"Order API database connection pool exhausted",
                    "description": (
                        f"Database pool exhaustion detected for {settings.app_name}. "
                        f"Failure mode: {failure_mode}. "
                        f"Pool: {pool}. Metrics: {metrics}"
                    ),
                    "severity": "high",
                    "affected_services": [settings.app_name],
                    "environment": "demo",
                    "correlation_id": correlation_id,
                    "symptoms": [
                        "Database connection pool exhausted",
                        f"Failure mode: {failure_mode}",
                    ],
                    "tags": ["demo1", "database", "connection-pool"],
                    "signals": [
                        {
                            "signal_id": f"demo1-pool-{now}",
                            "signal_type": "metric_threshold",
                            "source": "other",
                            "title": "Database connection pool exhausted",
                            "description": (
                                f"Pool utilisation reached {pool['used']}/"
                                f"{pool['max']}; free connections: {pool['free']}."
                            ),
                            "severity": "high",
                            "received_at": now,
                            "service": settings.app_name,
                            "environment": "demo",
                            "raw_payload": {
                                "pool": pool,
                                "metrics": metrics,
                                "failure_mode": failure_mode,
                            },
                            "labels": {
                                "service": settings.app_name,
                                "failure_mode": failure_mode,
                            },
                        }
                    ],
                    "metadata": {
                        "source": "demo1",
                        "failure_mode": failure_mode,
                        "pool": pool,
                        "metrics": metrics,
                    },
                }

                response = await client.post(
                    f"{settings.sentinel_url.rstrip('/')}/api/v1/incidents",
                    json=payload,
                )

                response.raise_for_status()

                result = response.json()

                logger.warning(
                    "sentinel_incident_reported",
                    extra={
                        "status_code": response.status_code,
                        "incident_id": result.get("incident", {}).get("incident_id"),
                        "is_duplicate": result.get("is_duplicate"),
                    },
                )

                incident_reported = True

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("sentinel_incident_reporting_failed")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    set_version(settings.app_version)
    logger.info(
        "startup",
        extra={
            "version": settings.app_version,
            "failure_mode": settings.demo_failure_mode,
            "db_pool_max": settings.db_pool_max,
        },
    )
    pusher = asyncio.create_task(_push_metrics())
    yield
    pusher.cancel()
    await close_pool()
    logger.info("shutdown")


app = FastAPI(
    title="E-Commerce Order API",
    version=settings.app_version,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware — record latency and errors for every request
# ---------------------------------------------------------------------------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    is_error = response.status_code >= 500
    record_request(elapsed_ms, is_error)

    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": round(elapsed_ms, 1),
            "failure_mode": settings.demo_failure_mode,
        },
    )
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    stats = snapshot()
    pool = pool_stats()
    status = "HEALTHY"
    if stats["error_rate_pct"] > 10 or pool.get("used", 0) >= pool.get("max", 10):
        status = "DEGRADED"

    return {
        "status": status,
        "service": settings.app_name,
        "version": settings.app_version,
        "failure_mode": settings.demo_failure_mode,
        "metrics": stats,
        "db_pool": pool,
    }


@app.get("/metrics/raw")
async def metrics_raw():
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "metrics": snapshot(),
        "pool": pool_stats(),
        "failure_mode": settings.demo_failure_mode,
    }


@app.get("/orders/{order_id}")
async def fetch_order(order_id: str):
    try:
        order = await asyncio.wait_for(get_order(order_id), timeout=8.0)
    except asyncio.TimeoutError:
        logger.warning("order_timeout", extra={"order_id": order_id})
        raise HTTPException(status_code=503, detail="Service unavailable — DB pool exhausted")
    except Exception as exc:
        logger.error("order_error", extra={"order_id": order_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail="Internal server error")

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/orders")
async def fetch_orders(limit: int = 20):
    try:
        orders = await asyncio.wait_for(list_recent_orders(limit), timeout=8.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Service unavailable — DB pool exhausted")
    except Exception as exc:
        logger.error("list_orders_error", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"orders": orders, "count": len(orders)}


@app.post("/admin/failure")
async def set_failure_mode(mode: str = "none"):
    """
    Demo helper — toggle failure mode without restarting the process.
    Allowed values: 'none', 'connection_leak'
    """
    if mode not in ("none", "connection_leak"):
        raise HTTPException(status_code=400, detail="Unknown failure mode")
    settings.demo_failure_mode = mode
    logger.warning("failure_mode_changed", extra={"new_mode": mode})
    return {"failure_mode": mode}
