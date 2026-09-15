from contextlib import contextmanager

from asyncpg import InterfaceError, PostgresError
from sqlalchemy.exc import SQLAlchemyError

from logger import logger


@contextmanager
def catch_db_exceptions():
    try:
        yield
    except (SQLAlchemyError, OSError, PostgresError, InterfaceError) as e:
        logger.error("%s", e)
