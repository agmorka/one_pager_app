"""LakehouseAccess reads: user-facing errors reach the pages unchanged."""

from collections.abc import Callable

import pytest

from onepagerapp.data_access.connection import (
    READ_ACCESS_DENIED_MESSAGE,
    ReadAccessDeniedError,
    SessionExpiredError,
    StatementFailedError,
)
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.models import RegistryFilter, UseCaseFilter
from tests.unit.test_lakehouse_writes import _access, _FakeConnection

READS: dict[str, Callable[[LakehouseAccess], object]] = {
    "get_registry": lambda a: a.get_registry(RegistryFilter(), 1, 10),
    "get_registry_status_counts": lambda a: a.get_registry_status_counts(
        RegistryFilter()
    ),
    "get_one_pager_status": lambda a: a.get_one_pager_status("OP-0001"),
    "get_change_log": lambda a: a.get_change_log("OP-0001"),
    "get_review_comments": lambda a: a.get_review_comments("OP-0001"),
    "get_lock": lambda a: a.get_lock("OP-0001"),
    "get_locks": lambda a: a.get_locks(["OP-0001"]),
    "get_use_cases": lambda a: a.get_use_cases(UseCaseFilter(), 1, 10),
    "get_use_case": lambda a: a.get_use_case("UC-001"),
    "get_use_case_references": lambda a: a.get_use_case_references("UC-001"),
}


@pytest.mark.unit
@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
@pytest.mark.parametrize(
    "error",
    [
        ReadAccessDeniedError(READ_ACCESS_DENIED_MESSAGE.format(target="this data")),
        SessionExpiredError(),
    ],
    ids=["access_denied", "session_expired"],
)
def test__lakehouse_read__keeps_user_facing_errors(
    read: Callable[[LakehouseAccess], object], error: Exception
) -> None:
    access = _access(_FakeConnection(error=error))

    with pytest.raises(type(error)) as raised:
        read(access)

    assert raised.value is error


@pytest.mark.unit
@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test__lakehouse_read__wraps_other_failures(
    read: Callable[[LakehouseAccess], object],
) -> None:
    access = _access(_FakeConnection(error=StatementFailedError("boom")))

    with pytest.raises(RuntimeError, match="boom") as raised:
        read(access)

    assert type(raised.value) is RuntimeError
    assert isinstance(raised.value.__cause__, StatementFailedError)
