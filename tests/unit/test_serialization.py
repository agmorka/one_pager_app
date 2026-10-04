"""YAML ↔ OnePagerDocument conversion."""

from typing import Any

import pytest
import yaml

from onepagerapp.documents.serialization import (
    document_from_dict,
    document_to_dict,
    document_to_yaml,
)
from tests.helpers import FIXTURES_DIR

CREATED = {
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


def _approved_fixture() -> dict[str, Any]:
    """Return OP-0001 v1.0.0, a complete v2 document, as a dict."""
    path = FIXTURES_DIR / "OP-0001" / "OP-0001_v1.0.0.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.unit
def test__created_document__from_dict__audit_fields_read() -> None:
    """``createdBy`` and the change log are read."""
    # When
    document = document_from_dict(CREATED)

    # Then
    assert document.created_by == "A B"
    assert document.change_log[0]["summary"] == "Initial draft created"


@pytest.mark.unit
def test__created_document__round_trip__dict_and_yaml_unchanged() -> None:
    """Reading and writing gives the same dict and YAML content."""
    # Given
    document = document_from_dict(CREATED)

    # When
    as_dict, as_yaml = document_to_dict(document), document_to_yaml(document)

    # Then
    assert as_dict == CREATED
    assert yaml.safe_load(as_yaml) == CREATED


@pytest.mark.unit
def test__v2_sections__from_dict__every_section_read() -> None:
    """Every v2 section is read into its attribute."""
    # When
    document = document_from_dict(_approved_fixture())

    # Then
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


@pytest.mark.unit
def test__v2_sections__round_trip__dict_unchanged() -> None:
    """A complete v2 document survives a round trip."""
    # Given
    data = _approved_fixture()

    # When
    result = document_to_dict(document_from_dict(data))

    # Then
    assert result == data


@pytest.mark.unit
def test__empty_problem_statement__to_dict__omitted() -> None:
    """An empty problem statement is not written."""
    # Given
    document = document_from_dict(
        {"dataProduct": "x", "dataProductOwner": {}, "businessProblemStatement": ""}
    )

    # When
    data = document_to_dict(document)

    # Then
    assert "businessProblemStatement" not in data


@pytest.mark.unit
def test__legacy_data_element_key__round_trip__written_as_v2_key() -> None:
    """``dataElementPreview`` is read and written as ``dataProductPreview``."""
    # Given
    document = document_from_dict(
        {"dataProductOwner": {}, "dataElementPreview": [{"elementName": "x"}]}
    )

    # When
    data = document_to_dict(document)

    # Then
    assert document.data_product_preview == [{"elementName": "x"}]
    assert data["dataProductPreview"] == [{"elementName": "x"}]


@pytest.mark.unit
def test__classification_without_flags__from_dict__flags_not_defaulted() -> None:
    """Missing flags stay missing, so the strict tier can report them."""
    # When
    document = document_from_dict(
        {"dataProductOwner": {}, "dataClassification": {"classificationLevel": "x"}}
    )

    # Then
    assert document.data_classification == {"classificationLevel": "x"}


@pytest.mark.unit
def test__malformed_sections__round_trip__read_as_empty_and_not_written() -> None:
    """Wrongly shaped sections load as empty and are left out when written."""
    # Given
    data = {
        "dataProductOwner": {},
        "useCases": None,
        "outOfScope": ["keep", "", None],
        "openQuestions": "not a list",
        "dataGovernanceArtifacts": {"businessConcepts": None, "other": [1]},
    }

    # When
    document = document_from_dict(data)
    written = document_to_dict(document)

    # Then
    assert document.use_cases == []
    assert document.out_of_scope == ["keep"]
    assert document.open_questions == []
    assert document.data_governance_artifacts == {"businessConcepts": []}
    assert "useCases" not in written
    assert "dataGovernanceArtifacts" not in written
