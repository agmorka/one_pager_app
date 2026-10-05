"""Tests for the lenient and strict tiers, business rules and schema versions.

Covers Backend_Design.md §4 and Data_Model.md §6.
"""

import copy
from collections.abc import Callable
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
from tests.helpers import FIXTURES_DIR

V1 = "structure_one_pager_v_1.json"
REQUIRED = "This field is required."


def _fixture(one_pager_id: str, version: str) -> dict[str, Any]:
    """Return a fixture document as a dict."""
    path = FIXTURES_DIR / "one_pagers" / one_pager_id / f"{one_pager_id}_v{version}.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _minimal_draft(**fields: Any) -> dict[str, Any]:  # noqa: ANN401 - any field
    """Return the smallest valid Draft, with ``fields`` added or replaced."""
    return {
        "structureDefinition": CURRENT_STRUCTURE_DEFINITION,
        "productName": "Customer Master",
        "description": "Unified customer view",
        "businessProblemStatement": "Customer data is scattered.",
        **fields,
    }


def _paths(errors: list[ValidationError]) -> set[str]:
    """Return the field paths of ``errors``."""
    return {e.field_path for e in errors}


@pytest.fixture
def complete() -> dict[str, Any]:
    """Return a complete v2 document that passes the strict tier."""
    return _fixture("OP-0001", "1.0.0")


# ============================================================================
# Lenient tier
# ============================================================================


@pytest.mark.unit
def test__name_description_and_problem_only__validate_lenient__no_errors() -> None:
    """A Draft needs only a product name, a description and the problem."""
    # When
    errors = validate_lenient(_minimal_draft())

    # Then
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "field", ["productName", "description", "businessProblemStatement"]
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test__blank_name_description_or_problem__validate_lenient__field_error(
    field: str, value: str | None
) -> None:
    """Product name, description and problem statement must not be blank."""
    # When
    errors = validate_lenient(_minimal_draft(**{field: value}))

    # Then
    assert field in _paths(errors)


@pytest.mark.unit
def test__empty_sections_and_partial_items__validate_lenient__no_errors() -> None:
    """Sections may be empty and items incomplete while drafting."""
    # Given
    draft = _minimal_draft(
        useCases=[],
        businessRequirements=[{"id": "BR-001"}],
        dataSources=[{"name": ""}],
        dataProductPreview=[{"elementName": "x"}],
        dataClassification={},
        openQuestions=[{"question": "Who owns it?"}],
    )

    # When
    errors = validate_lenient(draft)

    # Then
    assert errors == []


@pytest.mark.unit
def test__wrong_shapes__validate_lenient__still_rejected() -> None:
    """Types, formats and enums are checked in the lenient tier too."""
    # Given
    draft = _minimal_draft(
        useCases="UC-001",
        businessRequirements=[{"id": "REQ-1"}],
        dataClassification={"classificationLevel": "Top Secret"},
    )

    # When
    errors = validate_lenient(draft)

    # Then
    assert ValidationError("useCases", "Must be of type array.") in errors
    assert (
        ValidationError("businessRequirements[0].id", "Has an invalid format.")
        in errors
    )
    assert "dataClassification.classificationLevel" in _paths(errors)


@pytest.mark.unit
def test__name_too_long__validate_lenient__length_error() -> None:
    """Length limits apply in the lenient tier."""
    # When
    errors = validate_lenient(_minimal_draft(productName="x" * 201))

    # Then
    assert "productName" in _paths(errors)


@pytest.mark.unit
def test__current_schema__load_lenient_schema__required_removed_strict_untouched() -> (
    None
):
    """The lenient schema drops required/minItems; the cached strict one keeps them."""
    # When
    lenient = load_lenient_schema(CURRENT_STRUCTURE_DEFINITION)

    # Then
    assert "required" not in lenient
    assert "minItems" not in lenient["properties"]["useCases"]
    assert "required" in load_schema(CURRENT_STRUCTURE_DEFINITION)


# ============================================================================
# Strict tier
# ============================================================================


@pytest.mark.unit
def test__complete_document__validate_strict__no_errors(
    complete: dict[str, Any],
) -> None:
    """A complete document passes."""
    # When
    errors = validate_strict(complete)

    # Then
    assert errors == []


