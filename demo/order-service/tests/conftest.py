"""
Test fixtures — spins up a real asyncpg pool against a test DB,
or patches the pool for unit tests that don't need Postgres.
"""
import asyncio
import os
import pytest
import asyncpg

# Point at a test database; CI can override via env
TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://orders:orders@localhost:5432/orders_test",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def pg_pool():
    pool = await asyncpg.create_pool(dsn=TEST_DSN, min_size=2, max_size=10)
    # Bootstrap schema
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id            TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                item          TEXT NOT NULL,
                amount        NUMERIC(10,2) NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO orders (id, customer_name, item, amount, status) VALUES
              ('t001', 'Tester A', 'Widget', 9.99, 'pending'),
              ('t002', 'Tester B', 'Gadget', 19.99, 'shipped')
            ON CONFLICT (id) DO NOTHING
            """
        )
    yield pool
    await pool.close()
