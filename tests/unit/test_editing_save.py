"""Save Draft: lenient validation, MINOR bump, new version file, change log."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import (
    LockNotHeldError,
    SaveError,
    bump_minor,
    open_for_edit,
    save_draft,
    working_copy,
)
from onepagerapp.locking import release_lock
from onepagerapp.models import CurrentUser, NewOnePagerInput, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import create_one_pager

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
SESSION = "s1"
DOMAINS = ["Customer", "Sales"]
TYPES = ["Foundational", "Integrated", "Augmented"]


@pytest.fixture
def draft(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> OnePagerDocument:
    """OP-0003 created by MJO and opened in the editor (lock held by SESSION)."""
    create_one_pager(valid_input, creator, mock_data_access, document_store, now=NOW)
    session = open_for_edit(mock_data_access, "OP-0003", creator, SESSION, now=NOW)
    return working_copy(session.document)


def _save(  # noqa: ANN202
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
    document: OnePagerDocument,
    user: CurrentUser,
    summary: str = "Clarified the description",
    session_id: str = SESSION,
):
    return save_draft(
        data_access,
        store,
        "OP-0003",
        document,
        summary,
        user,
        session_id,
        allowed_domains=DOMAINS,
        allowed_types=TYPES,
        now=LATER,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", "0.2.0"), ("0.9.0", "0.10.0"), ("1.0.0", "1.1.0"), ("2.3.1", "2.4.0")],
)
def test__bump_minor(version: str, expected: str) -> None:
    assert bump_minor(version) == expected


@pytest.mark.unit
def test__bump_minor__rejects_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid version"):
        bump_minor("1.0")


@pytest.mark.unit
def test__save_draft__happy_path(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    draft.description = "  Unified <b>customer</b> view.  "
    draft.business_problem_statement = "Data is scattered."

    result = _save(mock_data_access, document_store, draft, creator)

    assert result.ok
    assert result.version == "0.2.0"
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert row.version == "0.2.0"
    assert row.last_updated_by == "MJO"
    assert row.last_updated_at == LATER
    assert row.one_pager_status == "Draft"

    stored = document_store.read("OP-0003", "0.2.0")
    assert stored.description == "Unified customer view."
    assert stored.business_problem_statement == "Data is scattered."
    assert stored.version == "0.2.0"
    assert stored.one_pager_status == "Draft"
    assert stored.change_log[-1]["summary"] == "Clarified the description"
    assert stored.change_log[-1]["version"] == "0.2.0"
    # The previous version file is kept unchanged (immutable history).
    assert document_store.read("OP-0003", "0.1.0").description.startswith("Unified")

    entry = mock_data_access.get_change_log("OP-0003")[0]
    assert entry.event_type == "content_save"
    assert entry.version == "0.2.0"
    assert entry.summary == "Clarified the description"


@pytest.mark.unit
def test__save_draft__second_save_bumps_again(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    first = _save(mock_data_access, document_store, draft, creator)
    second = _save(
        mock_data_access, document_store, working_copy(first.document), creator
    )
    assert second.version == "0.3.0"
    assert len(second.document.change_log) == 3


@pytest.mark.unit
def test__save_draft__operational_fields_come_from_delta(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    draft.data_product = "renamed"
    draft.one_pager_status = "Approved"
    draft.data_product_status = "Active"

    result = _save(mock_data_access, document_store, draft, creator)

    assert result.document.data_product == "customer_master"
    assert result.document.one_pager_status == "Draft"
    assert result.document.data_product_status == "In Definition"


@pytest.mark.unit
@pytest.mark.parametrize("summary", ["", "   ", "<p></p>", "x" * 501])
def test__save_draft__summary_required(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    summary: str,
) -> None:
    result = _save(mock_data_access, document_store, draft, creator, summary=summary)

    assert not result.ok
    assert [e.field_path for e in result.errors] == ["changeSummary"]
    assert mock_data_access.get_one_pager_status_row("OP-0003").version == "0.1.0"


@pytest.mark.unit
def test__save_draft__lenient_errors_write_nothing(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    draft.product_name = ""
    draft.owner_email = "not-an-email"
    draft.smes.append({"name": "Twice", "initials": "MJO", "email": "t@bec.dk"})

    result = _save(mock_data_access, document_store, draft, creator)

    paths = {e.field_path for e in result.errors}
    assert {"productName", "dataProductOwner.email", "smes[1].initials"} <= paths
    assert not document_store.exists("OP-0003", "0.2.0")
    assert len(mock_data_access.get_change_log("OP-0003")) == 1


@pytest.mark.unit
def test__save_draft__rejects_inactive_domain_but_keeps_stored_one(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    draft.business_domain = "Unknown"
    result = _save(mock_data_access, document_store, draft, creator)
    assert [e.field_path for e in result.errors] == ["businessDomain"]

    draft.business_domain = "Customer"
    result = save_draft(
        mock_data_access,
        document_store,
        "OP-0003",
        draft,
        "Kept",
        creator,
        SESSION,
        allowed_domains=[],  # "Customer" was deactivated since
        allowed_types=TYPES,
        now=LATER,
    )
    assert result.ok


@pytest.mark.unit
def test__save_draft__owner_can_hand_over(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    draft.owner_name, draft.owner_initials = "Kim Hansen", "KHA"
    draft.owner_email = "kha@bec.dk"
    draft.smes = []

    result = _save(mock_data_access, document_store, draft, creator)

    assert result.ok
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert (row.owner_initials, row.owner_name) == ("KHA", "Kim Hansen")


@pytest.mark.unit
def test__save_draft__not_authorized(
    draft: OnePagerDocument,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    stranger = resolve_current_user("alice.brown@company.com")
    with pytest.raises(PermissionDeniedError):
        _save(mock_data_access, document_store, draft, stranger)


@pytest.mark.unit
def test__save_draft__requires_the_lock(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    with pytest.raises(LockNotHeldError):
        _save(mock_data_access, document_store, draft, creator, session_id="other")

    release_lock(mock_data_access, "OP-0003", creator)
    with pytest.raises(LockNotHeldError):
        _save(mock_data_access, document_store, draft, creator)
    assert not document_store.exists("OP-0003", "0.2.0")


class _ChangeLogFails(MockDataAccess):
    fail = True

    def append_change_log(self, entry) -> None:  # noqa: ANN001
        if self.fail and entry.event_type == "content_save":
            msg = "boom"
            raise RuntimeError(msg)
        super().append_change_log(entry)


@pytest.mark.unit
def test__save_draft__change_log_failure_rolls_back_and_retry_works(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _ChangeLogFails(document_store)
    create_one_pager(valid_input, creator, data_access, document_store, now=NOW)
    doc = open_for_edit(data_access, "OP-0003", creator, SESSION, now=NOW).document

    with pytest.raises(SaveError, match="changes are preserved"):
        _save(data_access, document_store, working_copy(doc), creator)

    assert data_access.get_one_pager_status_row("OP-0003").version == "0.1.0"
    assert not document_store.exists("OP-0003", "0.2.0")

    data_access.fail = False
    assert _save(data_access, document_store, working_copy(doc), creator).ok


class _StatusUpdateFails(MockDataAccess):
    def update_one_pager_status(self, row, **kwargs) -> bool:  # noqa: ANN001, ANN003, ARG002
        msg = "boom"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__save_draft__status_update_failure_discards_file(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _StatusUpdateFails(document_store)
    create_one_pager(valid_input, creator, data_access, document_store, now=NOW)
    doc = open_for_edit(data_access, "OP-0003", creator, SESSION, now=NOW).document

    with pytest.raises(SaveError):
        _save(data_access, document_store, working_copy(doc), creator)
    assert not document_store.exists("OP-0003", "0.2.0")
    assert len(data_access.get_change_log("OP-0003")) == 1


@pytest.mark.unit
def test__save_draft__concurrent_change_is_a_conflict(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    original = mock_data_access.update_one_pager_status

    def changed_meanwhile(row, **kwargs) -> bool:  # noqa: ANN001, ANN003
        mock_data_access._status_rows["OP-0003"] = replace(
            mock_data_access._status_rows["OP-0003"], version="0.5.0"
        )
        return original(row, **kwargs)

    mock_data_access.update_one_pager_status = changed_meanwhile
    with pytest.raises(SaveError, match="changed by someone else"):
        _save(mock_data_access, document_store, draft, creator)
    assert not document_store.exists("OP-0003", "0.2.0")


@pytest.mark.unit
def test__save_draft__leftover_file_from_crashed_save_is_replaced(
    draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    orphan = replace(draft, description="orphan")
    document_store.write("OP-0003", orphan, "0.2.0")

    result = _save(mock_data_access, document_store, draft, creator)

    assert result.ok
    assert document_store.read("OP-0003", "0.2.0").description != "orphan"
