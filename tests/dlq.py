from json import JSONDecodeError

import asyncpg
import httpx
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, TimeoutError as SATimeoutError

import dlq
from exceptions import RpcHTTPStatusError, BitcoinRpcError

H = 999


def with_pgcode(exc: Exception, pgcode: str) -> Exception:
    """Mimic SQLAlchemy's asyncpg adapter, which copies the SQLSTATE onto the wrapped error as `pgcode`"""
    exc.pgcode = pgcode  # type: ignore[attr-defined]
    return exc


db_errors = [
    DBAPIError("INSERT ...", {}, Exception("connection is closed"), connection_invalidated=True),
    IntegrityError("INSERT ...", {}, Exception("duplicate key")),
    SATimeoutError("pool exhausted"),
    ConnectionRefusedError(),
    asyncpg.CannotConnectNowError("the database system is starting up"),
    asyncpg.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
    asyncpg.InterfaceError("connection is closed"),
]

rpc_errors = [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("timed out"),
    httpx.InvalidURL("bad url"),
    RpcHTTPStatusError(429, "Too Many Requests", "getblockhash", ["0xab"]),
    BitcoinRpcError("getblockhash", ["0xab"], -8, "invalid parameter"),
    JSONDecodeError("Expecting value", "", 0),
]

runtime_errors = [
    KeyError("coinbase_tx"),
    TypeError("error"),
    ValueError("error"),
    OverflowError("error"),
]


def ids(e):
    return type(e).__name__


@pytest.mark.parametrize("exc", db_errors + rpc_errors + runtime_errors, ids=ids)
def test__add_to_queue_on_handled_exception(exc, clear_dlq):
    with dlq.add_to_deadletterqueue(H):
        raise exc
    assert dlq.queue == [H]


def test__dlq_stays_empty_if_no_exception(clear_dlq):
    with dlq.add_to_deadletterqueue(H):
        pass
    assert dlq.queue == []


def test__unexpected_exception_propagates_and_is_not_queued(clear_dlq):
    with pytest.raises(RuntimeError):
        with dlq.add_to_deadletterqueue(H):
            raise RuntimeError("bug")
    assert dlq.queue == []


# --------------------
# db errors
# --------------------
@pytest.mark.parametrize("exc", db_errors, ids=ids)
def test__db_errors_are_logged_as_insertion_failures(exc, clear_dlq, caplog):
    with dlq.add_to_deadletterqueue(H):
        raise exc
    assert caplog.messages[-1].startswith(f"Block {H} insertion failed")


@pytest.mark.parametrize(
    ("exc", "pgcode"),
    [
        (IntegrityError("INSERT ...", {}, with_pgcode(Exception("duplicate key"), "23505")), "23505"),
        (asyncpg.CannotConnectNowError("the database system is starting up"), "57P03"),
        (ConnectionRefusedError(), None),
    ],
    ids=["wrapped_asyncpg", "raw_asyncpg", "oserror"],
)
def test__db_errors_log_the_pgcode(exc, pgcode, clear_dlq, caplog):
    with dlq.add_to_deadletterqueue(H):
        raise exc
    assert f"insertion failed [{pgcode}]" in caplog.messages[-1]


# --------------------
# rpc errors
# --------------------
@pytest.mark.parametrize("exc", rpc_errors, ids=ids)
def test__rpc_errors_are_not_logged_as_insertion_failures(exc, clear_dlq, caplog):
    with dlq.add_to_deadletterqueue(H):
        raise exc
    assert "insertion failed" not in caplog.messages[-1]


def test__rpc_errors_log_retry_attempts(clear_dlq, caplog):
    exc = RpcHTTPStatusError(429, "Too Many Requests", "getblock", ["0xab"])
    exc.attempts = 3  # type: ignore[attr-defined]  # set by utils.raise_outside_of_retry
    with dlq.add_to_deadletterqueue(H):
        raise exc
    assert caplog.messages[-1].endswith("(after 3 attempt(s))")
