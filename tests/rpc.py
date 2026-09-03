import os
import pathlib
import tempfile
import json
from unittest.mock import patch
from contextlib import contextmanager

import httpx
import respx
import symdjson
from aiolimiter import AsyncLimiter
from tenacity import stop_after_attempt, wait_none

import pytest

from tests.utils import fast_retries
import rpc
from exceptions import RpcHTTPStatusError, BitcoinRpcError


MAX_CONN = 10
MAX_CONN_KEEPALIVE = 10
VERB = "POST"
METHOD = "getblockhash"
PARAMS = [957354]
PARAMS_INT = 957354
BLOCK_HASH = "000000000000000000002bb58bd9225e26120abfab13434310c3252cfa5a982e"
CACHE_FILE_NAME = "getblockhash_b14e2493ac3bef439b9f3941d853b79c4bc11fff7c5244acbac3d9e375b767b9.json"


@contextmanager
def prepare_rpc_call_cache_dir(mock_url="http://foo.com/bar"):
    with (
        tempfile.TemporaryDirectory() as tmp_dir,
        patch.object(rpc, "RPC_CACHE_DIR", pathlib.Path(tmp_dir)),
        patch.object(rpc, "getblockhash_ratio", AsyncLimiter(15, 1.0)),
        patch.dict(os.environ, {"RPC_URL": mock_url}, clear=True),
    ):
        yield pathlib.Path(tmp_dir)


# -----------
# session
# -----------
async def test_aenter_creates_session():
    r = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    assert r._session is None
    async with r as client:
        assert client is r
        assert isinstance(r._session, httpx.AsyncClient)
        if r._session is not None:
            assert r._session.is_closed is False
        else:
            assert True is False


async def test_aexit_close_session():
    r = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    async with r:
        session = r._session
    if session is not None:
        assert session.is_closed is True
    else:
        assert True is False
    assert r._session is None


async def test_aexit_closes_session_even_on_exception():
    r = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    session = None
    with pytest.raises(ValueError):
        async with r:
            session = r._session
            raise ValueError("error")
    assert r._session is None
    if session is not None:
        assert session.is_closed is True
    else:
        assert True is False


async def test_aenter_reuses_open_session():
    r = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    async with r:
        session1 = r._session
        await r._get_session()
        session2 = r._session
    assert session1 is session2


async def test_rpclient_instances_use_different_sessions():
    r1 = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    r2 = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    async with r1:
        session1 = r1._session
    async with r2:
        session2 = r2._session
    assert session1 is not session2


# -----------
# rpc_url
# -----------
@patch.dict(os.environ, {"RPC_URL": "https://foo.com/bar"}, clear=True)
async def test_rpc_url():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        assert r.rpc_url == "https://foo.com/bar"


@patch("rpc.load_dotenv")
async def test_rpc_url_without_env_var_should_fail(mock_load_dotenv):
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError):
                r.rpc_url


# -----------
# call_rpc
# -----------
@pytest.mark.integration
async def test_call_rpc():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        result = await r.call_rpc(VERB, METHOD, PARAMS)
        assert result == BLOCK_HASH


async def test_call_rpc_without_async_with():
    r = rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE)
    with prepare_rpc_call_cache_dir():
        with respx.mock:
            # fmt: off
            route = respx.post(r.rpc_url).mock(
                return_value=httpx.Response(200,json={"result": BLOCK_HASH})
            )
            with pytest.raises(RuntimeError):
                await r.call_rpc(VERB, METHOD, PARAMS)
            assert route.call_count == 0


async def test_call_rpc_cache_no_cache_200():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            with respx.mock:
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value=httpx.Response(200,json={"result": BLOCK_HASH})
                )
                result = await r.call_rpc(VERB, METHOD, PARAMS)
            assert result == BLOCK_HASH
            assert route.called


