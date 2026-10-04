"""LakehouseAccess reads: user-facing errors reach the pages unchanged."""

import pytest

from onepagerapp.data_access.connection import (
    READ_ACCESS_DENIED_MESSAGE,
    ReadAccessDeniedError,
    SessionExpiredError,
    StatementFailedError,
)
from tests.helpers import LAKEHOUSE_READS, fake_connection, lakehouse_access

# Reads the pages call; each turns other failures into a RuntimeError.
PAGE_READS = [
    "get_registry",
    "get_registry_status_counts",
    "get_one_pager_status",
    "get_change_log",
    "get_review_comments",
    "get_lock",
    "get_locks",
    "get_use_cases",
    "get_use_case",
    "get_use_case_references",
]


@pytest.mark.unit
@pytest.mark.parametrize("method", PAGE_READS)
@pytest.mark.parametrize(
    "error",
    [
        ReadAccessDeniedError(READ_ACCESS_DENIED_MESSAGE.format(target="this data")),
        SessionExpiredError(),
    ],
    ids=["access_denied", "session_expired"],
)
def test__user_facing_error__page_read__same_error_raised(
    method: str, error: Exception
) -> None:
    """Access-denied and session-expired errors pass through unchanged."""
    # Given
    read = LAKEHOUSE_READS[method]
    access = lakehouse_access(fake_connection(error=error))

    # When / Then
    with pytest.raises(type(error)) as raised:
        read(access)
    assert raised.value is error


@pytest.mark.unit
@pytest.mark.parametrize("method", PAGE_READS)
def test__statement_failure__page_read__wrapped_in_runtime_error(method: str) -> None:
    """Other failures become a plain RuntimeError with the cause attached."""
    # Given
    read = LAKEHOUSE_READS[method]
    access = lakehouse_access(fake_connection(error=StatementFailedError("boom")))

    # When / Then
    with pytest.raises(RuntimeError, match="boom") as raised:
        read(access)
    assert type(raised.value) is RuntimeError
    assert isinstance(raised.value.__cause__, StatementFailedError)
