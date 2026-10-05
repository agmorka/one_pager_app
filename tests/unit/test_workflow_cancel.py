"""Cancel: OP → Cancelled with the system DP → Cancelled (Req §6)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import TransitionError, cancel_one_pager
from tests.helpers import (
    ADMIN_ROLES,
    NOW,
    failing,
    make_user,
    update_status_row,
)

ADMIN = make_user("ADM")


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "Ready for Review", "In Review"])
def test__one_pager_before_approval__cancel__both_statuses_cancelled(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    status: str,
) -> None:
    """Cancel sets both statuses and logs each change with the reason."""
    # Given
    update_status_row(mock_data_access, draft_id, one_pager_status=status)

    # When
    row = cancel_one_pager(
        mock_data_access,
        document_store,
        draft_id,
        creator,
        reason="Duplicate",
        now=NOW,
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
def test__draft_v0_1_0__cancel__next_major_version_file_written(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Like an approval, cancelling closes the One Pager as the next MAJOR."""
    # When
    row = cancel_one_pager(mock_data_access, document_store, draft_id, creator, now=NOW)

    # Then
    assert row.version == "1.0.0"
    assert mock_data_access.get_one_pager_status_row(draft_id).version == "1.0.0"
    assert {e.version for e in mock_data_access.get_change_log(draft_id)[:2]} == {
        "1.0.0"
    }
    document = document_store.read(draft_id, "1.0.0")
    assert document is not None
    assert document.version == "1.0.0"
    assert (document.one_pager_status, document.data_product_status) == (
        "Cancelled",
        "Cancelled",
    )
    assert document.change_log[-1]["version"] == "1.0.0"
    assert document_store.exists(draft_id, "0.1.0")


@pytest.mark.unit
def test__change_log_write_fails__cancel__changes_nothing(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed cancel leaves the row and the version files as they were."""
    # Given
    monkeypatch.setattr(
        mock_data_access, "append_change_log_entries", failing("warehouse down")
    )

    # When / Then
    with pytest.raises(TransitionError):
        cancel_one_pager(mock_data_access, document_store, draft_id, creator, now=NOW)
    row = mock_data_access.get_one_pager_status_row(draft_id)
    assert (row.one_pager_status, row.version) == ("Draft", "0.1.0")
    assert not document_store.exists(draft_id, "1.0.0")


@pytest.mark.unit
def test__draft_locked_by_sme__owner_cancels__lock_is_released(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Cancelling releases the lock, whoever holds it."""
    # Given
    open_for_edit(mock_data_access, draft_id, make_user("DPR"), "sme-session", now=NOW)

    # When
    cancel_one_pager(mock_data_access, document_store, draft_id, creator, now=NOW)

    # Then
    assert mock_data_access.get_lock(draft_id) is None


@pytest.mark.unit
def test__user_not_owner_or_sme_without_admin_role__cancel__raises_denied(
    draft_id: str,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A stranger cannot cancel."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        cancel_one_pager(mock_data_access, document_store, draft_id, ADMIN, now=NOW)


@pytest.mark.unit
def test__admin_not_owner_or_sme__cancel__one_pager_cancelled(
    draft_id: str,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """An Admin may cancel any One Pager."""
    # When
    row = cancel_one_pager(
        mock_data_access,
        document_store,
        draft_id,
        ADMIN,
        roles=ADMIN_ROLES,
        now=NOW,
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
    document_store: OnePagerDocumentStore,
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
        cancel_one_pager(mock_data_access, document_store, draft_id, creator, now=NOW)
    assert mock_data_access.get_one_pager_status_row(draft_id).one_pager_status == op
