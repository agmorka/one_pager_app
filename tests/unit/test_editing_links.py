"""Use Case links (Backend §9), BR IDs and saving a complete document."""

from datetime import UTC, datetime, timedelta

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.editing import (
    assign_requirement_id,
    link_use_case,
    open_for_edit,
    save_draft,
    unlink_use_case,
    validate_new_use_case_links,
    working_copy,
)
from onepagerapp.models import CurrentUser, NewOnePagerInput, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.validation import validate_strict
from onepagerapp.workflow import create_one_pager

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def doc(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> OnePagerDocument:
    create_one_pager(valid_input, creator, mock_data_access, document_store, now=NOW)
    opened = open_for_edit(mock_data_access, "OP-0003", creator, "s1", now=NOW)
    return working_copy(opened.document)


def _save(data_access, store, doc, user):  # noqa: ANN001, ANN202
    return save_draft(
        data_access,
        store,
        "OP-0003",
        doc,
        "Filled in all sections",
        user,
        "s1",
        allowed_domains=["Customer"],
        allowed_types=["Foundational"],
        now=NOW + timedelta(minutes=1),
    )


def _deprecate(data_access: MockDataAccess, use_case_id: str) -> None:
    data_access.set_use_case_deprecated(use_case_id, True, "AB")


@pytest.mark.unit
def test__validate_new_links__unknown_deprecated_and_duplicates(
    mock_data_access: MockDataAccess,
) -> None:
    _deprecate(mock_data_access, "UC-003")
    errors = validate_new_use_case_links(
        mock_data_access, [], ["UC-001", "UC-001", "UC-003", "UC-999"]
    )
    messages = [e.message for e in errors]
    assert messages == [
        "UC-001 is linked twice.",
        "UC-003 is deprecated and cannot be linked.",
        "UC-999 does not exist.",
    ]


@pytest.mark.unit
def test__validate_new_links__existing_deprecated_link_stays(
    mock_data_access: MockDataAccess,
) -> None:
    _deprecate(mock_data_access, "UC-002")
    assert validate_new_use_case_links(mock_data_access, ["UC-002"], ["UC-002"]) == []


@pytest.mark.unit
def test__link_and_unlink__require_owner_or_sme(
    doc: OnePagerDocument, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    link_use_case(mock_data_access, "OP-0003", "UC-001", creator)
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == ["UC-001"]
    assert "OP-0003" in mock_data_access.get_use_case_references("UC-001")

    stranger = resolve_current_user("alice.brown@company.com")
    with pytest.raises(PermissionDeniedError):
        link_use_case(mock_data_access, "OP-0003", "UC-002", stranger)
    with pytest.raises(PermissionDeniedError):
        unlink_use_case(mock_data_access, "OP-0003", "UC-001", stranger)

    unlink_use_case(mock_data_access, "OP-0003", "UC-001", creator)
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == []


@pytest.mark.unit
def test__link__refuses_deprecated(
    doc: OnePagerDocument, creator: CurrentUser, mock_data_access: MockDataAccess
) -> None:
    _deprecate(mock_data_access, "UC-001")
    with pytest.raises(ValueError, match="deprecated"):
        link_use_case(mock_data_access, "OP-0003", "UC-001", creator)


@pytest.mark.unit
def test__assign_requirement_id__uses_global_counter(
    mock_data_access: MockDataAccess,
) -> None:
    first, second = {"requirement": "a"}, {"requirement": "b"}
    assert assign_requirement_id(mock_data_access, first) == "BR-001"
    assert assign_requirement_id(mock_data_access, second) == "BR-002"
    assert first["id"] == "BR-001"


@pytest.mark.unit
def test__save__syncs_use_case_references(
    doc: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    doc.use_cases = [{"useCaseId": "UC-001"}, {"useCaseId": "UC-002"}]
    assert _save(mock_data_access, document_store, doc, creator).ok
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == ["UC-001", "UC-002"]

    doc.use_cases = [{"useCaseId": "UC-002"}]
    assert _save(mock_data_access, document_store, doc, creator).ok
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == ["UC-002"]


@pytest.mark.unit
def test__save__refuses_newly_linked_deprecated_use_case(
    doc: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    _deprecate(mock_data_access, "UC-001")
    doc.use_cases = [{"useCaseId": "UC-001"}]
    result = _save(mock_data_access, document_store, doc, creator)
    assert [e.field_path for e in result.errors] == ["useCases"]
    assert mock_data_access.get_linked_use_case_ids("OP-0003") == []


def fill_all_sections(doc: OnePagerDocument) -> None:
    """Content that satisfies the strict tier, as the editor tabs produce it."""
    doc.business_problem_statement = "Customer data is scattered."
    doc.use_cases = [{"useCaseId": "UC-001"}]
    doc.business_requirements = [
        {"id": "BR-001", "requirement": "Daily refresh", "priority": "High"}
    ]
    doc.data_sources = [{"name": "CRM", "dataProvided": "Customer master data"}]
    doc.data_product_preview = [
        {
            "elementName": "customer_id",
            "dataType": "STRING",
            "isPrimaryKey": True,
            "containsPII": False,
            "isCriticalDataElement": True,
            "cdeCriticalityTiering": "Tier 1",
            "description": "Customer key",
            "useCaseLinks": ["UC-001"],
        },
        {
            "elementName": "segment",
            "dataType": "STRING",
            "isPrimaryKey": False,
            "containsPII": False,
            "isCriticalDataElement": False,
            "description": "Segment",
        },
    ]
    doc.data_classification = {
        "classificationLevel": "Internal",
        "containsPII": True,
        "containsSensitiveData": False,
    }
    doc.retention_requirements = [
        {"dataCategory": "Customer", "retentionPeriod": "5 years"}
    ]
    doc.data_governance_artifacts = {
        "businessConcepts": [{"name": "Customer", "definition": "A client"}],
        "cdeQuality": [
            {"elementName": "customer_id", "dimension": "Uniqueness", "rule": "Unique"}
        ],
        "cdeLineage": [],
    }
    doc.out_of_scope = ["Prospects"]
    doc.open_questions = [{"question": "Which CRM?", "status": "Open"}]
    doc.assumptions = ["CRM is the master"]


@pytest.mark.unit
def test__save__complete_document_passes_strict_validation(
    doc: OnePagerDocument,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    fill_all_sections(doc)

    result = _save(mock_data_access, document_store, doc, creator)

    assert result.ok
    stored = document_store.read("OP-0003", result.version)
    assert validate_strict(document_to_dict(stored)) == []
    assert stored.out_of_scope == ["Prospects"]
    assert stored.data_governance_artifacts["cdeQuality"][0]["dimension"] == (
        "Uniqueness"
    )
    assert "cdeLineage" not in stored.data_governance_artifacts
