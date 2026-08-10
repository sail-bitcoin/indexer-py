import hashlib
import json
import os
import requests

from pathlib import Path
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from requests.exceptions import HTTPError

from logger import logger
import context_manager


RPC_CACHE_DIR = Path("var/rpc_cache")


# ------------------------------------------------------------
class GetBlockClient:
    """Shared GetBlock JSON-RPC setup"""

    def __init__(self):
        self._headers = {"Content-Type": "application/json"}
        self._payload = {"jsonrpc": "2.0", "id": "getblock.io"}
        self._rpc_domain = "go.getblock.io"
        self._rpc_url = None
        self._retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504, 505],
            allowed_methods=frozenset(["POST"]),  # API is RPC not REST
        )
        self._session = self._create_session()

    @property
    def rpc_url(self) -> str:
        if self._rpc_url is None:
            load_dotenv()
            token = os.getenv("GETBLOCK_ACCESS_TOKEN")
            if not token:
                raise RuntimeError("Could not retrieve GetBlock Access Token — check .env file")
            self._rpc_url = f"https://{self._rpc_domain}/{token}"
        return self._rpc_url

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        s.mount("https://", HTTPAdapter(max_retries=self._retries))
        return s

    def call_rpc(self, verb: str, method: str, params: list = []):
        cache_key = hashlib.sha256(f"{method}:{params}".encode()).hexdigest()
        cache_file = RPC_CACHE_DIR / f"{method}_{cache_key}.json"
        if cache_file.exists():
            logger.info("Cache hit:  %s  with params: %s", method, params)
            return json.loads(cache_file.read_text())

        url = self.rpc_url
        logger.info("Calling RPC:  %s  %s  with params: %s", verb, method, params)

        with context_manager.fail_on_error():
            payload = json.dumps({**self._payload, "method": method, "params": params})
            response = self._session.request(verb, url, headers=self._headers, data=payload)
            if response.status_code != 200:
                raise HTTPError(response=response)

            logger.info("Request succeded.")
            result = response.json()["result"]

            RPC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result))
            return result


# ------------------------------------------------------------
class Blocks(GetBlockClient):
    def get_block_hash(self, block_height: int):
        return self.call_rpc("POST", "getblockhash", [block_height])

    def get_block(self, block_hash: str, verbosity: int = 1):
        return self.call_rpc("POST", "getblock", [block_hash, verbosity])


class NetworkInfo(GetBlockClient):
    def get_blockchaininfo(self):
        return self.call_rpc("POST", "getblockchaininfo", [])
