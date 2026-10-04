"""Submit for Review: atomic Draft → Ready for Review → In Review (Req §6)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import LockNotHeldError
from onepagerapp.models import CurrentUser, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import TransitionError, submit_for_review
from tests.helpers import (
    ALICE,
    BOB,
    IN_REVIEW_ID,
    LATER,
    NOW,
    SESSION_ID,
    failing,
    fill_all_sections,
    save,
)


@pytest.fixture
def saved_draft(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> str:
    """Save the opened Draft complete as v0.2.0 (lock still held) and return its ID."""
    fill_all_sections(opened_draft)
    result = save(mock_data_access, document_store, opened_draft, creator, now=NOW)
    assert result.ok
    return "OP-0003"


@pytest.fixture
def incomplete_draft(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> str:
    """Save the opened Draft as v0.2.0 with sections missing and return its ID."""
    opened_draft.assumptions.append("Saved once")
    result = save(mock_data_access, document_store, opened_draft, creator, now=NOW)
    assert result.ok
    return "OP-0003"


@pytest.mark.unit
def test__complete_draft__submit__moves_to_in_review_without_version_bump(
    saved_draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Both transitions are applied at once; a status change keeps the version."""
    # When
    result = submit_for_review(
        mock_data_access, saved_draft, creator, SESSION_ID, now=LATER
    )

    # Then
    assert result.ok
    row = mock_data_access.get_one_pager_status_row(saved_draft)
    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "In Review",
        "In Definition",
        "0.2.0",
    )


@pytest.mark.unit
def test__complete_draft__submit__logs_both_transitions(
    saved_draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """The change log gets one entry per transition, newest first."""
    # When
    submit_for_review(mock_data_access, saved_draft, creator, SESSION_ID, now=LATER)

    # Then
    log = mock_data_access.get_change_log(saved_draft)[:2]
    assert [(e.from_status, e.to_status) for e in log] == [
        ("Ready for Review", "In Review"),
        ("Draft", "Ready for Review"),
    ]
    assert all(e.version == "0.2.0" for e in log)


@pytest.mark.unit
def test__complete_draft__submit__releases_the_lock(
    saved_draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Submitting releases the edit lock (Backend_Design.md §6)."""
    # When
    submit_for_review(mock_data_access, saved_draft, creator, SESSION_ID, now=LATER)

    # Then
    assert mock_data_access.get_lock(saved_draft) is None


@pytest.mark.unit
def test__incomplete_draft__submit__strict_errors_keep_draft_and_lock(
    incomplete_draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Strict validation errors block the submit and change nothing."""
    # When
    result = submit_for_review(
        mock_data_access, incomplete_draft, creator, SESSION_ID, now=LATER
    )

    # Then
    assert not result.ok
    assert {"useCases", "dataSources"} <= {e.field_path for e in result.errors}
    row = mock_data_access.get_one_pager_status_row(incomplete_draft)
    assert row.one_pager_status == "Draft"
    assert mock_data_access.get_lock(incomplete_draft) is not None


@pytest.mark.unit
def test__user_not_owner_or_sme__submit__raises_permission_denied(
    saved_draft: str, mock_data_access: MockDataAccess
) -> None:
    """Only the Owner or an SME can submit."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        submit_for_review(mock_data_access, saved_draft, ALICE, "s9", now=LATER)


@pytest.mark.unit
def test__owner_without_the_lock__submit__raises_lock_not_held(
    saved_draft: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Submitting needs the edit lock of the same session."""
    # When / Then
    with pytest.raises(LockNotHeldError, match="edit lock"):
        submit_for_review(mock_data_access, saved_draft, creator, "other", now=LATER)


@pytest.mark.unit
def test__one_pager_in_review__submit__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """Only a Draft or Draft Update can be submitted."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="In Review"):
        submit_for_review(mock_data_access, IN_REVIEW_ID, BOB, SESSION_ID, now=NOW)


@pytest.mark.unit
def test__change_log_write_fails__submit__rolls_back_and_keeps_lock(
    saved_draft: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed transition leaves the Draft and its lock as they were."""
    # Given
    monkeypatch.setattr(mock_data_access, "append_change_log_entries", failing())

    # When / Then
    with pytest.raises(TransitionError):
        submit_for_review(mock_data_access, saved_draft, creator, SESSION_ID, now=LATER)
    row = mock_data_access.get_one_pager_status_row(saved_draft)
    assert row.one_pager_status == "Draft"
    assert mock_data_access.get_lock(saved_draft) is not None
