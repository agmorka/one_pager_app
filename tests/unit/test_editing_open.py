"""Edit mode: per-record permission check and opening a One Pager for edit."""

import pytest

from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import DocumentMissingError, open_for_edit, working_copy
from onepagerapp.locking import LockStatus
from onepagerapp.models import AuthorizedUser, CurrentUser
from onepagerapp.permissions import (
    PermissionDeniedError,
    check_can_edit,
    edit_denied_reason,
    get_action_states,
    is_owner_or_sme,
)
from tests.helpers import (
    ALICE,
    BOB,
    IN_REVIEW_ID,
    LATER,
    NOW,
    SESSION_ID,
    make_user,
)


def _authorized(initials: str, role: str) -> list[AuthorizedUser]:
    """Return the authorized users of OP-0009: one person with ``role``."""
    return [AuthorizedUser("OP-0009", initials, initials, f"{initials}@x.dk", role)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("initials", "role", "expected"),
    [
        ("MJO", "owner", True),
        ("MJO", "sme", True),
        ("MJO", "viewer", False),
        ("ABR", "owner", False),
    ],
)
def test__authorized_users__is_owner_or_sme__matches_initials_and_role(
    creator: CurrentUser, initials: str, role: str, expected: bool
) -> None:
    """Only an Owner or SME row with the user's initials counts."""
    # When
    result = is_owner_or_sme(creator, _authorized(initials, role))

    # Then
    assert result is expected


@pytest.mark.unit
def test__no_user__is_owner_or_sme__false() -> None:
    """Without a user nobody is Owner or SME."""
    # When
    result = is_owner_or_sme(None, _authorized("MJO", "owner"))

    # Then
    assert result is False


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "Draft Update"])
def test__owner_of_editable_status__edit_denied_reason__none(
    creator: CurrentUser, status: str
) -> None:
    """A Draft or Draft Update can be edited by its Owner."""
    # When
    reason = edit_denied_reason(creator, status, _authorized("MJO", "owner"))

    # Then
    assert reason is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["Ready for Review", "In Review", "Approved", "Cancelled"]
)
def test__owner_of_other_status__edit_denied_reason__names_status(
    creator: CurrentUser, status: str
) -> None:
    """Other statuses are not editable; the reason names the status."""
    # When
    reason = edit_denied_reason(creator, status, _authorized("MJO", "owner"))

    # Then
    assert reason is not None
    assert status in reason


@pytest.mark.unit
def test__user_not_owner_or_sme__check_can_edit__raises_and_logs(
    creator: CurrentUser, caplog: pytest.LogCaptureFixture
) -> None:
    """A denied edit raises and is logged as a security event."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        check_can_edit(creator, "OP-0009", "Draft", _authorized("ABR", "owner"))
    assert "permission_denied" in caplog.text
    assert "OP-0009" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user", "status", "holder", "enabled"),
    [
        ("MJO", "Draft", None, True),
        ("DPR", "Draft Update", None, True),
        ("MJO", "Draft", "MJO", True),
        ("MJO", "Draft", "DPR", False),
        ("ABR", "Draft", None, False),
        ("MJO", "In Review", None, False),
    ],
)
def test__user_status_and_lock__action_states__edit_enabled_for_owner_sme_draft(
    user: str, status: str, holder: str | None, enabled: bool
) -> None:
    """Edit is enabled for the Owner/SME of an editable, not foreign-locked record."""
    # When
    states = get_action_states(
        user,
        "MJO",
        status,
        holder is not None,
        holder,
        authorized_initials={"MJO", "DPR"},
    )

    # Then
    assert states["edit"].enabled is enabled


@pytest.mark.unit
def test__owners_draft__open_for_edit__acquires_lock_and_reads_document(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Opening takes the lock and returns the current version."""
    # When
    session = open_for_edit(mock_data_access, draft_id, creator, SESSION_ID, now=NOW)

    # Then
    assert session.one_pager_id == draft_id
    assert session.lock.status is LockStatus.ACQUIRED
    assert session.status_row.version == "0.1.0"
    assert session.document.product_name == "Customer Master Data"
    assert mock_data_access.get_lock(draft_id).locked_by_initials == "MJO"


@pytest.mark.unit
def test__draft_locked_by_sme__owner_opens__not_acquired(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Another user's lock blocks editing."""
    # Given
    open_for_edit(mock_data_access, draft_id, make_user("DPR"), "other", now=NOW)

    # When
    session = open_for_edit(mock_data_access, draft_id, creator, SESSION_ID, now=LATER)

    # Then
    assert not session.lock.acquired
    assert session.lock.status is LockStatus.LOCKED_BY_OTHER


@pytest.mark.unit
def test__user_not_owner_or_sme__open_for_edit__raises_and_takes_no_lock(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """An unauthorized user gets no lock."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        open_for_edit(mock_data_access, draft_id, ALICE, SESSION_ID, now=NOW)
    assert mock_data_access.get_lock(draft_id) is None


@pytest.mark.unit
def test__one_pager_in_review__open_for_edit__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """A One Pager In Review is not editable, not even by its Owner."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="In Review"):
        open_for_edit(mock_data_access, IN_REVIEW_ID, BOB, SESSION_ID, now=NOW)


@pytest.mark.unit
def test__unknown_id__open_for_edit__raises_not_found(
    creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """An unknown One Pager cannot be opened."""
    # When / Then
    with pytest.raises(NotFoundError):
        open_for_edit(mock_data_access, "OP-9999", creator, SESSION_ID, now=NOW)


@pytest.mark.unit
def test__version_file_missing__open_for_edit__raises_document_missing(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """The current version's YAML file must exist."""
    # Given
    mock_data_access._status_rows[draft_id].version = "0.9.0"

    # When / Then
    with pytest.raises(DocumentMissingError):
        open_for_edit(mock_data_access, draft_id, creator, SESSION_ID, now=NOW)


@pytest.mark.unit
def test__opened_document__change_working_copy__original_unchanged(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """The working copy is a deep copy."""
    # Given
    session = open_for_edit(mock_data_access, draft_id, creator, SESSION_ID, now=NOW)

    # When
    working_copy(session.document).smes.append({"name": "X"})

    # Then
    assert len(session.document.smes) == 1
