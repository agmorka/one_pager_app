"""Use Case links (Backend §9), BR IDs and saving a complete document."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.editing import (
    assign_requirement_id,
    link_use_case,
    unlink_use_case,
    validate_new_use_case_links,
)
from onepagerapp.models import CurrentUser, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.validation import validate_strict
from tests.helpers import ALICE, fill_all_sections, save


def _deprecate(data_access: MockDataAccess, use_case_id: str) -> None:
    """Deprecate a seeded Use Case."""
    data_access.set_use_case_deprecated(use_case_id, True, "ABR")


@pytest.mark.unit
def test__unknown_deprecated_and_duplicate_links__validate_new_links__one_error_each(
    mock_data_access: MockDataAccess,
) -> None:
    """New links must be unique, existing and not deprecated."""
    # Given
    _deprecate(mock_data_access, "UC-003")

    # When
    errors = validate_new_use_case_links(
        mock_data_access, [], ["UC-001", "UC-001", "UC-003", "UC-999"]
    )

    # Then
    assert [e.message for e in errors] == [
        "UC-001 is linked twice.",
        "UC-003 is deprecated and cannot be linked.",
        "UC-999 does not exist.",
    ]


@pytest.mark.unit
def test__existing_link_to_deprecated_use_case__validate_new_links__no_error(
    mock_data_access: MockDataAccess,
) -> None:
    """A link made before the deprecation may stay."""
    # Given
    _deprecate(mock_data_access, "UC-002")

    # When
    errors = validate_new_use_case_links(mock_data_access, ["UC-002"], ["UC-002"])

    # Then
    assert errors == []


@pytest.mark.unit
def test__owners_draft__link_use_case__reference_recorded(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Linking records the reference on both sides."""
    # When
    link_use_case(mock_data_access, draft_id, "UC-001", creator)

    # Then
    assert mock_data_access.get_linked_use_case_ids(draft_id) == ["UC-001"]
    assert draft_id in mock_data_access.get_use_case_references("UC-001")


@pytest.mark.unit
def test__linked_use_case__unlink_use_case__reference_removed(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """Unlinking removes the reference."""
    # Given
    link_use_case(mock_data_access, draft_id, "UC-001", creator)

    # When
    unlink_use_case(mock_data_access, draft_id, "UC-001", creator)

    # Then
    assert mock_data_access.get_linked_use_case_ids(draft_id) == []


@pytest.mark.unit
@pytest.mark.parametrize("change", [link_use_case, unlink_use_case])
def test__user_not_owner_or_sme__change_link__raises_permission_denied(
    draft_id: str, mock_data_access: MockDataAccess, change: object
) -> None:
    """Only the Owner or an SME can change Use Case links."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        change(mock_data_access, draft_id, "UC-001", ALICE)  # type: ignore[operator]


@pytest.mark.unit
def test__deprecated_use_case__link_use_case__raises_value_error(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    """A deprecated Use Case cannot be linked."""
    # Given
    _deprecate(mock_data_access, "UC-001")

    # When / Then
    with pytest.raises(ValueError, match="deprecated"):
        link_use_case(mock_data_access, draft_id, "UC-001", creator)


@pytest.mark.unit
def test__two_requirements__assign_requirement_id__global_counter_ids(
    mock_data_access: MockDataAccess,
) -> None:
    """Requirement IDs come from one global counter and are set on the item."""
    # Given
    first, second = {"requirement": "a"}, {"requirement": "b"}

    # When
    ids = [
        assign_requirement_id(mock_data_access, first),
        assign_requirement_id(mock_data_access, second),
    ]

    # Then
    assert ids == ["BR-001", "BR-002"]
    assert first["id"] == "BR-001"


@pytest.mark.unit
def test__use_cases_in_document__save__references_added(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Saving links the Use Cases of the document."""
    # Given
    opened_draft.use_cases = [{"useCaseId": "UC-001"}, {"useCaseId": "UC-002"}]

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == ["UC-001", "UC-002"]


@pytest.mark.unit
def test__use_case_dropped_from_document__save__reference_removed(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Saving unlinks a Use Case that is no longer in the document."""
    # Given
    opened_draft.use_cases = [{"useCaseId": "UC-001"}, {"useCaseId": "UC-002"}]
    assert save(mock_data_access, document_store, opened_draft, creator).ok
    opened_draft.use_cases = [{"useCaseId": "UC-002"}]

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == ["UC-002"]


@pytest.mark.unit
def test__newly_linked_deprecated_use_case__save__returns_error_and_no_link(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A save that newly links a deprecated Use Case is refused."""
    # Given
    _deprecate(mock_data_access, "UC-001")
    opened_draft.use_cases = [{"useCaseId": "UC-001"}]

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert [e.field_path for e in result.errors] == ["useCases"]
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == []


@pytest.mark.unit
def test__all_sections_filled__save__stored_document_passes_strict(
    opened_draft: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A complete document is stored with every section; empty lists drop out."""
    # Given
    fill_all_sections(opened_draft)

    # When
    result = save(mock_data_access, document_store, opened_draft, creator)

    # Then
    assert result.ok
    stored = document_store.read("OP-0003", result.version)
    assert validate_strict(document_to_dict(stored)) == []
    assert stored.out_of_scope == ["Prospects"]
    governance = stored.data_governance_artifacts
    assert governance["cdeQuality"][0]["dimension"] == "Uniqueness"
    assert "cdeLineage" not in governance
