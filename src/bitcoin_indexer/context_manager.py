from contextlib import contextmanager
from requests import exceptions
from sqlalchemy.exc import IntegrityError

from logger import logger


@contextmanager
def fail_on_error():
    try:
        yield
    except exceptions.HTTPError as e:
        if e.response is not None:
            logger.error("HTTPError %s: %s - %s", e.response.status_code, e.response.reason, e.response.text)
        else:
            logger.error("HTTPError (no response): %s", e)
    except (exceptions.RequestException, AttributeError) as e:
        logger.error(e)


@contextmanager
def fail_on_db_insert_error(session):
    try:
        yield
    except IntegrityError as e:
        session.rollback()
        logger.error({e})
