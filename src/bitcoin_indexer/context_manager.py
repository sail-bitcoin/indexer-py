from contextlib import contextmanager

from sqlalchemy.exc import SQLAlchemyError

from logger import logger


@contextmanager
def catch_db_exceptions():
    try:
        yield
    except SQLAlchemyError as e:
        logger.error("%s", e)
