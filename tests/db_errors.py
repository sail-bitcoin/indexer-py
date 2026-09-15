"""Error management integration tests: real Postgres, real connection failures.

They check outcomes (the block is inserted after a retry, or queued in the DLQ without crashing),
not exception classes: the classes raised depend on the driver and insert method (SQLAlchemy execute
wraps asyncpg errors, raw asyncpg COPY does not), so these tests fail if a change stops handling them.
"""

import asyncio
import copy

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

import db
import dlq
import tests.variables as var
from tests.utils import fast_retries

pytestmark = pytest.mark.integration

H = var.block_b["height"]


@pytest.fixture(autouse=True)
def no_retry_wait():
    with fast_retries(db._insert_prepared, db.INSERTION_RETRIES):
        yield


def attempts() -> int:
    """Attempts made by the last _insert_prepared call."""
    return db._insert_prepared.statistics["attempt_number"]


async def count_rows(engine, table) -> int:
    async with engine.connect() as conn:
        return await conn.scalar(select(func.count()).select_from(table))


async def kill_all_connections(db_url: str):
    """Like a Postgres restart: terminate every client connection, including idle ones in the engine pool."""
    url = make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)
    pg = await asyncpg.connect(url)
    try:
        await pg.execute("""
            SELECT pg_terminate_backend(pid) FROM pg_stat_activity
            WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
        """)
    finally:
        await pg.close()
    await asyncio.sleep(0.2)  # let the pooled connections notice their socket was closed


async def kill_connection_on_insert(engine, table: str, times: int):
    """Postgres kills the connection inserting into `table` (mid-transaction), for the first `times` inserts."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SEQUENCE IF EXISTS kill_counter"))
        await conn.execute(text("CREATE SEQUENCE kill_counter"))
        await conn.execute(text(f"""
            CREATE OR REPLACE FUNCTION kill_own_connection() RETURNS trigger AS $$
            BEGIN
                IF nextval('kill_counter') <= {times} THEN
                    PERFORM pg_terminate_backend(pg_backend_pid());
                    PERFORM pg_sleep(10);  -- the termination is processed here
                END IF;
                RETURN NULL;
            END $$ LANGUAGE plpgsql
        """))
        await conn.execute(text(f"""
            CREATE TRIGGER kill_on_insert BEFORE INSERT ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION kill_own_connection()
        """))


# --------------------
# transient errors: retried, then inserted
# --------------------
async def test_insert_block_recovers_from_connections_killed_while_idle_in_pool(engine, db_url):
    await db.insert_block(copy.deepcopy(var.block_a), engine)  # leaves an idle connection in the pool
    await kill_all_connections(db_url)

    await db.insert_block(copy.deepcopy(var.block_b), engine)

    assert attempts() == 2
    assert await count_rows(engine, db.Blocks) == 2


async def test_insert_block_recovers_from_connection_killed_mid_transaction(engine):
    await kill_connection_on_insert(engine, "transactions", times=1)

    await db.insert_block(copy.deepcopy(var.block_b), engine)

    assert attempts() == 2
    # the killed attempt was rolled back: nothing duplicated (and the retry didn't hit a duplicate key)
    assert await count_rows(engine, db.Blocks) == 1
    assert await count_rows(engine, db.Transactions) == len(var.block_b["tx"])


# --------------------
# errors after retries or non transient: queued in the DLQ, not crashing
# --------------------
async def test_block_goes_to_dlq_when_connection_keeps_being_killed(engine, clear_dlq):
    await kill_connection_on_insert(engine, "transactions", times=999)

    with dlq.add_to_deadletterqueue(H):
        await db.insert_block(copy.deepcopy(var.block_b), engine)

    assert dlq.queue == [H]
    assert attempts() == db.INSERTION_RETRIES
    assert await count_rows(engine, db.Blocks) == 0


async def test_block_goes_to_dlq_when_postgres_is_unreachable(db_url, clear_dlq):
    url = make_url(db_url).set(port=1).render_as_string(hide_password=False)
    e = db.create_db_engine(url)

    try:
        with dlq.add_to_deadletterqueue(H):
            await db.insert_block(copy.deepcopy(var.block_b), e)
    finally:
        await e.dispose()

    assert dlq.queue == [H]
    assert attempts() == db.INSERTION_RETRIES


async def test_block_goes_to_dlq_without_retry_on_duplicate_block(engine, clear_dlq):
    await db.insert_block(copy.deepcopy(var.block_b), engine)

    with dlq.add_to_deadletterqueue(H):
        await db.insert_block(copy.deepcopy(var.block_b), engine)

    assert dlq.queue == [H]
    assert attempts() == 1
