"""Update: Approved → Draft Update (Req §5, Backend §2, §6)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit, working_copy
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    ConfirmationRequiredError,
    TransitionError,
    approve_one_pager,
    reject_one_pager,
    start_update,
    submit_for_review,
)
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    NOW,
    SESSION_ID,
    fill_all_sections,
    save,
    update_status_row,
)


@pytest.mark.unit
def test__approved_one_pager__confirmed_update__draft_update_keeps_version_and_dp(
    mock_data_access: MockDataAccess,
) -> None:
    """Update reopens the One Pager without touching the version or DP status."""
    # When
    row = start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True, now=NOW)

    # Then
    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Draft Update",
        "Ready for Development",
        "1.0.0",
    )
    entry = mock_data_access.get_change_log(APPROVED_ID)[0]
    assert (entry.from_status, entry.to_status, entry.version) == (
        "Approved",
        "Draft Update",
        "1.0.0",
    )


@pytest.mark.unit
def test__approved_one_pager__confirmed_update__takes_no_lock(
    mock_data_access: MockDataAccess,
) -> None:
    """The lock is taken later, when the Editor opens."""
    # When
    start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True, now=NOW)

    # Then
    assert mock_data_access.get_lock(APPROVED_ID) is None


@pytest.mark.unit
def test__approved_one_pager__unconfirmed_update__raises_and_keeps_status(
    mock_data_access: MockDataAccess,
) -> None:
    """Update needs an explicit confirmation."""
    # When / Then
    with pytest.raises(ConfirmationRequiredError):
        start_update(mock_data_access, APPROVED_ID, ALICE)
    row = mock_data_access.get_one_pager_status_row(APPROVED_ID)
    assert row.one_pager_status == "Approved"


@pytest.mark.unit
def test__user_not_owner_or_sme__update__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """Only the Owner or an SME can start an update."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        start_update(mock_data_access, APPROVED_ID, APPROVER, confirmed=True)


@pytest.mark.unit
def test__one_pager_in_draft_update__update_again__raises_invalid_transition(
    mock_data_access: MockDataAccess,
) -> None:
    """Update is only possible from Approved."""
    # Given
    start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)

    # When / Then
    with pytest.raises(InvalidTransitionError):
        start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)


@pytest.mark.unit
def test__approved_document_missing__update__raises_and_keeps_status(
    mock_data_access: MockDataAccess,
) -> None:
    """The Approved version file must exist to be copied for editing."""
    # Given
    update_status_row(mock_data_access, APPROVED_ID, version="3.0.0")

    # When / Then
    with pytest.raises(TransitionError, match="could not be found"):
        start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)
    row = mock_data_access.get_one_pager_status_row(APPROVED_ID)
    assert row.one_pager_status == "Approved"


@pytest.mark.unit
def test__draft_update_saved_and_submitted__approve__next_major_in_enhancement(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """A full update cycle ends in the next MAJOR version and keeps the old file."""
    # Given an update that is edited, saved and submitted
    start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True, now=NOW)
    session = open_for_edit(mock_data_access, APPROVED_ID, ALICE, SESSION_ID, now=NOW)
    doc = working_copy(session.document)
    fill_all_sections(doc)
    saved = save(
        mock_data_access,
        document_store,
        doc,
        ALICE,
        one_pager_id=APPROVED_ID,
        now=NOW,
    )
    assert saved.ok, saved.errors
    assert saved.version == "1.1.0"
    submitted = submit_for_review(
        mock_data_access, APPROVED_ID, ALICE, SESSION_ID, now=NOW
    )
    assert submitted.ok, submitted.errors

    # When
    row = approve_one_pager(
        mock_data_access,
        document_store,
        APPROVED_ID,
        APPROVER,
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Approved",
        "In Enhancement",
        "2.0.0",
    )
    assert document_store.exists(APPROVED_ID, "1.0.0")
    assert document_store.exists(APPROVED_ID, "2.0.0")


@pytest.mark.unit
def test__update_in_review__reject__returns_to_draft_update(
    mock_data_access: MockDataAccess,
) -> None:
    """A rejected update goes back to Draft Update and keeps the DP status."""
    # Given
    update_status_row(
        mock_data_access,
        APPROVED_ID,
        one_pager_status="In Review",
        data_product_status="Active",
    )

    # When
    row = reject_one_pager(
        mock_data_access,
        APPROVED_ID,
        APPROVER,
        "Keep the old lineage",
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert (row.one_pager_status, row.data_product_status) == ("Draft Update", "Active")
    entry = mock_data_access.get_change_log(APPROVED_ID)[0]
    assert entry.summary == "Update rejected: Keep the old lineage"
