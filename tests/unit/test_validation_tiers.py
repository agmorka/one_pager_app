"""Tests for the lenient and strict tiers, business rules and schema versions.

Covers Backend_Design.md §4 and Data_Model.md §6.
"""

import copy
from typing import Any

import pytest
import yaml

from onepagerapp.models import ValidationError
from onepagerapp.validation import (
    CDE_ONLY_MESSAGE,
    CURRENT_STRUCTURE_DEFINITION,
    RETENTION_REQUIRED_MESSAGE,
    SUPPORTED_STRUCTURE_DEFINITIONS,
    load_lenient_schema,
    load_schema,
    validate_business_rules,
    validate_lenient,
    validate_schema,
    validate_strict,
)
from tests.conftest import FIXTURES_DIR

V1 = "structure_one_pager_v_1.json"


def _fixture(one_pager_id: str, version: str) -> dict[str, Any]:
    path = FIXTURES_DIR / one_pager_id / f"{one_pager_id}_v{version}.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def complete() -> dict[str, Any]:
    """Return a complete v2 document that passes the strict tier."""
    return _fixture("OP-0001", "1.0.0")


def _minimal_draft() -> dict[str, Any]:
    return {
        "structureDefinition": CURRENT_STRUCTURE_DEFINITION,
        "productName": "Customer Master",
        "description": "Unified customer view",
    }


def _paths(errors: list[ValidationError]) -> set[str]:
    return {e.field_path for e in errors}


# ============================================================================
# Lenient tier
# ============================================================================


@pytest.mark.unit
def test__lenient__product_name_and_description_are_enough() -> None:
    assert validate_lenient(_minimal_draft()) == []


@pytest.mark.unit
@pytest.mark.parametrize("field", ["productName", "description"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test__lenient__product_name_and_description_required(
    field: str, value: str | None
) -> None:
    draft = _minimal_draft()
    draft[field] = value
    assert field in _paths(validate_lenient(draft))


@pytest.mark.unit
def test__lenient__empty_sections_and_partial_items_are_fine() -> None:
    draft = {
        **_minimal_draft(),
        "useCases": [],
        "businessRequirements": [{"id": "BR-001"}],
        "dataSources": [{"name": ""}],
        "dataProductPreview": [{"elementName": "x"}],
        "dataClassification": {},
        "openQuestions": [{"question": "Who owns it?"}],
    }
    assert validate_lenient(draft) == []


@pytest.mark.unit
def test__lenient__wrong_shapes_are_still_rejected() -> None:
    draft = {
        **_minimal_draft(),
        "useCases": "UC-001",
        "businessRequirements": [{"id": "REQ-1"}],
        "dataClassification": {"classificationLevel": "Top Secret"},
    }
    errors = validate_lenient(draft)
    assert ValidationError("useCases", "Must be of type array.") in errors
    assert (
        ValidationError("businessRequirements[0].id", "Has an invalid format.")
        in errors
    )
    assert "dataClassification.classificationLevel" in _paths(errors)


@pytest.mark.unit
def test__lenient__length_limits_still_apply() -> None:
    draft = {**_minimal_draft(), "productName": "x" * 201}
    assert "productName" in _paths(validate_lenient(draft))


@pytest.mark.unit
def test__lenient_schema__keeps_property_named_like_a_keyword() -> None:
    lenient = load_lenient_schema(CURRENT_STRUCTURE_DEFINITION)
    assert "required" not in lenient
    assert "minItems" not in lenient["properties"]["useCases"]
    # The strict schema itself is untouched (cached object not mutated).
    assert "required" in load_schema(CURRENT_STRUCTURE_DEFINITION)


# ============================================================================
# Strict tier
# ============================================================================


@pytest.mark.unit
def test__strict__complete_document_passes(complete: dict[str, Any]) -> None:
    assert validate_strict(complete) == []


@pytest.mark.unit
def test__strict__minimal_draft_lists_every_missing_section() -> None:
    errors = validate_strict(_minimal_draft())
    assert {
        "dataProduct",
        "businessDomain",
        "dataProductType",
        "dataProductOwner",
        "businessProblemStatement",
        "useCases",
        "businessRequirements",
        "dataSources",
        "dataProductPreview",
        "dataClassification",
    } <= _paths(errors)
    assert all(e.message == "This field is required." for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "section",
    ["useCases", "businessRequirements", "dataSources", "dataProductPreview"],
)
def test__strict__sections_need_at_least_one_item(
    complete: dict[str, Any], section: str
) -> None:
    complete[section] = []
    assert ValidationError(section, "Add at least 1 item.") in validate_strict(complete)


@pytest.mark.unit
def test__strict__nested_required_fields_get_bracketed_paths(
    complete: dict[str, Any],
) -> None:
    del complete["businessRequirements"][1]["requirement"]
    complete["dataSources"][0]["dataProvided"] = ""
    errors = validate_strict(complete)
    assert (
        ValidationError(
            "businessRequirements[1].requirement", "This field is required."
        )
        in errors
    )
    assert (
        ValidationError("dataSources[0].dataProvided", "This field is required.")
        in errors
    )


@pytest.mark.unit
def test__strict__empty_business_problem_statement_fails(
    complete: dict[str, Any],
) -> None:
    complete["businessProblemStatement"] = ""
    assert "businessProblemStatement" in _paths(validate_strict(complete))


@pytest.mark.unit
def test__strict__missing_classification_flags_are_reported(
    complete: dict[str, Any],
) -> None:
    complete["dataClassification"] = {"classificationLevel": "Public"}
    assert {
        "dataClassification.containsPII",
        "dataClassification.containsSensitiveData",
    } <= _paths(validate_strict(complete))


@pytest.mark.unit
def test__strict__runs_schema_and_business_rules_together(
    complete: dict[str, Any],
) -> None:
    complete["useCases"] = []
    complete["retentionRequirements"] = []
    assert {"useCases", "retentionRequirements"} <= _paths(validate_strict(complete))


# ============================================================================
# Conditional business rules
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "classification",
    [
        {"classificationLevel": "Public", "containsPII": True},
        {"classificationLevel": "Public", "containsSensitiveData": True},
        {"classificationLevel": "Internal"},
        {"classificationLevel": "Restricted"},
    ],
)
def test__rules__retention_required(classification: dict[str, Any]) -> None:
    document = {"dataClassification": classification}
    assert validate_business_rules(document) == [
        ValidationError("retentionRequirements", RETENTION_REQUIRED_MESSAGE)
    ]


@pytest.mark.unit
def test__rules__retention_not_required_for_public_non_sensitive_data() -> None:
    document = {
        "dataClassification": {
            "classificationLevel": "Public",
            "containsPII": False,
            "containsSensitiveData": False,
        }
    }
    assert validate_business_rules(document) == []


@pytest.mark.unit
def test__rules__retention_list_satisfies_rule() -> None:
    document = {
        "dataClassification": {"classificationLevel": "Internal"},
        "retentionRequirements": [
            {"dataCategory": "All", "retentionPeriod": "5 years"}
        ],
    }
    assert validate_business_rules(document) == []


@pytest.mark.unit
def test__rules__v1_retention_string_satisfies_rule() -> None:
    document = {
        "dataClassification": {
            "classificationLevel": "Internal",
            "retentionRequirements": "7 years",
        }
    }
    assert validate_business_rules(document) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [("cdeCriticalityTiering", "Tier 1"), ("useCaseLinks", ["UC-001"])],
)
def test__rules__cde_fields_only_on_cdes(field: str, value: object) -> None:
    element = {"elementName": "x", "isCriticalDataElement": False, field: value}
    errors = validate_business_rules({"dataProductPreview": [{}, element]})
    assert errors == [
        ValidationError(f"dataProductPreview[1].{field}", CDE_ONLY_MESSAGE)
    ]


