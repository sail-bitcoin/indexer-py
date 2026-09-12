from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.exc import OperationalError

import dlq
from exceptions import RpcHTTPStatusError, BitcoinRpcError

# fmt: off
exc_to_catch = [
    KeyError("error"),
    OperationalError("INSERT ...", {}, Exception("db cconnectin lost")),
    httpx.RequestError("error"),
    RpcHTTPStatusError(429, "Too Many Retries", "getblockhash", ["0xab"]),
    BitcoinRpcError("getblockhash", ["0xab"], -8, "invalid parameter")
]


@pytest.mark.parametrize("exc_class", exc_to_catch, ids=lambda e: type(e).__name__)
@patch("db.insert_block")
def test__add_to_queue_on_insert_block_exception(mock_insert_block, exc_class, clear_dlq):
    h = 999
    assert dlq.queue == []
    with dlq.add_to_deadletterqueue(h):
        raise exc_class
    assert len(dlq.queue) == 1
    assert dlq.queue[0] == h


@patch("db.insert_block")
def test__dlq_stays_empty_if_insert_block_raise_no_exception(mock_insert_block, clear_dlq):
    h = 999
    assert dlq.queue == []
    with dlq.add_to_deadletterqueue(h):
        print("no error")
    assert dlq.queue == []
