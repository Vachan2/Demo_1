"""
order_service.py — healthy version (v1.0.0)

All connections are properly released via the context-manager form
of pool.acquire().  The buggy version (v1.1.0) is controlled by the
DEMO_FAILURE_MODE environment variable.
"""
import logging
from database import get_pool
from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Healthy implementation
# ---------------------------------------------------------------------------

async def get_order_healthy(order_id: str) -> dict | None:
    """
    Fetch an order row.  Uses 'async with pool.acquire()' so the connection
    is guaranteed to be returned to the pool on every path.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, customer_name, item, amount, status, created_at "
            "FROM orders WHERE id = $1",
            order_id,
        )
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Buggy implementation  (v1.1.0  — "optimize order lookup")
#
# The diff looks innocuous: the developer inlined pool.acquire() to avoid
# the context-manager overhead.  The missing `finally: await pool.release(conn)`
# is easy to miss in code review — especially when the happy path works fine.
# ---------------------------------------------------------------------------

async def get_order_buggy(order_id: str) -> dict | None:
    """
    Fetch an order row.

    NOTE: This version acquires a connection but never releases it.
    Under concurrent traffic the pool exhausts and new requests hang / timeout.
    """
    pool = get_pool()
    conn = await pool.acquire()          # <-- acquired
    try:
        row = await conn.fetchrow(
            "SELECT id, customer_name, item, amount, status, created_at "
            "FROM orders WHERE id = $1",
            order_id,
        )
        return dict(row) if row else None
    except Exception:
        raise
    # conn is never released — this is the defect


# ---------------------------------------------------------------------------
# Public facade — switched by DEMO_FAILURE_MODE
# ---------------------------------------------------------------------------

async def get_order(order_id: str) -> dict | None:
    if settings.demo_failure_mode == "connection_leak":
        logger.debug("failure_mode=connection_leak order_id=%s", order_id)
        return await get_order_buggy(order_id)
    return await get_order_healthy(order_id)


async def list_recent_orders(limit: int = 20) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, customer_name, item, amount, status, created_at "
            "FROM orders ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]
