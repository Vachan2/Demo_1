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
    """Periodically POST metrics to Sentinel so it can detect anomalies."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            await asyncio.sleep(settings.metrics_push_interval)
            try:
                payload = {
                    "service": settings.app_name,
                    "version": settings.app_version,
                    "metrics": snapshot(),
                    "pool": pool_stats(),
                    "failure_mode": settings.demo_failure_mode,
                }
                await client.post(
                    f"{settings.sentinel_url}/api/v1/metrics",
                    json=payload,
                )
            except Exception:
                pass  # Sentinel may not be running yet; don't crash the API


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
