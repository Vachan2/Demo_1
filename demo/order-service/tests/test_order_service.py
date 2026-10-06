"""
Unit and integration tests for order_service.py.

Key tests:
  - healthy path returns order data
  - healthy path releases connections after each call
  - buggy path leaks connections (regression guard)
"""
import sys
import os
import pytest
import asyncpg
from unittest.mock import AsyncMock, MagicMock, patch

# Allow importing sibling modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Unit tests (no real DB required — pool is mocked)
# ---------------------------------------------------------------------------

class TestHealthyImplementation:
    """order_service.get_order_healthy always releases the connection."""

    @pytest.mark.asyncio
    async def test_returns_order_dict(self):
        mock_row = {"id": "123", "customer_name": "Alice", "item": "Laptop",
                    "amount": 1299.99, "status": "shipped", "created_at": None}

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)

        mock_pool = MagicMock()
        # Simulate `async with pool.acquire() as conn`
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("order_service.get_pool", return_value=mock_pool):
            from order_service import get_order_healthy
            result = await get_order_healthy("123")

        assert result["id"] == "123"
        assert result["customer_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_order(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("order_service.get_pool", return_value=mock_pool):
            from order_service import get_order_healthy
            result = await get_order_healthy("does-not-exist")

        assert result is None


class TestConnectionRelease:
    """
    Critical regression: the healthy implementation must release its connection.
    We verify this by tracking acquire/release call counts on a real pool.
    """

    @pytest.mark.asyncio
    async def test_healthy_releases_connection(self, pg_pool):
        """Connection count before == connection count after for healthy path."""
        import database as db_module
        db_module._pool = pg_pool

        used_before = pg_pool.get_size() - pg_pool.get_idle_size()

        from order_service import get_order_healthy
        await get_order_healthy("t001")

        used_after = pg_pool.get_size() - pg_pool.get_idle_size()
        assert used_after == used_before, (
            f"Connection leak detected: used_before={used_before}, used_after={used_after}"
        )

    @pytest.mark.asyncio
    async def test_buggy_leaks_connection(self, pg_pool):
        """
        The buggy implementation does NOT release — used connections grow.
        This is a deliberate regression guard: if this test ever PASSES,
        the buggy implementation has been accidentally fixed and the demo
        will no longer demonstrate the failure.
        """
        import database as db_module
        db_module._pool = pg_pool

        # Drain any idle slots to make counting reliable
        borrowed = []
        while pg_pool.get_idle_size() > 0:
            try:
                c = await pg_pool.acquire()
                borrowed.append(c)
            except Exception:
                break

        used_before = pg_pool.get_size() - pg_pool.get_idle_size()

        from order_service import get_order_buggy
        # Patch failure mode so the buggy function is called directly
        await get_order_buggy("t001")

        used_after = pg_pool.get_size() - pg_pool.get_idle_size()

        # Release the borrowed connections + cleanup the leaked one
        for c in borrowed:
            await pg_pool.release(c)
        # The leaked connection from buggy path
        if used_after > used_before:
            pass  # pool will reclaim on close

        assert used_after > used_before, (
            "Expected connection leak but none detected — "
            "buggy path may have been accidentally fixed"
        )


# ---------------------------------------------------------------------------
# Integration tests — require real Postgres
# ---------------------------------------------------------------------------

class TestOrderServiceIntegration:

    @pytest.mark.asyncio
    async def test_known_order_returns_data(self, pg_pool):
        import database as db_module
        db_module._pool = pg_pool

        from order_service import get_order_healthy
        order = await get_order_healthy("t001")
        assert order is not None
        assert order["id"] == "t001"
        assert order["item"] == "Widget"

    @pytest.mark.asyncio
    async def test_list_recent_orders(self, pg_pool):
        import database as db_module
        db_module._pool = pg_pool

        from order_service import list_recent_orders
        orders = await list_recent_orders(limit=5)
        assert isinstance(orders, list)
        assert len(orders) >= 2

    @pytest.mark.asyncio
    async def test_unknown_order_returns_none(self, pg_pool):
        import database as db_module
        db_module._pool = pg_pool

        from order_service import get_order_healthy
        result = await get_order_healthy("xxxxxxxx")
        assert result is None
