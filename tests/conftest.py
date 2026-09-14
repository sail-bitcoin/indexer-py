import os

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

import db
import dlq

# ------------
# DB
# ------------
# support Podman's socket
if "DOCKER_HOST" not in os.environ:
    for _candidate in (f"/run/user/{os.getuid()}/podman/podman.sock", "/run/podman/podman.sock"):
        if os.path.exists(_candidate):
            os.environ["DOCKER_HOST"] = f"unix://{_candidate}"
            os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
            break


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:15", driver="asyncpg") as pg:
        yield pg


@pytest.fixture
def db_url(postgres_container, monkeypatch):
    url = postgres_container.get_connection_url()
    monkeypatch.setenv("DB_URL", url)
    return url


@pytest.fixture
async def engine(db_url):
    e = await db.set_up_db()
    yield e
    async with e.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS blocks, transactions, inputs, outputs, coinbaseinputs CASCADE"))
    await e.dispose()


# ------------
# DLQ
# ------------
@pytest.fixture
def clear_dlq():
    dlq.queue.clear()
    yield
    dlq.queue.clear()