@pytest.mark.unit
def test__minimal_draft__validate_strict__every_missing_section_required() -> None:
    """Each missing section is reported as required."""
    # Given
    draft = _minimal_draft()
    del draft["businessProblemStatement"]

    # When
    errors = validate_strict(draft)

    # Then
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
    assert all(e.message == REQUIRED for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "section",
    ["useCases", "businessRequirements", "dataSources", "dataProductPreview"],
)
def test__empty_list_section__validate_strict__needs_one_item(
    complete: dict[str, Any], section: str
) -> None:
    """List sections need at least one item."""
    # Given
    complete[section] = []

    # When
    errors = validate_strict(complete)

    # Then
    assert ValidationError(section, "Add at least 1 item.") in errors


@pytest.mark.unit
def test__missing_nested_fields__validate_strict__bracketed_paths(
    complete: dict[str, Any],
) -> None:
    """Nested errors carry the item index in their path."""
    # Given
    del complete["businessRequirements"][1]["requirement"]
    complete["dataSources"][0]["dataProvided"] = ""

    # When
    errors = validate_strict(complete)

    # Then
    assert ValidationError("businessRequirements[1].requirement", REQUIRED) in errors
    assert ValidationError("dataSources[0].dataProvided", REQUIRED) in errors


@pytest.mark.unit
def test__empty_problem_statement__validate_strict__error(
    complete: dict[str, Any],
) -> None:
    """An empty problem statement does not count."""
    # Given
    complete["businessProblemStatement"] = ""

    # When
    errors = validate_strict(complete)

    # Then
    assert "businessProblemStatement" in _paths(errors)


@pytest.mark.unit
def test__classification_without_flags__validate_strict__both_flags_reported(
    complete: dict[str, Any],
) -> None:
    """PII and sensitive-data flags are required."""
    # Given
    complete["dataClassification"] = {"classificationLevel": "Public"}

    # When
    errors = validate_strict(complete)

    # Then
    assert {
        "dataClassification.containsPII",
        "dataClassification.containsSensitiveData",
    } <= _paths(errors)


@pytest.mark.unit
def test__schema_and_rule_errors__validate_strict__both_reported(
    complete: dict[str, Any],
) -> None:
    """The strict tier runs the schema and the business rules together."""
    # Given
    complete["useCases"] = []
    complete["retentionRequirements"] = []

    # When
    errors = validate_strict(complete)

    # Then
    assert {"useCases", "retentionRequirements"} <= _paths(errors)


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
def test__non_public_or_sensitive_data__business_rules__retention_required(
    classification: dict[str, Any],
) -> None:
    """Retention is required unless the data is public and not sensitive."""
    # When
    errors = validate_business_rules({"dataClassification": classification})

    # Then
    assert errors == [
        ValidationError("retentionRequirements", RETENTION_REQUIRED_MESSAGE)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        {
            "dataClassification": {
                "classificationLevel": "Public",
                "containsPII": False,
                "containsSensitiveData": False,
            }
        },
        {
            "dataClassification": {"classificationLevel": "Internal"},
            "retentionRequirements": [
                {"dataCategory": "All", "retentionPeriod": "5 years"}
            ],
        },
        {
            "dataClassification": {
                "classificationLevel": "Internal",
                "retentionRequirements": "7 years",
            }
        },
    ],
    ids=["public-non-sensitive", "retention-list", "v1-retention-string"],
)
def test__retention_not_needed_or_given__business_rules__no_errors(
    document: dict[str, Any],
) -> None:
    """Public non-sensitive data needs no retention; a v2 list or v1 string counts."""
    # When
    errors = validate_business_rules(document)

    # Then
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [("cdeCriticalityTiering", "Tier 1"), ("useCaseLinks", ["UC-001"])],
)
def test__cde_field_on_non_cde__business_rules__cde_only_error(
    field: str, value: object
) -> None:
    """CDE fields are only allowed on Critical Data Elements."""
    # Given
    element = {"elementName": "x", "isCriticalDataElement": False, field: value}

    # When
    errors = validate_business_rules({"dataProductPreview": [{}, element]})

    # Then
    assert errors == [
        ValidationError(f"dataProductPreview[1].{field}", CDE_ONLY_MESSAGE)
    ]