@pytest.mark.unit
def test__rules__cde_fields_allowed_on_cdes_and_blank_on_others() -> None:
    elements = [
        {
            "isCriticalDataElement": True,
            "cdeCriticalityTiering": "Tier 1",
            "useCaseLinks": ["UC-001"],
        },
        {
            "isCriticalDataElement": False,
            "cdeCriticalityTiering": None,
            "useCaseLinks": [],
        },
        {"cdeCriticalityTiering": ""},
    ]
    assert validate_business_rules({"dataProductPreview": elements}) == []


@pytest.mark.unit
def test__rules__legacy_data_element_key_is_checked() -> None:
    element = {"isCriticalDataElement": False, "cdeCriticalityTiering": "Tier 1"}
    errors = validate_business_rules({"dataElementPreview": [element]})
    assert _paths(errors) == {"dataElementPreview[0].cdeCriticalityTiering"}


@pytest.mark.unit
def test__rules__duplicate_business_requirement_ids() -> None:
    requirements = [{"id": "BR-001"}, {"id": "BR-002"}, {"id": "BR-001"}]
    errors = validate_business_rules({"businessRequirements": requirements})
    assert errors == [
        ValidationError(
            "businessRequirements[2].id",
            "Requirement ID BR-001 is used more than once.",
        )
    ]


# ============================================================================
# Schema evolution (13.7)
# ============================================================================


@pytest.mark.unit
def test__versions__every_supported_schema_loads() -> None:
    for name in SUPPORTED_STRUCTURE_DEFINITIONS:
        assert load_schema(name)["$schema"].startswith("http://json-schema.org/")


@pytest.mark.unit
def test__versions__v1_document_is_validated_against_v1() -> None:
    v1_document = _fixture("OP-0001", "0.2.0")
    assert v1_document["structureDefinition"] == V1
    assert validate_schema(v1_document) == []
    assert validate_strict(v1_document) == []
    # The same content does not meet v2 (useCases hold IDs there, etc.).
    assert validate_schema(v1_document, CURRENT_STRUCTURE_DEFINITION) != []


@pytest.mark.unit
def test__versions__declared_version_decides_the_rules(
    complete: dict[str, Any],
) -> None:
    del complete["useCases"]
    v1_labelled = {**copy.deepcopy(complete), "structureDefinition": V1}
    v1_labelled["businessRequirements"] = [{"id": "BR-001", "description": "x"}]
    # v2 requires useCases; v1 does not.
    assert "useCases" in _paths(validate_strict(complete))
    assert "useCases" not in _paths(validate_strict(v1_labelled))


@pytest.mark.unit
def test__versions__path_prefix_is_ignored() -> None:
    document = {**_minimal_draft(), "structureDefinition": f"schemas/{V1}"}
    assert validate_lenient(document) == []


@pytest.mark.unit
def test__versions__missing_definition_uses_current_schema() -> None:
    draft = _minimal_draft()
    del draft["structureDefinition"]
    assert validate_lenient(draft) == []
    assert "useCases" in _paths(validate_strict(draft))


@pytest.mark.unit
@pytest.mark.parametrize(
    "validate", [validate_lenient, validate_strict, validate_schema]
)
def test__versions__unsupported_definition_is_an_error(validate: object) -> None:
    document = {**_minimal_draft(), "structureDefinition": "../../etc/passwd"}
    errors = validate(document)  # type: ignore[operator]
    assert "structureDefinition" in _paths(errors)
    assert "Unsupported structure definition" in errors[-1].message
