from contextlib import contextmanager
from json import JSONDecodeError

import httpx
from sqlalchemy.exc import SQLAlchemyError

from exceptions import RpcHTTPStatusError, BitcoinRpcError
from logger import logger


@contextmanager
def fail_on_error(reraise=False):
    try:
        yield
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
            logger.error(e)
        if reraise:
            raise


@contextmanager
def log_on_db_insert_error():
    try:
        yield
    except SQLAlchemyError as e:
        logger.error({e})
    except TypeError as e:
        logger.error({e})


@contextmanager
def rollback_on_error(session):
    with session.begin_nested():
        yield
