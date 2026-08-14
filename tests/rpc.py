import unittest
import os
import pathlib
import tempfile
import json
from unittest.mock import patch
from contextlib import contextmanager

import httpx
import respx
from aiolimiter import AsyncLimiter
from tenacity import stop_after_attempt, wait_none

import pytest

import rpc
from exceptions import RpcHTTPStatusError, BitcoinRpcError


@contextmanager
def prepare_rpc_call_cache_dir(mock_url="http://foo.com/bar"):
    with (
        tempfile.TemporaryDirectory() as tmp_dir,
        patch.object(rpc, "RPC_CACHE_DIR", pathlib.Path(tmp_dir)),
        patch.object(rpc, "getblockhash_ratio", AsyncLimiter(15, 1.0)),
        patch.dict(os.environ, {"RPC_URL": mock_url}, clear=True),
    ):
        yield pathlib.Path(tmp_dir)


@contextmanager
def fast_retries(retries_number=3):
    call_retry = getattr(rpc.RpcClient.call_rpc, "retry")
    with (
        patch.object(call_retry, "wait", wait_none()),
        patch.object(call_retry, "stop", stop_after_attempt(retries_number)),
    ):
        yield


class TestRpcClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.max_conn = 10
        self.max_conn_keepalive = 10
        self.verb = "POST"
        self.method = "getblockhash"
        self.params = [957354]
        self.params_int = 957354
        self.block_hash = "000000000000000000002bb58bd9225e26120abfab13434310c3252cfa5a982e"
        self.cache_file_name = "getblockhash_b14e2493ac3bef439b9f3941d853b79c4bc11fff7c5244acbac3d9e375b767b9.json"

    # -----------
    # session
    # -----------
    async def test_aenter_creates_session(self):
        r = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        self.assertIsNone(r._session)
        async with r as client:
            self.assertIs(client, r)
            self.assertIsInstance(r._session, httpx.AsyncClient)
            if r._session is not None:
                self.assertFalse(r._session.is_closed)
            else:
                assert True is False

    async def test_aexit_close_session(self):
        r = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        async with r:
            session = r._session
        if session is not None:
            assert session.is_closed is True
        else:
            assert True is False
        assert r._session is None

    async def test_aexit_closes_session_even_on_exception(self):
        r = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        session = None
        with self.assertRaises(ValueError):
            async with r:
                session = r._session
                raise ValueError("error")
        assert r._session is None
        if session is not None:
            assert session.is_closed is True
        else:
            assert True is False

    async def test_aenter_reuses_open_session(self):
        r = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        async with r:
            session1 = r._session
            await r._get_session()
            session2 = r._session
        self.assertIs(session1, session2)

    async def test_rpclient_instances_use_different_sessions(self):
        r1 = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        r2 = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        async with r1:
            session1 = r1._session
        async with r2:
            session2 = r2._session
        self.assertIsNot(session1, session2)

    # -----------
    # rpc_url
    # -----------
    @patch.dict(os.environ, {"RPC_URL": "https://foo.com/bar"}, clear=True)
    async def test_rpc_url(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            self.assertEqual(r.rpc_url, "https://foo.com/bar")

    @patch("rpc.load_dotenv")
    async def test_rpc_url_without_env_var_should_fail(self, mock_load_dotenv):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with patch.dict(os.environ, {}, clear=True):
                try:
                    r.rpc_url
                    assert False
                except RuntimeError:
                    assert True

    # -----------
    # call_rpc
    # -----------
    @pytest.mark.integration
    async def test_call_rpc(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            result = await r.call_rpc(self.verb, self.method, self.params)
            assert result == self.block_hash

    async def test_call_rpc_without_async_with(self):
        r = rpc.RpcClient(self.max_conn, self.max_conn_keepalive)
        with prepare_rpc_call_cache_dir():
            with respx.mock:
                # fmt: off
                route = respx.post(r.rpc_url).mock(
                    return_value=httpx.Response(200,json={"result": self.block_hash})
                )
                with pytest.raises(RuntimeError):
                    await r.call_rpc(self.verb, self.method, self.params)
                assert route.call_count == 0

    async def test_call_rpc_cache_no_cache_200(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                with respx.mock:
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value=httpx.Response(200,json={"result": self.block_hash})
                    )
                    result = await r.call_rpc(self.verb, self.method, self.params)
                assert result == self.block_hash
                assert route.called

    async def test_call_rpc_local_cache_hit_200(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                cache_file = rpc.RPC_CACHE_DIR / self.cache_file_name
                cache_file.write_text(json.dumps(self.block_hash))
                with respx.mock:
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(200,json={"result": "fake"})
                    )
                    result = await r.call_rpc(self.verb, self.method, self.params)
                assert result == self.block_hash
                assert not route.called

    async def test_call_rpc_retries_on_429_RpcHTTPStatusError(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(429,json={})
                    )
                    with pytest.raises(RpcHTTPStatusError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == retries

    async def test_call_rpc_retries_on_500_RpcHTTPStatusError(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(500,json={"result": "fake"})
                    )
                    with pytest.raises(RpcHTTPStatusError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == retries

    async def test_call_rpc_retries_on_200_BitcoinRpcError_error_not_none_with_code_message(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(200,json={"result": "nothing", "error": {"code": "-8", "message": "Invalid parameter"}})
                    )
                    with pytest.raises(BitcoinRpcError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == retries

    async def test_call_rpc_retries_on_200_BitcoinRpcError_error_not_none_without_code_message(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(200,json={"result": "nothing", "error": {}})
                    )
                    with pytest.raises(BitcoinRpcError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == retries

    async def test_call_rpc_retries_on_200_BitcoinRpcError_error_result_both_none(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(200,json={})
                    )
                    with pytest.raises(BitcoinRpcError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == retries

    async def test_call_rpc_should_not_retry_on_403(self):
        async with rpc.RpcClient(self.max_conn, self.max_conn_keepalive) as r:
            with prepare_rpc_call_cache_dir():
                retries = 3
                with respx.mock, fast_retries(retries):
                    # fmt: off
                    route = respx.post(r.rpc_url).mock(
                        return_value = httpx.Response(403,json={"result": "good"})
                    )
                    with pytest.raises(RpcHTTPStatusError):
                        await r.call_rpc(self.verb, self.method, self.params)
                    assert route.call_count == 1

    # ---------------
    # should_retry
    # ---------------
    async def test_should_retry_httpx_TimeoutException_yes(self):
        exc = httpx.TimeoutException("Request timeout")
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_httpx_NetworkError_yes(self):
        exc = httpx.NetworkError("Network Error")
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_httpx_ProtcolError_yes(self):
        exc = httpx.ProtocolError("Protocol Error")
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_httpx_JSONDecodeError_yes(self):
        exc = json.JSONDecodeError("Json decode error", "not json", 0)
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_BitcoinRpcError_yes(self):
        exc = BitcoinRpcError(method=self.method, params=self.params, code=-8, message="Invalid parameter")
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_RpcHTTPStatusError_403_no(self):
        exc = RpcHTTPStatusError(status_code=403, reason="Forbidden", method=self.method, params=self.params)
        res = rpc.should_retry(exc)
        assert res is False

    async def test_should_retry_RpcHTTPStatusError_429_yes(self):
        exc = RpcHTTPStatusError(status_code=429, reason="Too Many Requests", method=self.method, params=self.params)
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_RpcHTTPStatusError_500_yes(self):
        exc = RpcHTTPStatusError(status_code=500, reason="Internal server error", method=self.method, params=self.params)
        res = rpc.should_retry(exc)
        assert res is True

    async def test_should_retry_RpcHTTPStatusError_503_yes(self):
        exc = RpcHTTPStatusError(status_code=503, reason="Service Unavailable", method=self.method, params=self.params)
        res = rpc.should_retry(exc)
        assert res is True

    # -----------
    # rpc_url's wrapper
    # -----------
    async def test_blocks_rpc_call_wrapper(self):
        async with rpc.Blocks(self.max_conn, self.max_conn_keepalive) as b:
            with prepare_rpc_call_cache_dir():
                with respx.mock:
                    # fmt: off
                    route = respx.post(b.rpc_url).mock(
                        return_value= httpx.Response(200,json={"result": f"{self.block_hash}"})
                    )
                    wrapper_result = await b.get_block_hash(self.params_int)

                assert wrapper_result == self.block_hash
                assert route.called


if __name__ == "__main__":
    unittest.main()
