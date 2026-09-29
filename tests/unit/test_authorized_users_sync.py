"""Sync of one_pager_authorized_users with the Owner/SMEs on save (Backend §11)."""

from datetime import UTC, datetime, timedelta

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import (
    SaveError,
    diff_authorized_users,
    open_for_edit,
    save_draft,
    working_copy,
)
from onepagerapp.models import (
    AuthorizedUser,
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
)
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import create_one_pager

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _user(initials: str, role: str = "sme", name: str = "N") -> AuthorizedUser:
    return AuthorizedUser("OP-0003", initials, name, f"{initials}@bec.dk", role)


def _rows(data_access: MockDataAccess) -> dict[str, str]:
    return {
        u.user_initials: u.role for u in data_access.get_authorized_users("OP-0003")
    }


@pytest.mark.unit
def test__diff__insert_update_delete() -> None:
    current = [_user("MJO", "owner"), _user("DPR"), _user("OLD")]
    desired = [_user("MJO", "owner"), _user("DPR", name="Renamed"), _user("NEW")]

    diff = diff_authorized_users(current, desired)

    assert [u.user_initials for u in diff.inserts] == ["NEW"]
    assert [u.user_initials for u in diff.updates] == ["DPR"]
    assert diff.deletes == ["OLD"]


@pytest.mark.unit
def test__diff__role_change_is_an_update() -> None:
    diff = diff_authorized_users(
        [_user("MJO", "owner"), _user("DPR")], [_user("DPR", "owner"), _user("MJO")]
    )
    assert {u.user_initials: u.role for u in diff.updates} == {
        "DPR": "owner",
        "MJO": "sme",
    }
    assert not diff.inserts
    assert not diff.deletes


@pytest.mark.unit
def test__diff__no_change_is_empty() -> None:
    users = [_user("MJO", "owner"), _user("DPR")]
    assert diff_authorized_users(users, list(users)).empty


def _save(data_access, store, doc, user, now=NOW + timedelta(minutes=1)):  # noqa: ANN001, ANN202
    return save_draft(
        data_access,
        store,
        "OP-0003",
        doc,
        "Changed the team",
        user,
        "s1",
        allowed_domains=["Customer"],
        allowed_types=["Foundational"],
        now=now,
    )


@pytest.fixture
def opened(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> OnePagerDocument:
    create_one_pager(valid_input, creator, mock_data_access, document_store, now=NOW)
    doc = open_for_edit(mock_data_access, "OP-0003", creator, "s1", now=NOW).document
    return working_copy(doc)


@pytest.mark.unit
def test__save__syncs_authorized_users(
    opened,  # noqa: ANN001
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    assert _rows(mock_data_access) == {"MJO": "owner", "DPR": "sme"}
    opened.smes = [
        {"name": "Kim Hansen", "initials": "kha", "email": "kha@bec.dk"},
    ]

    assert _save(mock_data_access, document_store, opened, creator).ok

    assert _rows(mock_data_access) == {"MJO": "owner", "KHA": "sme"}


@pytest.mark.unit
def test__save__removed_user_loses_edit_access(
    opened,  # noqa: ANN001
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    # MJO hands the One Pager over to DPR and leaves the SME list.
    opened.owner_name, opened.owner_initials = "Diana Prince", "DPR"
    opened.owner_email = "diana@bec.dk"
    opened.smes = []
    assert _save(mock_data_access, document_store, opened, creator).ok
    assert _rows(mock_data_access) == {"DPR": "owner"}

    with pytest.raises(PermissionDeniedError):
        _save(mock_data_access, document_store, opened, creator)

    sme = resolve_current_user("dpr@bec.dk")
    assert open_for_edit(
        mock_data_access, "OP-0003", sme, "s2", now=NOW + timedelta(hours=1)
    ).lock.acquired


class _ChangeLogFails(MockDataAccess):
    def append_change_log(self, entry) -> None:  # noqa: ANN001
        if entry.event_type == "content_save":
            msg = "boom"
            raise RuntimeError(msg)
        super().append_change_log(entry)


@pytest.mark.unit
def test__save__failure_restores_authorized_users(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _ChangeLogFails(document_store)
    create_one_pager(valid_input, creator, data_access, document_store, now=NOW)
    doc = working_copy(
        open_for_edit(data_access, "OP-0003", creator, "s1", now=NOW).document
    )
    doc.smes = [{"name": "Kim Hansen", "initials": "KHA", "email": "kha@bec.dk"}]

    with pytest.raises(SaveError):
        _save(data_access, document_store, doc, creator)

    assert _rows(data_access) == {"MJO": "owner", "DPR": "sme"}
