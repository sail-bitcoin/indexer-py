import os

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

import db

# support Podman's socket
if "DOCKER_HOST" not in os.environ:
    for _candidate in (f"/run/user/{os.getuid()}/podman/podman.sock", "/run/podman/podman.sock"):
        if os.path.exists(_candidate):
            os.environ["DOCKER_HOST"] = f"unix://{_candidate}"
            os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
            break


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:15") as pg:
        yield pg


@pytest.fixture
def db_url(postgres_container, monkeypatch):
    url = postgres_container.get_connection_url()
    monkeypatch.setenv("DB_URL", url)
    return url


@pytest.fixture(autouse=True)
def clean_tables(request):
    needs_db = "db_url" in request.fixturenames
    url = request.getfixturevalue("db_url") if needs_db else None
    yield
    if url is None:
        return
    engine = db.create_db_engine(url)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE blocks, transactions, inputs, outputs, coinbaseinputs CASCADE"))
    engine.dispose()
