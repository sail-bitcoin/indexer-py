from contextlib import contextmanager
from json import JSONDecodeError

import asyncpg
import httpx
from sqlalchemy.exc import SQLAlchemyError

from logger import logger
from exceptions import RpcHTTPStatusError, BitcoinRpcError

queue: list[int] = []


@contextmanager
def add_to_deadletterqueue(height: int):
    """When db insertion fail (even after retries for retryable exceptions) add to a Dead Letter Queue the block"""
    try:
        yield
    except (SQLAlchemyError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
        orig = getattr(e, "orig", e)  # asyncpg exception, if any
        pgcode = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
        logger.error("Block %s insertion failed [%s]: %s", height, pgcode, e)
        queue.append(height)
    except (
        RpcHTTPStatusError,
        BitcoinRpcError,
        httpx.RequestError,
        httpx.HTTPError,
        httpx.InvalidURL,
        JSONDecodeError,
    ) as e:
        attempts = getattr(e, "attempts", None)
        if attempts:
            logger.error("%s (after %d attempt(s))", e, attempts)
        else:
            logger.error("%s", e)
        queue.append(height)
    except (TypeError, KeyError, ValueError, OverflowError) as e:
        logger.error("%s", e)
        queue.append(height)
