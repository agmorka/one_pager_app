"""Edit mode: per-record permission check and opening a One Pager for edit."""

from datetime import UTC, datetime, timedelta

import pytest

from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import DocumentMissingError, open_for_edit, working_copy
from onepagerapp.locking import LockStatus
from onepagerapp.models import AuthorizedUser, CurrentUser, NewOnePagerInput
from onepagerapp.permissions import (
    PermissionDeniedError,
    check_can_edit,
    edit_denied_reason,
    get_action_states,
    is_owner_or_sme,
)
from onepagerapp.workflow import create_one_pager
from tests.users import CREATOR_ROLES, make_user

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _authorized(*pairs: tuple[str, str]) -> list[AuthorizedUser]:
    return [
        AuthorizedUser("OP-0009", initials, initials, f"{initials}@x.dk", role)
        for initials, role in pairs
    ]


@pytest.fixture
def draft_id(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store,  # noqa: ANN001
) -> str:
    result = create_one_pager(
        valid_input,
        creator,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )
    assert result.one_pager_id
    return result.one_pager_id


@pytest.mark.unit
def test__is_owner_or_sme__matches_initials_and_role(creator: CurrentUser) -> None:
    assert is_owner_or_sme(creator, _authorized(("MJO", "owner")))
    assert is_owner_or_sme(creator, _authorized(("MJO", "sme")))
    assert not is_owner_or_sme(creator, _authorized(("MJO", "viewer")))
    assert not is_owner_or_sme(creator, _authorized(("ABR", "owner")))
    assert not is_owner_or_sme(None, _authorized(("MJO", "owner")))


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "Draft Update"])
def test__edit_denied_reason__editable_statuses(
    creator: CurrentUser, status: str
) -> None:
    assert edit_denied_reason(creator, status, _authorized(("MJO", "owner"))) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["Ready for Review", "In Review", "Approved", "Cancelled"]
)
def test__edit_denied_reason__other_statuses(creator: CurrentUser, status: str) -> None:
    reason = edit_denied_reason(creator, status, _authorized(("MJO", "owner")))
    assert reason is not None
    assert status in reason


@pytest.mark.unit
def test__check_can_edit__denial_is_logged(
    creator: CurrentUser, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        check_can_edit(creator, "OP-0009", "Draft", _authorized(("ABR", "owner")))
    assert "permission_denied" in caplog.text
    assert "OP-0009" in caplog.text


@pytest.mark.unit
def test__action_states__edit_enabled_for_owner_sme_of_draft() -> None:
    def edit(user: str, status: str, holder: str | None = None) -> bool:
        return get_action_states(
            user,
            "MJO",
            status,
            holder is not None,
            holder,
            authorized_initials={"MJO", "DPR"},
        )["edit"].enabled

    assert edit("MJO", "Draft")
    assert edit("DPR", "Draft Update")
    assert edit("MJO", "Draft", holder="MJO")
    assert not edit("MJO", "Draft", holder="DPR")
    assert not edit("ABR", "Draft")
    assert not edit("MJO", "In Review")


@pytest.mark.unit
def test__open_for_edit__acquires_lock_and_reads_document(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    session = open_for_edit(mock_data_access, draft_id, creator, "s1", now=NOW)

    assert session.one_pager_id == draft_id
    assert session.lock.status is LockStatus.ACQUIRED
    assert session.status_row.version == "0.1.0"
    assert session.document.product_name == "Customer Master Data"
    assert mock_data_access.get_lock(draft_id).locked_by_initials == "MJO"


@pytest.mark.unit
def test__open_for_edit__locked_by_other_user(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    sme = make_user("DPR")
    open_for_edit(mock_data_access, draft_id, sme, "other", now=NOW)

    session = open_for_edit(
        mock_data_access, draft_id, creator, "s1", now=NOW + timedelta(minutes=1)
    )

    assert not session.lock.acquired
    assert session.lock.status is LockStatus.LOCKED_BY_OTHER


@pytest.mark.unit
def test__open_for_edit__not_authorized_takes_no_lock(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    stranger = make_user("ABR", "Alice Brown")
    with pytest.raises(PermissionDeniedError):
        open_for_edit(mock_data_access, draft_id, stranger, "s1", now=NOW)
    assert mock_data_access.get_lock(draft_id) is None


@pytest.mark.unit
def test__open_for_edit__status_not_editable(mock_data_access: MockDataAccess) -> None:
    owner = make_user("BSM", "Bob Smith")  # OP-0002 is In Review
    with pytest.raises(PermissionDeniedError, match="In Review"):
        open_for_edit(mock_data_access, "OP-0002", owner, "s1", now=NOW)


@pytest.mark.unit
def test__open_for_edit__unknown_id(
    creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    with pytest.raises(NotFoundError):
        open_for_edit(mock_data_access, "OP-9999", creator, "s1", now=NOW)


@pytest.mark.unit
def test__open_for_edit__missing_document(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    mock_data_access._status_rows[draft_id].version = "0.9.0"
    with pytest.raises(DocumentMissingError):
        open_for_edit(mock_data_access, draft_id, creator, "s1", now=NOW)


@pytest.mark.unit
def test__working_copy__is_independent(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    session = open_for_edit(mock_data_access, draft_id, creator, "s1", now=NOW)
    copy = working_copy(session.document)
    copy.smes.append({"name": "X"})
    assert len(session.document.smes) == 1
