"""Save Draft: lenient validation, MINOR bump, new version file, change log."""

from dataclasses import replace

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import LockNotHeldError, SaveError, bump_minor, working_copy
from onepagerapp.locking import release_lock
from onepagerapp.models import CurrentUser, OnePagerDocument, OnePagerStatusRow
from onepagerapp.permissions import PermissionDeniedError
from tests.helpers import ALICE, LATER, failing, failing_for_event, save


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", "0.2.0"), ("0.9.0", "0.10.0"), ("1.0.0", "1.1.0"), ("2.3.1", "2.4.0")],
)
def test__version__bump_minor__next_minor_with_patch_reset(
    version: str, expected: str
) -> None:
    """A save bumps the MINOR part and resets the patch."""
    # When
    result = bump_minor(version)

    # Then
    assert result == expected


@pytest.mark.unit
def test__invalid_version__bump_minor__raises_value_error() -> None:
    """A version without three parts is rejected."""
    # When / Then
    with pytest.raises(ValueError, match="Invalid version"):
        bump_minor("1.0")


@pytest.mark.unit
def test__edited_draft__save__new_minor_version_in_status_row(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A save bumps the version and records who saved when."""
    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert (result.ok, result.version) == (True, "0.2.0")
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert (row.version, row.one_pager_status) == ("0.2.0", "Draft")
    assert (row.last_updated_by, row.last_updated_at) == ("MJO", LATER)


@pytest.mark.unit
def test__edited_draft__save__writes_sanitized_new_version_file(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The new file holds the sanitized content and its change log entry."""
    # Given
    opened_draft.description = "  Unified <b>customer</b> view.  "
    opened_draft.business_problem_statement = "Data is scattered."

    # When
    save(mock_data_access, document_store, opened_draft, creator)

    # Then
    stored = document_store.read("OP-0003", "0.2.0")
    assert stored.description == "Unified customer view."
    assert stored.business_problem_statement == "Data is scattered."
    assert (stored.version, stored.one_pager_status) == ("0.2.0", "Draft")
    assert stored.change_log[-1]["summary"] == "Clarified the description"
    assert stored.change_log[-1]["version"] == "0.2.0"


@pytest.mark.unit
def test__edited_draft__save__keeps_previous_version_file(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Version files are immutable history."""
    # Given
    opened_draft.description = "Changed"

    # When
    save(mock_data_access, document_store, opened_draft, creator)

    # Then
    previous = document_store.read("OP-0003", "0.1.0")
    assert previous.description.startswith("Unified")


@pytest.mark.unit
def test__edited_draft__save__logs_content_save(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A save adds a content_save entry with the summary."""
    # When
    save(mock_data_access, document_store, opened_draft, creator)

    # Then
    entry = mock_data_access.get_change_log("OP-0003")[0]
    assert (entry.event_type, entry.version, entry.summary) == (
        "content_save",
        "0.2.0",
        "Clarified the description",
    )


@pytest.mark.unit
def test__saved_once__save_again__bumps_again(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Every save is a new MINOR version with its own change log entry."""
    # Given
    first = save(mock_data_access, document_store, opened_draft, creator)

    # When
    second = save(
        mock_data_access, document_store, working_copy(first.document), creator
    )

    # Then
    assert second.version == "0.3.0"
    assert len(second.document.change_log) == 3


@pytest.mark.unit
def test__operational_fields_changed_in_document__save__taken_from_status_row(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The data product and statuses cannot be changed by editing the document."""
    # Given
    opened_draft.data_product = "renamed"
    opened_draft.one_pager_status = "Approved"
    opened_draft.data_product_status = "Active"

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.document.data_product == "customer_master"
    assert result.document.one_pager_status == "Draft"
    assert result.document.data_product_status == "In Definition"


@pytest.mark.unit
@pytest.mark.parametrize("summary", ["", "   ", "<p></p>", "x" * 501])
def test__missing_or_too_long_summary__save__returns_summary_error(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    summary: str,
) -> None:
    """A change summary of 1 to 500 characters is required."""
    # When
    result = save(
        mock_data_access, document_store, opened_draft, creator, summary=summary
    )

    # Then
    assert not result.ok
    assert [e.field_path for e in result.errors] == ["changeSummary"]
    assert mock_data_access.get_one_pager_status_row("OP-0003").version == "0.1.0"


@pytest.mark.unit
def test__lenient_errors__save__returns_errors_and_writes_nothing(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Invalid basics are reported; no file and no change log entry are written."""
    # Given
    opened_draft.product_name = ""
    opened_draft.owner_email = "not-an-email"
    opened_draft.smes.append({"name": "Twice", "initials": "MJO", "email": "t@bec.dk"})

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    paths = {e.field_path for e in result.errors}
    assert {"productName", "dataProductOwner.email", "smes[1].initials"} <= paths
    assert not document_store.exists("OP-0003", "0.2.0")
    assert len(mock_data_access.get_change_log("OP-0003")) == 1


@pytest.mark.unit
def test__unknown_business_domain__save__returns_domain_error(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A domain that is not an active reference value is refused."""
    # Given
    opened_draft.business_domain = "Unknown"

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert [e.field_path for e in result.errors] == ["businessDomain"]


@pytest.mark.unit
def test__stored_domain_deactivated_since__save__accepted(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The domain already stored may be kept after it was deactivated."""
    # Given "Customer" is stored but no longer active

    # When
    result = save(
        mock_data_access, document_store, opened_draft, creator, allowed_domains=[]
    )

    # Then
    assert result.ok


@pytest.mark.unit
def test__owner_hands_over__save__status_row_has_new_owner(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The Owner may hand the One Pager to somebody else."""
    # Given
    opened_draft.owner_name, opened_draft.owner_initials = "Kim Hansen", "KHA"
    opened_draft.owner_email = "kha@bec.dk"
    opened_draft.smes = []

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert (row.owner_initials, row.owner_name) == ("KHA", "Kim Hansen")


@pytest.mark.unit
def test__user_not_owner_or_sme__save__raises_permission_denied(
    opened_draft: OnePagerDocument,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Only the Owner or an SME can save."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        save(mock_data_access, document_store, opened_draft, ALICE)


@pytest.mark.unit
def test__lock_held_by_other_session__save__raises_lock_not_held(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The lock must be held by the saving session."""
    # When / Then
    with pytest.raises(LockNotHeldError):
        save(
            mock_data_access,
            document_store,
            opened_draft,
            creator,
            session_id="other",
        )
    assert not document_store.exists("OP-0003", "0.2.0")


@pytest.mark.unit
def test__lock_released__save__raises_lock_not_held(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Saving after the lock was released is refused."""
    # Given
    release_lock(mock_data_access, "OP-0003", creator)

    # When / Then
    with pytest.raises(LockNotHeldError):
        save(mock_data_access, document_store, opened_draft, creator)
    assert not document_store.exists("OP-0003", "0.2.0")


@pytest.mark.unit
def test__change_log_write_fails__save__rolls_back(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed save keeps the old version and discards the new file."""
    # Given
    original = mock_data_access.append_change_log
    monkeypatch.setattr(
        mock_data_access,
        "append_change_log",
        failing_for_event(original, "content_save"),
    )

    # When / Then
    with pytest.raises(SaveError, match="changes are preserved"):
        save(mock_data_access, document_store, working_copy(opened_draft), creator)
    assert mock_data_access.get_one_pager_status_row("OP-0003").version == "0.1.0"
    assert not document_store.exists("OP-0003", "0.2.0")


@pytest.mark.unit
def test__save_failed_before__retry_after_recovery__saved(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed save leaves nothing that blocks the retry."""
    # Given a save that failed on the change log
    original = mock_data_access.append_change_log
    monkeypatch.setattr(
        mock_data_access,
        "append_change_log",
        failing_for_event(original, "content_save"),
    )
    with pytest.raises(SaveError):
        save(mock_data_access, document_store, working_copy(opened_draft), creator)
    monkeypatch.setattr(mock_data_access, "append_change_log", original)

    # When
    result = save(mock_data_access, document_store, working_copy(opened_draft), creator)

    # Then
    assert result.ok


@pytest.mark.unit
def test__status_update_fails__save__discards_file_and_logs_nothing(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed status update removes the new file before the change log."""
    # Given
    monkeypatch.setattr(mock_data_access, "update_one_pager_status", failing())

    # When / Then
    with pytest.raises(SaveError):
        save(mock_data_access, document_store, working_copy(opened_draft), creator)
    assert not document_store.exists("OP-0003", "0.2.0")
    assert len(mock_data_access.get_change_log("OP-0003")) == 1


@pytest.mark.unit
def test__row_changed_by_someone_else__save__raises_conflict(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent change of the status row makes the save a conflict."""
    # Given another writer moves the version just before the conditional update
    update = mock_data_access.update_one_pager_status

    def changed_meanwhile(row: OnePagerStatusRow, **kwargs: object) -> bool:
        rows = mock_data_access._status_rows
        rows["OP-0003"] = replace(rows["OP-0003"], version="0.5.0")
        return update(row, **kwargs)

    monkeypatch.setattr(mock_data_access, "update_one_pager_status", changed_meanwhile)

    # When / Then
    with pytest.raises(SaveError, match="changed by someone else"):
        save(mock_data_access, document_store, opened_draft, creator)
    assert not document_store.exists("OP-0003", "0.2.0")


@pytest.mark.unit
def test__leftover_file_from_crashed_save__save__file_replaced(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """An unreferenced file of the next version is replaced, not an error."""
    # Given
    document_store.write(
        "OP-0003", replace(opened_draft, description="orphan"), "0.2.0"
    )

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    assert document_store.read("OP-0003", "0.2.0").description != "orphan"
