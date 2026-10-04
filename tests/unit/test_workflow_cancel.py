"""Cancel: OP → Cancelled with the system DP → Cancelled (Req §6)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import open_for_edit
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import cancel_one_pager
from tests.helpers import ADMIN_ROLES, NOW, make_user, update_status_row

ADMIN = make_user("ADM")


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "Ready for Review", "In Review"])
def test__one_pager_before_approval__cancel__both_statuses_cancelled(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    status: str,
) -> None:
    """Cancel sets both statuses and logs each change with the reason."""
    # Given
    update_status_row(mock_data_access, draft_id, one_pager_status=status)

    # When
    row = cancel_one_pager(
        mock_data_access, draft_id, creator, reason="Duplicate", now=NOW
    )

    # Then
    assert (row.one_pager_status, row.data_product_status) == ("Cancelled", "Cancelled")
    entries = mock_data_access.get_change_log(draft_id)[:2]
    assert {(e.status_field, e.from_status, e.to_status) for e in entries} == {
        ("one_pager_status", status, "Cancelled"),
        ("data_product_status", "In Definition", "Cancelled"),
    }
    assert {e.event_type for e in entries} == {"cancellation", "status_transition"}
    assert all(e.summary.endswith(": Duplicate") for e in entries)


@pytest.mark.unit
def test__draft_locked_by_sme__owner_cancels__lock_is_released(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Cancelling releases the lock, whoever holds it."""
    # Given
    open_for_edit(mock_data_access, draft_id, make_user("DPR"), "sme-session", now=NOW)

    # When
    cancel_one_pager(mock_data_access, draft_id, creator, now=NOW)

    # Then
    assert mock_data_access.get_lock(draft_id) is None


@pytest.mark.unit
def test__user_not_owner_or_sme_without_admin_role__cancel__raises_denied(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """A stranger cannot cancel."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        cancel_one_pager(mock_data_access, draft_id, ADMIN, now=NOW)


@pytest.mark.unit
def test__admin_not_owner_or_sme__cancel__one_pager_cancelled(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """An Admin may cancel any One Pager."""
    # When
    row = cancel_one_pager(
        mock_data_access, draft_id, ADMIN, roles=ADMIN_ROLES, now=NOW
    )

    # Then
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
def test__approved_or_cancelled_one_pager__cancel__raises_invalid_transition(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    op: str,
    dp: str,
) -> None:
    """Cancel is not possible after approval, nor twice."""
    # Given
    update_status_row(
        mock_data_access, draft_id, one_pager_status=op, data_product_status=dp
    )

    # When / Then
    with pytest.raises(InvalidTransitionError):
        cancel_one_pager(mock_data_access, draft_id, creator, now=NOW)
    assert mock_data_access.get_one_pager_status_row(draft_id).one_pager_status == op
