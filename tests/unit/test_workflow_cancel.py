"""Cancel: OP → Cancelled with the system DP → Cancelled (Req §6)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit
from onepagerapp.models import CurrentUser, NewOnePagerInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import cancel_one_pager, create_one_pager
from tests.users import make_user

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def draft(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> str:
    create_one_pager(valid_input, creator, mock_data_access, document_store, now=NOW)
    return "OP-0003"


def _set_status(data_access: MockDataAccess, op: str, dp: str) -> None:
    row = data_access._status_rows["OP-0003"]
    data_access._status_rows["OP-0003"] = replace(
        row, one_pager_status=op, data_product_status=dp
    )


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "Ready for Review", "In Review"])
def test__cancel__sets_both_statuses(
    draft: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    status: str,
) -> None:
    _set_status(mock_data_access, status, "In Definition")

    row = cancel_one_pager(
        mock_data_access, draft, creator, reason="Duplicate", now=NOW
    )

    assert (row.one_pager_status, row.data_product_status) == ("Cancelled", "Cancelled")
    entries = mock_data_access.get_change_log(draft)[:2]
    assert {(e.status_field, e.from_status, e.to_status) for e in entries} == {
        ("one_pager_status", status, "Cancelled"),
        ("data_product_status", "In Definition", "Cancelled"),
    }
    assert {e.event_type for e in entries} == {"cancellation", "status_transition"}
    assert all(e.summary.endswith(": Duplicate") for e in entries)


@pytest.mark.unit
def test__cancel__releases_anyones_lock(
    draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    sme = make_user("DPR")
    open_for_edit(mock_data_access, draft, sme, "sme-session", now=NOW)

    cancel_one_pager(mock_data_access, draft, creator, now=NOW)

    assert mock_data_access.get_lock(draft) is None


@pytest.mark.unit
def test__cancel__admin_may_cancel_others(
    draft: str, mock_data_access: MockDataAccess
) -> None:
    admin = make_user("ADM")
    with pytest.raises(PermissionDeniedError):
        cancel_one_pager(mock_data_access, draft, admin, now=NOW)

    row = cancel_one_pager(mock_data_access, draft, admin, roles=[Actor.ADMIN], now=NOW)
    assert row.one_pager_status == "Cancelled"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp"),
    [
        ("Approved", "Ready for Development"),
        ("Draft Update", "Active"),
        ("Cancelled", "Cancelled"),
    ],
)
def test__cancel__not_allowed_after_approval_or_twice(
    draft: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    op: str,
    dp: str,
) -> None:
    _set_status(mock_data_access, op, dp)
    with pytest.raises(InvalidTransitionError):
        cancel_one_pager(mock_data_access, draft, creator, now=NOW)
    assert mock_data_access.get_one_pager_status_row(draft).one_pager_status == op
