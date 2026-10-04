"""Sync of one_pager_authorized_users with the Owner/SMEs on save (Backend §11)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import SaveError, diff_authorized_users, open_for_edit
from onepagerapp.models import AuthorizedUser, CurrentUser, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from tests.helpers import LATER, failing_for_event, make_user, save


def _user(initials: str, role: str = "sme", name: str = "N") -> AuthorizedUser:
    """Return an authorized user of OP-0003."""
    return AuthorizedUser("OP-0003", initials, name, f"{initials}@bec.dk", role)


def _rows(data_access: MockDataAccess) -> dict[str, str]:
    """Return the stored authorized users of OP-0003 as initials → role."""
    users = data_access.get_authorized_users("OP-0003")
    return {u.user_initials: u.role for u in users}


@pytest.mark.unit
def test__added_renamed_and_removed_users__diff__insert_update_delete() -> None:
    """New users are inserted, changed ones updated and missing ones deleted."""
    # Given
    current = [_user("MJO", "owner"), _user("DPR"), _user("OLD")]
    desired = [_user("MJO", "owner"), _user("DPR", name="Renamed"), _user("NEW")]

    # When
    diff = diff_authorized_users(current, desired)

    # Then
    assert [u.user_initials for u in diff.inserts] == ["NEW"]
    assert [u.user_initials for u in diff.updates] == ["DPR"]
    assert diff.deletes == ["OLD"]


@pytest.mark.unit
def test__roles_swapped__diff__updates_only() -> None:
    """A role change is an update."""
    # Given
    current = [_user("MJO", "owner"), _user("DPR")]
    desired = [_user("DPR", "owner"), _user("MJO")]

    # When
    diff = diff_authorized_users(current, desired)

    # Then
    assert {u.user_initials: u.role for u in diff.updates} == {
        "DPR": "owner",
        "MJO": "sme",
    }
    assert not diff.inserts
    assert not diff.deletes


@pytest.mark.unit
def test__same_users__diff__empty() -> None:
    """Nothing to do when the users did not change."""
    # Given
    users = [_user("MJO", "owner"), _user("DPR")]

    # When
    diff = diff_authorized_users(users, list(users))

    # Then
    assert diff.empty


@pytest.mark.unit
def test__sme_replaced__save__authorized_users_follow(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Saving replaces the SME rows; initials are upper-cased."""
    # Given
    assert _rows(mock_data_access) == {"MJO": "owner", "DPR": "sme"}
    opened_draft.smes = [{"name": "Kim Hansen", "initials": "kha", "email": "k@b.dk"}]

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    assert _rows(mock_data_access) == {"MJO": "owner", "KHA": "sme"}


def _hand_over_to_dpr(document: OnePagerDocument) -> None:
    """Make DPR the Owner and remove every SME (MJO leaves the One Pager)."""
    document.owner_name, document.owner_initials = "Diana Prince", "DPR"
    document.owner_email = "diana@bec.dk"
    document.smes = []


@pytest.mark.unit
def test__owner_hands_over_and_leaves__save__only_new_owner_authorized(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The former Owner's row is removed."""
    # Given
    _hand_over_to_dpr(opened_draft)

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    assert _rows(mock_data_access) == {"DPR": "owner"}


@pytest.mark.unit
def test__former_owner_removed__save_again__raises_permission_denied(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A user removed from the One Pager loses edit access at once."""
    # Given
    _hand_over_to_dpr(opened_draft)
    save(mock_data_access, document_store, opened_draft, creator)

    # When / Then
    with pytest.raises(PermissionDeniedError):
        save(mock_data_access, document_store, opened_draft, creator)


@pytest.mark.unit
def test__new_owner_after_hand_over__open_for_edit__lock_acquired(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The new Owner can edit once the former Owner closed the editor."""
    # Given
    _hand_over_to_dpr(opened_draft)
    save(mock_data_access, document_store, opened_draft, creator)
    later = LATER.replace(hour=LATER.hour + 1)

    # When
    session = open_for_edit(
        mock_data_access, "OP-0003", make_user("DPR"), "s2", now=later
    )

    # Then
    assert session.lock.acquired


@pytest.mark.unit
def test__change_log_write_fails__save__authorized_users_restored(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed save rolls the authorized users back."""
    # Given
    original = mock_data_access.append_change_log
    monkeypatch.setattr(
        mock_data_access,
        "append_change_log",
        failing_for_event(original, "content_save"),
    )
    opened_draft.smes = [{"name": "Kim Hansen", "initials": "KHA", "email": "k@b.dk"}]

    # When / Then
    with pytest.raises(SaveError):
        save(mock_data_access, document_store, opened_draft, creator)
    assert _rows(mock_data_access) == {"MJO": "owner", "DPR": "sme"}
