"""Submit for Review: atomic Draft → Ready for Review → In Review (Req §6)."""

from datetime import UTC, datetime, timedelta

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import (
    LockNotHeldError,
    open_for_edit,
    save_draft,
    working_copy,
)
from onepagerapp.models import CurrentUser, NewOnePagerInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import TransitionError, create_one_pager, submit_for_review
from tests.unit.test_editing_links import fill_all_sections

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


def _prepare(
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
    valid_input: NewOnePagerInput,
    user: CurrentUser,
    *,
    complete: bool = True,
) -> None:
    """Create OP-0003, open it (lock "s1") and save a (complete) version 0.2.0."""
    create_one_pager(valid_input, user, data_access, store, now=NOW)
    doc = working_copy(
        open_for_edit(data_access, "OP-0003", user, "s1", now=NOW).document
    )
    if complete:
        fill_all_sections(doc)
    doc.assumptions.append("Saved once")
    result = save_draft(
        data_access,
        store,
        "OP-0003",
        doc,
        "Filled in",
        user,
        "s1",
        allowed_domains=["Customer"],
        allowed_types=["Foundational"],
        now=NOW,
    )
    assert result.ok


@pytest.mark.unit
def test__submit__moves_to_in_review_atomically(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    _prepare(mock_data_access, document_store, valid_input, creator)

    result = submit_for_review(mock_data_access, "OP-0003", creator, "s1", now=LATER)

    assert result.ok
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert (row.one_pager_status, row.data_product_status) == (
        "In Review",
        "In Definition",
    )
    assert row.version == "0.2.0"  # pure status change: no version bump
    log = mock_data_access.get_change_log("OP-0003")
    assert [(e.from_status, e.to_status) for e in log[:2]] == [
        ("Ready for Review", "In Review"),
        ("Draft", "Ready for Review"),
    ]
    assert all(e.version == "0.2.0" for e in log[:2])
    # Submitting releases the lock (Backend_Design.md §6).
    assert mock_data_access.get_lock("OP-0003") is None


@pytest.mark.unit
def test__submit__strict_validation_blocks(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    _prepare(mock_data_access, document_store, valid_input, creator, complete=False)

    result = submit_for_review(mock_data_access, "OP-0003", creator, "s1", now=LATER)

    assert not result.ok
    assert {"useCases", "dataSources"} <= {e.field_path for e in result.errors}
    assert (
        mock_data_access.get_one_pager_status_row("OP-0003").one_pager_status == "Draft"
    )
    assert mock_data_access.get_lock("OP-0003") is not None


@pytest.mark.unit
def test__submit__requires_owner_or_sme_and_the_lock(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    _prepare(mock_data_access, document_store, valid_input, creator)

    stranger = resolve_current_user("alice.brown@company.com")
    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        submit_for_review(mock_data_access, "OP-0003", stranger, "s9", now=LATER)
    with pytest.raises(LockNotHeldError, match="edit lock"):
        submit_for_review(mock_data_access, "OP-0003", creator, "other", now=LATER)


@pytest.mark.unit
def test__submit__only_from_draft_or_draft_update(
    mock_data_access: MockDataAccess,
) -> None:
    owner = resolve_current_user("bob.smith@company.com")  # OP-0002 is In Review
    with pytest.raises(PermissionDeniedError, match="In Review"):
        submit_for_review(mock_data_access, "OP-0002", owner, "s1", now=NOW)


class _ChangeLogFails(MockDataAccess):
    def append_change_log_entries(self, entries) -> None:  # noqa: ANN001, ARG002
        msg = "boom"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__submit__failure_rolls_back_and_keeps_lock(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _ChangeLogFails(document_store)
    _prepare(data_access, document_store, valid_input, creator)

    with pytest.raises(TransitionError):
        submit_for_review(data_access, "OP-0003", creator, "s1", now=LATER)

    assert data_access.get_one_pager_status_row("OP-0003").one_pager_status == "Draft"
    assert data_access.get_lock("OP-0003") is not None