@pytest.mark.unit
def test__cde_fields_on_cdes_and_blank_elsewhere__business_rules__no_errors() -> None:
    """Filled on CDEs and blank on other elements is fine."""
    # Given
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

    # When
    errors = validate_business_rules({"dataProductPreview": elements})

    # Then
    assert errors == []


@pytest.mark.unit
def test__legacy_data_element_key__business_rules__checked_too() -> None:
    """The v1 ``dataElementPreview`` key is checked like the v2 key."""
    # Given
    element = {"isCriticalDataElement": False, "cdeCriticalityTiering": "Tier 1"}

    # When
    errors = validate_business_rules({"dataElementPreview": [element]})

    # Then
    assert _paths(errors) == {"dataElementPreview[0].cdeCriticalityTiering"}


@pytest.mark.unit
def test__duplicate_requirement_id__business_rules__later_item_reported() -> None:
    """Requirement IDs must be unique."""
    # Given
    requirements = [{"id": "BR-001"}, {"id": "BR-002"}, {"id": "BR-001"}]

    # When
    errors = validate_business_rules({"businessRequirements": requirements})

    # Then
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
@pytest.mark.parametrize("name", SUPPORTED_STRUCTURE_DEFINITIONS)
def test__supported_definition__load_schema__json_schema(name: str) -> None:
    """Every supported schema version loads."""
    # When
    schema = load_schema(name)

    # Then
    assert schema["$schema"].startswith("http://json-schema.org/")


@pytest.mark.unit
def test__v1_document__validate__passes_its_own_version() -> None:
    """A v1 document is validated against v1 and passes."""
    # Given
    v1_document = _fixture("OP-0001", "0.2.0")
    assert v1_document["structureDefinition"] == V1

    # When
    schema_errors, strict_errors = (
        validate_schema(v1_document),
        validate_strict(v1_document),
    )

    # Then
    assert (schema_errors, strict_errors) == ([], [])


@pytest.mark.unit
def test__v1_document__validate_schema_against_v2__fails() -> None:
    """The same content does not meet v2 (useCases hold IDs there, etc.)."""
    # Given
    v1_document = _fixture("OP-0001", "0.2.0")

    # When
    errors = validate_schema(v1_document, CURRENT_STRUCTURE_DEFINITION)

    # Then
    assert errors != []


@pytest.mark.unit
def test__same_content_labelled_v1_and_v2__validate_strict__declared_version_rules(
    complete: dict[str, Any],
) -> None:
    """v2 requires useCases; v1 does not."""
    # Given
    del complete["useCases"]
    v1_labelled = {**copy.deepcopy(complete), "structureDefinition": V1}
    v1_labelled["businessRequirements"] = [{"id": "BR-001", "description": "x"}]

    # When
    v2_errors, v1_errors = validate_strict(complete), validate_strict(v1_labelled)

    # Then
    assert "useCases" in _paths(v2_errors)
    assert "useCases" not in _paths(v1_errors)


@pytest.mark.unit
def test__definition_with_path_prefix__validate_lenient__prefix_ignored() -> None:
    """Only the file name of the definition counts."""
    # When
    errors = validate_lenient(_minimal_draft(structureDefinition=f"schemas/{V1}"))

    # Then
    assert errors == []


@pytest.mark.unit
def test__no_definition__validate__current_schema_used() -> None:
    """A document without a definition is validated against the current schema."""
    # Given
    draft = _minimal_draft()
    del draft["structureDefinition"]

    # When
    lenient, strict = validate_lenient(draft), validate_strict(draft)

    # Then
    assert lenient == []
    assert "useCases" in _paths(strict)


@pytest.mark.unit
@pytest.mark.parametrize(
    "validate", [validate_lenient, validate_strict, validate_schema]
)
def test__unsupported_definition__validate__definition_error(
    validate: Callable[[dict], list[ValidationError]],
) -> None:
    """An unknown definition (or a path trick) is an error, not a file read."""
    # Given
    document = _minimal_draft(structureDefinition="../../etc/passwd")

    # When
    errors = validate(document)

    # Then
    assert "structureDefinition" in _paths(errors)
    assert "Unsupported structure definition" in errors[-1].message
