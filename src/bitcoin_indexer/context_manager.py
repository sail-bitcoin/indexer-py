from contextlib import contextmanager
from json import JSONDecodeError
from sqlalchemy.exc import IntegrityError
import httpx

from logger import logger
from exceptions import RpcHTTPStatusError, BitcoinRpcError


@contextmanager
def fail_on_error(reraise=False):
    try:
        yield
    except (RpcHTTPStatusError, BitcoinRpcError, httpx.RequestError, httpx.HTTPError, httpx.InvalidURL, JSONDecodeError) as e:
        attempts = getattr(e, "attempts", None)
        if attempts:
            logger.error("%s (after %d attempt(s))", e, attempts)
        else:
            logger.error(e)
        if reraise:
            raise


@contextmanager
def fail_on_db_insert_error(session):
    try:
        yield
    except IntegrityError as e:
        session.rollback()
        logger.error({e})
