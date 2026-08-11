import unittest
import os
import pathlib
import tempfile
import json
from unittest.mock import patch
from contextlib import contextmanager

import pytest
import requests_mock
from urllib3.response import HTTPResponse
from requests.adapters import HTTPAdapter

from bitcoin_indexer import rpc


@contextmanager
def prepare_rpc_call_cache_dir(access_token="foo"):
    with (
        tempfile.TemporaryDirectory() as tmp_dir,
        patch.object(rpc, "RPC_CACHE_DIR", pathlib.Path(tmp_dir)),
        patch.dict(os.environ, {"GETBLOCK_ACCESS_TOKEN": access_token}, clear=True),
    ):
        yield pathlib.Path(tmp_dir)


class TestGetBlockClient(unittest.TestCase):
    def setUp(self):
        self.verb = "POST"
        self.method = "getblockhash"
        self.params = [957354]
        self.params_int = 957354
        self.block_hash = "000000000000000000002bb58bd9225e26120abfab13434310c3252cfa5a982e"
        self.cache_file_name = "getblockhash_b14e2493ac3bef439b9f3941d853b79c4bc11fff7c5244acbac3d9e375b767b9.json"

    # -----------
    # rpc_url
    # -----------
    @patch.dict(os.environ, {"GETBLOCK_ACCESS_TOKEN": "foo"}, clear=True)
    def test_rpc_url(self):
        gbc = rpc.GetBlockClient()
        self.assertEqual(gbc.rpc_url, "https://go.getblock.io/foo")

    @patch("bitcoin_indexer.rpc.load_dotenv")
    def test_rpc_url_without_env_var_should_fail(self, mock_load_dotenv):
        with patch.dict(os.environ, {}, clear=True):
            try:
                gbc = rpc.GetBlockClient()
                gbc.rpc_url
                assert False
            except RuntimeError:
                assert True

    # -----------
    # call_rpc
    # -----------
    @pytest.mark.integration
    def test_call_rpc(self):
        gbc = rpc.GetBlockClient()
        result = gbc.call_rpc(self.verb, self.method, self.params)
        assert result == self.block_hash

    def test_call_rpc_cache_no_cache(self):
        with prepare_rpc_call_cache_dir():
            gbc = rpc.GetBlockClient()
            with requests_mock.Mocker() as m:
                m.post(gbc.rpc_url, json={"result": f"{self.block_hash}"})
                result = gbc.call_rpc(self.verb, self.method, self.params)
            assert result == self.block_hash
            assert m.called

    def test_call_rpc_local_cache_hit(self):
        with prepare_rpc_call_cache_dir():
            cache_file = rpc.RPC_CACHE_DIR / self.cache_file_name
            cache_file.write_text(json.dumps(self.block_hash))
            gbc = rpc.GetBlockClient()
            with requests_mock.Mocker() as m:
                m.post(gbc.rpc_url, json={"result": "fake"})
                result = gbc.call_rpc(self.verb, self.method, self.params)
            assert result == self.block_hash
            assert not m.called

    @patch("urllib3.connectionpool.HTTPConnectionPool._make_request")
    def test_call_rpc_retries_on_failure(self, mock__make_request):
        with prepare_rpc_call_cache_dir():
            gbc = rpc.GetBlockClient()
            fast_retries = gbc._retries.new(backoff_factor=0.01)
            total_retries = (gbc._retries.total or 0) + 1
            gbc._session.mount("https://", HTTPAdapter(max_retries=fast_retries))

            mock__make_request.return_value = HTTPResponse(
                body="b",
                status=500,
                reason="foo",
                request_url="foo.com",
                preload_content=False,
            )
            gbc.call_rpc(self.verb, self.method, self.params)

            assert mock__make_request.call_count == total_retries

    # -----------
    # rpc_url's wrapper
    # -----------
    def test_blocks_rpc_call_wrapper(self):
        with prepare_rpc_call_cache_dir():
            b = rpc.Blocks()
            with requests_mock.Mocker() as m:
                m.post(b.rpc_url, json={"result": f"{self.block_hash}"})
                wrapper_result = b.get_block_hash(self.params_int)

            assert wrapper_result == self.block_hash
            assert m.called


if __name__ == "__main__":
    unittest.main()
