import pytest
import yaml

from onepagerapp.documents.serialization import (
    document_from_dict,
    document_to_dict,
    document_to_yaml,
)
from onepagerapp.documents.store import OnePagerDocumentStore
from tests.conftest import FIXTURES_DIR


@pytest.mark.unit
def test__new_fields_round_trip() -> None:
    data = {
        "structureDefinition": "structure_one_pager_v_1.json",
        "dataProduct": "customer_master",
        "productName": "Customer Master",
        "businessDomain": "Customer",
        "dataProductType": "Foundational",
        "onePagerStatus": "Draft",
        "dataProductStatus": "In Definition",
        "version": "0.1.0",
        "dataProductOwner": {"name": "A B", "initials": "ABR", "email": "a@b.dk"},
        "description": "d",
        "createdBy": "A B",
        "createdAt": "2026-09-29T10:00:00+00:00",
        "lastUpdated": "2026-09-29T10:00:00+00:00",
        "changeLog": [
            {
                "version": "0.1.0",
                "date": "2026-09-29T10:00:00+00:00",
                "author": "A B",
                "summary": "Initial draft created",
            }
        ],
    }
    document = document_from_dict(data)
    assert document.created_by == "A B"
    assert document.change_log[0]["summary"] == "Initial draft created"
    assert document_to_dict(document) == data
    assert yaml.safe_load(document_to_yaml(document)) == data


@pytest.mark.unit
def test__empty_business_problem_statement_is_omitted() -> None:
    document = document_from_dict(
        {"dataProduct": "x", "dataProductOwner": {}, "businessProblemStatement": ""}
    )
    assert "businessProblemStatement" not in document_to_dict(document)


@pytest.mark.unit
def test__fixtures_still_load() -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR)
    assert store.read("OP-0001", "1.0.0").product_name == "Person Master Data"
    assert store.read("OP-0002", "0.3.0").data_product == "order"


@pytest.mark.unit
def test__v2_sections_round_trip() -> None:
    path = FIXTURES_DIR / "OP-0001" / "OP-0001_v1.0.0.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    document = document_from_dict(data)

    assert document.use_case_ids == ["UC-001", "UC-002"]
    assert document.business_requirements[0]["id"] == "BR-001"
    assert document.data_product_preview[0]["useCaseLinks"] == ["UC-001", "UC-002"]
    assert document.retention_requirements[0]["retentionPeriod"].startswith("7 years")
    assert set(document.data_governance_artifacts) == {
        "businessConcepts",
        "cdeQuality",
        "cdeLineage",
    }
    assert document.out_of_scope[0].startswith("Corporate customers")
    assert document.open_questions[0]["status"] == "Answered"
    assert document.assumptions == [
        "SAP ERP remains the system of record for employees"
    ]
    assert document_to_dict(document) == data


@pytest.mark.unit
def test__legacy_data_element_key_is_read() -> None:
    document = document_from_dict(
        {"dataProductOwner": {}, "dataElementPreview": [{"elementName": "x"}]}
    )
    assert document.data_product_preview == [{"elementName": "x"}]
    assert document_to_dict(document)["dataProductPreview"] == [{"elementName": "x"}]


@pytest.mark.unit
def test__classification_flags_are_not_defaulted() -> None:
    document = document_from_dict(
        {"dataProductOwner": {}, "dataClassification": {"classificationLevel": "x"}}
    )
    assert document.data_classification == {"classificationLevel": "x"}


@pytest.mark.unit
def test__malformed_sections_load_as_empty() -> None:
    document = document_from_dict(
        {
            "dataProductOwner": {},
            "useCases": None,
            "outOfScope": ["keep", "", None],
            "openQuestions": "not a list",
            "dataGovernanceArtifacts": {"businessConcepts": None, "other": [1]},
        }
    )
    assert document.use_cases == []
    assert document.out_of_scope == ["keep"]
    assert document.open_questions == []
    assert document.data_governance_artifacts == {"businessConcepts": []}
    written = document_to_dict(document)
    assert "useCases" not in written
    assert "dataGovernanceArtifacts" not in written
