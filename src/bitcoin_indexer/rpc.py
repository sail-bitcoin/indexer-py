import time  # TODO
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
import simdjson

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
    async def call_rpc(self, parser: simdjson.Parser, verb: str, method: str, params: list | None = None) -> Any:
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
                resp_raw = await self._session.request(verb, url, headers=self._headers, content=payload)
        else:
            resp_raw = await self._session.request(verb, url, headers=self._headers, content=payload)

        parsed_resp_raw = parser.parse(resp_raw.content)
        if resp_raw.status_code != 200:
            raise RpcHTTPStatusError(status_code=resp_raw.status_code, method=method, reason=resp_raw.reason_phrase, params=params)


        # TODO: timing loads vs raw parsing for a block:
        # orjson loads: 31.600ms
        # simdjson parsing: 17.930ms
        # t = time.perf_counter()
        # resp = orjson.loads(resp_raw.content)
        # print(f"orjson loads: {(time.perf_counter() - t)*1000:.3f}ms")
        # t = time.perf_counter()
        #parsed_resp_raw = parser.parse(resp_raw.content)
        # print(f"simdjson parsing: {(time.perf_counter() - t)*1000:.3f}ms")

        #TODO:
        # - stop deserializing here, just handle simdjson parsed raw, handle errors and return it. deserializing will happend in db.insert_from_dict
        #       ---> require the parser to be defined at the module/file level, not the class!!!!
        if parsed_resp_raw.get("error") is not None:
            code = parsed_resp_raw.get("error", {}).get("code")
            message = parsed_resp_raw.get("error", {}).get("message")
            raise BitcoinRpcError(method, params, code=code, message=message)
        if parsed_resp_raw.get("result") is None:
            raise BitcoinRpcError(method, params)

        logger.info("Request succeded.")
        parsed_result_raw = parsed_resp_raw.get("result")

        # TODO: remove caching
        # RPC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # cache_file.write_bytes(orjson.dumps(result))

        #TODO: see todo above, what should we return?
        return parsed_result_raw


# ------------------------------------------------------------
class Blocks(RpcClient):
    async def get_block_hash(self, block_height: int, parser: simdjson.Parser) -> Any:
        res = await self.call_rpc(parser, "POST", "getblockhash", [block_height])
        return res

    async def get_lastblock(self, parser: simdjson.Parser) -> int:
        res = await self.call_rpc(parser, "POST", "getblockcount", [])
        return res

    async def get_block(self, block_hash: str, parser: simdjson.Parser, verbosity: int = 2) -> Any:
        res = await self.call_rpc(parser, "POST", "getblock", [block_hash, verbosity])
        return res

    async def get_block_from_height(self, height: int, parser: simdjson.Parser, verbosity: int = 2) -> Any:
        block_hash = await self.get_block_hash(height, parser=parser)
        res = await self.get_block(block_hash, verbosity=verbosity, parser=parser)
        return res
