"""The lenient (Save Draft) and strict (Submit for Review) tiers (Backend §4)."""

from typing import Any

from onepagerapp.models import ValidationError
from onepagerapp.validation.rules import MAX_NAME_LENGTH, MAX_TEXT_LENGTH, check_length
from onepagerapp.validation.schema import (
    load_lenient_schema,
    load_schema,
    resolve_structure_definition,
    schema_errors,
)


def validate_lenient(document: dict[str, Any]) -> list[ValidationError]:
    """Validate a document for Save Draft (Backend_Design.md §4).

    Only ``productName`` and ``description`` must be non-empty. Any other field
    may be missing or empty, but what is present must match the schema's
    shapes and types (the document is always validated against its schema,
    Requirements_and_Scope.md §5).
    """
    errors: list[ValidationError] = []
    if not str(document.get("productName") or "").strip():
        errors.append(ValidationError("productName", "Product Name is required."))
    check_length(errors, "productName", document.get("productName"), MAX_NAME_LENGTH)
    if not str(document.get("description") or "").strip():
        errors.append(ValidationError("description", "Description is required."))
    check_length(errors, "description", document.get("description"), MAX_TEXT_LENGTH)

    structure_definition, version_errors = resolve_structure_definition(document)
    if structure_definition is None:
        return errors + version_errors
    errors += schema_errors(document, load_lenient_schema(structure_definition))
    return errors


# ============================================================================
# Strict tier (Submit for Review)
# ============================================================================

RETENTION_REQUIRED_MESSAGE = (
    "Add at least one retention requirement: required when the data contains "
    "PII or sensitive data, or is not classified as Public."
)
CDE_ONLY_MESSAGE = (
    "Only applies to Critical Data Elements. Leave it empty or mark the "
    "element as a CDE."
)


def _needs_retention(classification: dict[str, Any]) -> bool:
    level = classification.get("classificationLevel")
    return bool(
        classification.get("containsPII")
        or classification.get("containsSensitiveData")
        or (level and level != "Public")
    )


def _has_retention(document: dict[str, Any], classification: dict[str, Any]) -> bool:
    retention = document.get("retentionRequirements")
    if isinstance(retention, list) and retention:
        return True
    # v1 kept retention as a string inside dataClassification.
    return bool(str(classification.get("retentionRequirements") or "").strip())


def _is_set(value: object) -> bool:
    """Whether a value counts as filled in (not None, "" or [])."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return bool(value)
    return True


def _data_elements(document: dict[str, Any]) -> tuple[str, list[Any]]:
    for key in ("dataProductPreview", "dataElementPreview"):
        elements = document.get(key)
        if isinstance(elements, list):
            return key, elements
    return "dataProductPreview", []


def validate_business_rules(document: dict[str, Any]) -> list[ValidationError]:
    """Conditional rules on top of the schema (Requirements_and_Scope.md §5).

    - ``retentionRequirements`` must not be empty when the data contains PII or
      sensitive data, or when ``classificationLevel`` is not ``Public``.
    - ``cdeCriticalityTiering`` and ``useCaseLinks`` must be null unless the
      data element is a Critical Data Element.
    - Business Requirement IDs are unique within the One Pager
      (Requirements_and_Scope.md §14).
    """
    errors: list[ValidationError] = []

    classification = document.get("dataClassification")
    if (
        isinstance(classification, dict)
        and _needs_retention(classification)
        and not _has_retention(document, classification)
    ):
        errors.append(
            ValidationError("retentionRequirements", RETENTION_REQUIRED_MESSAGE)
        )

    key, elements = _data_elements(document)
    for i, element in enumerate(elements):
        if not isinstance(element, dict) or element.get("isCriticalDataElement"):
            continue
        errors.extend(
            ValidationError(f"{key}[{i}].{field_name}", CDE_ONLY_MESSAGE)
            for field_name in ("cdeCriticalityTiering", "useCaseLinks")
            if _is_set(element.get(field_name))
        )

    seen: set[str] = set()
    requirements = document.get("businessRequirements")
    for i, requirement in enumerate(
        requirements if isinstance(requirements, list) else []
    ):
        br_id = requirement.get("id") if isinstance(requirement, dict) else None
        if not br_id:
            continue
        if br_id in seen:
            errors.append(
                ValidationError(
                    f"businessRequirements[{i}].id",
                    f"Requirement ID {br_id} is used more than once.",
                )
            )
        seen.add(br_id)

    return errors


def validate_strict(document: dict[str, Any]) -> list[ValidationError]:
    """Validate a document for Submit for Review (Backend_Design.md §4).

    The full schema the document declares (every ``required`` field and
    ``minItems``), then the conditional business rules. Both run so the user
    sees every problem at once.
    """
    structure_definition, errors = resolve_structure_definition(document)
    if structure_definition is None:
        return errors
    errors = schema_errors(document, load_schema(structure_definition))
    return errors + validate_business_rules(document)
