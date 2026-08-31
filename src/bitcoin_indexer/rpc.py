import hashlib
import os
from json import JSONDecodeError
from logging import WARNING
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import orjson
from aiolimiter import AsyncLimiter
from dotenv import load_dotenv
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from exceptions import RpcHTTPStatusError, BitcoinRpcError
from logger import logger
from utils import raise_outside_of_retry


RPC_CACHE_DIR = Path("var/rpc_cache")
TIMEOUT_CONNECT = 10
TIMEOUT_READ = 60
TIMEOUT_WRITE = 10
TIMEOUT_POOL = 10

getblockhash_ratio = AsyncLimiter(6, 1.0)


def should_retry(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError, JSONDecodeError)):
        return True
    if isinstance(exc, RpcHTTPStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, BitcoinRpcError):
        return True
    return False


# ------------------------------------------------------------
class RpcClient:
    """Rpc Client JSON-RPC setup"""

    def __init__(self, max_conn=15, max_conn_keepalive=15) -> None:
        self.max_conn = max_conn
        self.max_conn_keepalived = max_conn_keepalive
        self._headers = {"Content-Type": "application/json"}
        self._payload = {"jsonrpc": "2.0", "id": 1}
        self._rpc_url = None
        self._session: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        await self._get_session()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: Exception | None, tb: TracebackType | None) -> None:
        await self._close_session()

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            # fmt: off
            self._session = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=TIMEOUT_CONNECT, read=TIMEOUT_READ, write=TIMEOUT_WRITE, pool=TIMEOUT_POOL),
                limits=httpx.Limits(
                    max_connections=self.max_conn,
                    max_keepalive_connections=self.max_conn_keepalived,
                )
            )
        return self._session

    async def _close_session(self) -> None:
        if self._session and not self._session.is_closed:
            await self._session.aclose()
            self._session = None

    @property
    def rpc_url(self) -> str:
        if self._rpc_url is None:
            load_dotenv()
            self._rpc_url = os.getenv("RPC_URL")
            if not self._rpc_url:
                raise RuntimeError("Could not retrieve RPC_URL — check .env file")
        return self._rpc_url

    # fmt: off
    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential_jitter(initial=1, jitter=3, max=10),
        retry_error_callback=raise_outside_of_retry,
        retry=retry_if_exception(should_retry),
        before_sleep=before_sleep_log(logger, WARNING),
    )
    async def call_rpc(self, verb: str, method: str, params: list | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("RpcClient must be used as `async with` for session lifecycle management.")
        cache_key = hashlib.sha256(f"{method}:{params}".encode()).hexdigest()
        cache_file = RPC_CACHE_DIR / f"{method}_{cache_key}.json"
        if cache_file.exists():
            logger.info("Cache hit:  %s %s", method, params)
            return orjson.loads(cache_file.read_bytes())

        url = self.rpc_url
        logger.info("Calling RPC:  %s  %s %s", verb, method, params)

        payload = orjson.dumps({**self._payload, "method": method, "params": params})
        if method == "getblockhash":
            async with getblockhash_ratio:
                response = await self._session.request(verb, url, headers=self._headers, content=payload)
        else:
            response = await self._session.request(verb, url, headers=self._headers, content=payload)

        if response.status_code != 200:
            raise RpcHTTPStatusError(status_code=response.status_code, method=method, reason=response.reason_phrase, params=params)

        resp = orjson.loads(response.content)
        if resp.get("error") is not None:
            code = resp.get("error", {}).get("code")
            message = resp.get("error", {}).get("message")
            raise BitcoinRpcError(method, params, code=code, message=message)
        if resp.get("result") is None:
            raise BitcoinRpcError(method, params)

        logger.info("Request succeded.")
        result = resp["result"]

        RPC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(orjson.dumps(result))
        return result


# ------------------------------------------------------------
class Blocks(RpcClient):
    async def get_block_hash(self, block_height: int) -> Any:
        response = await self.call_rpc("POST", "getblockhash", [block_height])
        return response

    async def get_lastblock(self) -> int:
        response = await self.call_rpc("POST", "getblockcount", [])
        return response

    async def get_block(self, block_hash: str, verbosity: int = 2) -> Any:
        response = await self.call_rpc("POST", "getblock", [block_hash, verbosity])
        return response

    async def get_block_from_height(self, height: int, verbosity: int = 2) -> Any:
        block_hash = await self.get_block_hash(height)
        response = await self.get_block(block_hash, verbosity)
        return response
