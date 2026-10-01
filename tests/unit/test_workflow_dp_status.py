"""Owner-initiated Data Product transitions (Backend §3, Req §6)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    ConfirmationRequiredError,
    change_data_product_status,
    data_product_options,
)
from tests.users import make_user

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
# OP-0001 is seeded Approved / Ready for Development, owned by Alice Brown (ABR).
OP_ID = "OP-0001"


@pytest.fixture
def owner() -> CurrentUser:
    return make_user("ABR", "Alice Brown")


def _set(data_access: MockDataAccess, op: str, dp: str) -> None:
    row = data_access._status_rows[OP_ID]
    data_access._status_rows[OP_ID] = replace(
        row, one_pager_status=op, data_product_status=dp
    )


def _targets(data_access: MockDataAccess, *, owner_or_sme: bool = True) -> list[str]:
    row = data_access.get_one_pager_status_row(OP_ID)
    return [r.to_status for r in data_product_options(row, owner_or_sme=owner_or_sme)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dp", "targets"),
    [
        ("Ready for Development", ["In Development"]),
        ("In Enhancement", ["In Development"]),
        ("In Development", ["Active"]),
        ("Active", ["Deprecated"]),
        ("Deprecated", []),
    ],
)
def test__options_follow_state_machine(
    mock_data_access: MockDataAccess, dp: str, targets: list[str]
) -> None:
    _set(mock_data_access, "Approved", dp)
    assert _targets(mock_data_access) == targets
    assert _targets(mock_data_access, owner_or_sme=False) == []


@pytest.mark.unit
def test__no_options_while_draft_update(mock_data_access: MockDataAccess) -> None:
    _set(mock_data_access, "Draft Update", "In Development")
    assert _targets(mock_data_access) == []


@pytest.mark.unit
def test__lifecycle_start_activate_deprecate(
    mock_data_access: MockDataAccess, owner: CurrentUser
) -> None:
    change_data_product_status(
        mock_data_access, OP_ID, "In Development", owner, now=NOW
    )
    change_data_product_status(mock_data_access, OP_ID, "Active", owner, now=NOW)
    with pytest.raises(ConfirmationRequiredError):
        change_data_product_status(
            mock_data_access, OP_ID, "Deprecated", owner, now=NOW
        )
    row = change_data_product_status(
        mock_data_access, OP_ID, "Deprecated", owner, confirmed=True, now=NOW
    )

    assert (row.one_pager_status, row.data_product_status) == ("Approved", "Deprecated")
    assert row.version == "1.0.0"
    latest = mock_data_access.get_change_log(OP_ID)[0]
    assert (latest.status_field, latest.from_status, latest.to_status) == (
        "data_product_status",
        "Active",
        "Deprecated",
    )
    assert latest.summary == "Data Product deprecated"


@pytest.mark.unit
def test__not_owner_or_sme(mock_data_access: MockDataAccess) -> None:
    other = make_user("BSM", "Bob Smith")
    with pytest.raises(PermissionDeniedError):
        change_data_product_status(mock_data_access, OP_ID, "In Development", other)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp", "target"),
    [
        ("Approved", "Ready for Development", "Active"),  # skips a step
        ("Approved", "Active", "In Enhancement"),  # system transition
        ("Draft Update", "Ready for Development", "In Development"),  # OP not Approved
        ("Approved", "Deprecated", "Active"),  # terminal
    ],
)
def test__invalid_transitions(
    mock_data_access: MockDataAccess,
    owner: CurrentUser,
    op: str,
    dp: str,
    target: str,
) -> None:
    _set(mock_data_access, op, dp)
    with pytest.raises(InvalidTransitionError):
        change_data_product_status(
            mock_data_access, OP_ID, target, owner, confirmed=True
        )
    assert mock_data_access.get_one_pager_status_row(OP_ID).data_product_status == dp
