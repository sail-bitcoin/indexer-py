from contextlib import contextmanager
from unittest.mock import patch

from tenacity import stop_after_attempt, wait_none


@contextmanager
def fast_retries(method: object, retries_number: int = 3):
    """Utility method to turn tenacity retry logic faster for test purpose."""
    call_retry = getattr(method, "retry")
    with (
        patch.object(call_retry, "wait", wait_none()),
        patch.object(call_retry, "stop", stop_after_attempt(retries_number)),
    ):
        yield
