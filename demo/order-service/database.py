"""
Database pool management.
Exposes acquire/release helpers used by the service layer.
"""
import asyncpg
from config import settings

_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url.split("?")[0],
        ssl="require",
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        command_timeout=5,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    return _pool


def pool_stats() -> dict:
    """Return current pool utilisation metrics."""
    if _pool is None:
        return {"min": 0, "max": 0, "size": 0, "free": 0, "used": 0}
    return {
        "min": _pool.get_min_size(),
        "max": _pool.get_max_size(),
        "size": _pool.get_size(),
        "free": _pool.get_idle_size(),
        "used": _pool.get_size() - _pool.get_idle_size(),
    }