async def test_call_rpc_local_cache_hit_200():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            cache_file = rpc.RPC_CACHE_DIR / CACHE_FILE_NAME
            cache_file.write_text(json.dumps(BLOCK_HASH))
            with respx.mock:
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(200,json={"result": "fake"})
                )
                result = await r.call_rpc(VERB, METHOD, PARAMS)
            assert result == BLOCK_HASH
            assert not route.called


async def test_call_rpc_retries_on_429_RpcHTTPStatusError():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(429,json={"foo": "bar"})
                )
                with pytest.raises(RpcHTTPStatusError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == retries


async def test_call_rpc_retries_on_500_RpcHTTPStatusError():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(500,json={"result": "fake"})
                )
                with pytest.raises(RpcHTTPStatusError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == retries


async def test_call_rpc_retries_on_200_BitcoinRpcError_error_not_none_with_code_message():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(200,json={"result": "nothing", "error": {"code": "-8", "message": "Invalid parameter"}})
                )
                with pytest.raises(BitcoinRpcError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == retries


async def test_call_rpc_retries_on_200_BitcoinRpcError_error_not_none_without_code_message():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(200,json={"result": "nothing", "error": {}})
                )
                with pytest.raises(BitcoinRpcError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == retries


async def test_call_rpc_retries_on_200_BitcoinRpcError_error_result_both_none():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(200,json={})
                )
                with pytest.raises(BitcoinRpcError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == retries


async def test_call_rpc_should_not_retry_on_403():
    async with rpc.RpcClient(MAX_CONN, MAX_CONN_KEEPALIVE) as r:
        with prepare_rpc_call_cache_dir():
            retries = 3
            with respx.mock, fast_retries(rpc.RpcClient.call_rpc, retries):
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value = httpx.Response(403,json={"result": "good"})
                )
                with pytest.raises(RpcHTTPStatusError):
                    await r.call_rpc(VERB, METHOD, PARAMS)
                assert route.call_count == 1


# ---------------
# should_retry
# ---------------
async def test_should_retry_httpx_TimeoutException_yes():
    exc = httpx.TimeoutException("Request timeout")
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_httpx_NetworkError_yes():
    exc = httpx.NetworkError("Network Error")
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_httpx_ProtcolError_yes():
    exc = httpx.ProtocolError("Protocol Error")
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_httpx_JSONDecodeError_yes():
    exc = json.JSONDecodeError("Json decode error", "not json", 0)
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_BitcoinRpcError_yes():
    exc = BitcoinRpcError(method=METHOD, params=PARAMS, code=-8, message="Invalid parameter")
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_RpcHTTPStatusError_403_no():
    exc = RpcHTTPStatusError(status_code=403, reason="Forbidden", method=METHOD, params=PARAMS)
    res = rpc.should_retry(exc)
    assert res is False


async def test_should_retry_RpcHTTPStatusError_429_yes():
    exc = RpcHTTPStatusError(status_code=429, reason="Too Many Requests", method=METHOD, params=PARAMS)
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_RpcHTTPStatusError_500_yes():
    exc = RpcHTTPStatusError(status_code=500, reason="Internal server error", method=METHOD, params=PARAMS)
    res = rpc.should_retry(exc)
    assert res is True


async def test_should_retry_RpcHTTPStatusError_503_yes():
    exc = RpcHTTPStatusError(status_code=503, reason="Service Unavailable", method=METHOD, params=PARAMS)
    res = rpc.should_retry(exc)
    assert res is True


# -----------
# rpc_url's wrapper
# -----------
async def test_blocks_rpc_call_wrapper():
    async with rpc.Blocks(MAX_CONN, MAX_CONN_KEEPALIVE) as b:
        with prepare_rpc_call_cache_dir():
            with respx.mock:
                # fmt: off
                route = respx.post(b.rpc_url).mock(
                    return_value= httpx.Response(200,json={"result": f"{BLOCK_HASH}"})
                )
                wrapper_result = await b.get_block_hash(PARAMS_INT)

            assert wrapper_result == BLOCK_HASH
            assert route.called
