from typing import NoReturn
from tenacity import RetryCallState


def raise_outside_of_retry(retry_state: RetryCallState) -> NoReturn:
    assert retry_state.outcome is not None
    exc = retry_state.outcome.exception()
    assert exc is not None
    exc.attempts = retry_state.attempt_number  # type: ignore[attr-defined]
    raise exc
