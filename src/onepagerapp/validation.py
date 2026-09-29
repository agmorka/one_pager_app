"""One Pager content validation (Backend_Design.md §4).

Three tiers, all derived from the JSON Schema (Requirements_and_Scope.md §5):

- **create** — used when a new One Pager is created (New_One_Pager_Plan D2):
  ``productName`` + ``description`` plus the keys the storage layer needs
  (``dataProduct``, ``businessDomain``, ``dataProductType``,
  ``dataProductOwner``). Works on the Editor's form input.
- **lenient** (Save Draft) — only ``productName`` and ``description`` must be
  non-empty; everything present must still have the right shape (the schema
  with its ``required`` / ``minItems`` / ``minLength`` keywords removed).
- **strict** (Submit for Review) — the full schema, including every
  ``required`` field and ``minItems``, plus the conditional business rules.

Documents are validated against the schema named in their own
``structureDefinition`` (Data_Model.md §6), so older documents keep
validating against the version they were written with.

Also provides input sanitization (Architecture.md §8). Errors are returned as a
list of ``ValidationError`` objects, never raised.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as SchemaError

from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
    PersonRef,
    ValidationError,
)

# Schema version written into new documents and one_pager_status (D9). Resolved
# as a file name inside the ``schemas`` directory.
CURRENT_STRUCTURE_DEFINITION = "structure_one_pager_v_2.json"

# Every schema version documents may declare (Data_Model.md §6: older versions
# are never removed). A document naming anything else fails validation.
SUPPORTED_STRUCTURE_DEFINITIONS = (
    "structure_one_pager_v_1.json",
    CURRENT_STRUCTURE_DEFINITION,
)

# Keywords that make a value mandatory; dropped from the schema for the
# lenient tier.
_PRESENCE_KEYWORDS = frozenset({"required", "minItems", "minLength"})

DATA_PRODUCT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
INITIALS_PATTERN = re.compile(r"^[A-Z]{2,5}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HTML_TAG = re.compile(r"<[^>]*>")

# Maximum lengths (D11). Values over the limit are rejected, never truncated.
MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 5000
MAX_EMAIL_LENGTH = 254

DATA_PRODUCT_TYPES = ("Foundational", "Integrated", "Augmented")

DATA_PRODUCT_RULE = (
    "Use 2-63 characters: lowercase letters, digits and underscores, "
    "starting with a letter (e.g. customer_master)."
)


# ============================================================================
# Sanitization
# ============================================================================


def sanitize_text(value: str | None) -> str:
    """Trim whitespace and strip HTML tags from a free-text value."""
    if not value:
        return ""
    return _HTML_TAG.sub("", str(value)).strip()


def _sanitize_person(person: PersonRef) -> PersonRef:
    team = sanitize_text(person.team)
    return PersonRef(
        name=sanitize_text(person.name),
        initials=sanitize_text(person.initials).upper(),
        email=sanitize_text(person.email),
        team=team or None,
    )


def _is_blank_person(person: PersonRef) -> bool:
    return not (person.name or person.initials or person.email or person.team)


def normalize_new_one_pager(data: NewOnePagerInput) -> NewOnePagerInput:
    """Return a sanitized copy of the create input.

    Trims and strips HTML from every text field, upper-cases initials and drops
    SME rows that are completely empty (e.g. a blank row left in the grid).
    """
    smes = [_sanitize_person(s) for s in data.smes]
    return replace(
        data,
        data_product=sanitize_text(data.data_product),
        product_name=sanitize_text(data.product_name),
        business_domain=sanitize_text(data.business_domain),
        data_product_type=sanitize_text(data.data_product_type),
        description=sanitize_text(data.description),
        business_problem_statement=sanitize_text(data.business_problem_statement),
        owner=_sanitize_person(data.owner),
        smes=[s for s in smes if not _is_blank_person(s)],
    )


def _sanitize_value(value: object) -> object:
    """Sanitize a document value: text is trimmed and stripped of HTML.

    Lists drop items that end up empty; dicts drop keys whose value ends up
    empty ("" / None / []), so blank optional fields are not stored.
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        items = [_sanitize_value(item) for item in value]
        return [item for item in items if item not in ("", None, [], {})]
    if isinstance(value, dict):
        cleaned = {key: _sanitize_value(item) for key, item in value.items()}
        return {k: v for k, v in cleaned.items() if v not in ("", None, [])}
    return value


def _clean_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = _sanitize_value(items)
    return (
        [item for item in cleaned if isinstance(item, dict)]
        if isinstance(cleaned, list)
        else []
    )


def _clean_strings(items: list[str]) -> list[str]:
    return [sanitize_text(item) for item in items if sanitize_text(item)]


def _clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    cleaned = _sanitize_value(value)
    return cleaned if isinstance(cleaned, dict) else {}


def person_from_dict(value: dict[str, Any]) -> PersonRef:
    """Build a PersonRef from a document person dict (owner / SME item)."""
    return PersonRef(
        name=str(value.get("name") or ""),
        initials=str(value.get("initials") or ""),
        email=str(value.get("email") or ""),
        team=value.get("team") or None,
    )


def normalize_document(document: OnePagerDocument) -> OnePagerDocument:
    """Return a sanitized copy of an edited document (Architecture.md §8).

    Every text value is trimmed and stripped of HTML, initials are upper-cased
    and empty list items (e.g. a blank SME row) are dropped.
    """
    owner = _sanitize_person(
        PersonRef(
            name=document.owner_name,
            initials=document.owner_initials,
            email=document.owner_email,
            team=document.owner_team,
        )
    )
    smes = [
        _sanitize_person(person_from_dict(s))
        for s in document.smes
        if isinstance(s, dict)
    ]
    return replace(
        document,
        product_name=sanitize_text(document.product_name),
        business_domain=sanitize_text(document.business_domain),
        data_product_type=sanitize_text(document.data_product_type),
        description=sanitize_text(document.description),
        owner_name=owner.name,
        owner_initials=owner.initials,
        owner_email=owner.email,
        owner_team=owner.team,
        business_problem_statement=sanitize_text(document.business_problem_statement),
        smes=[
            {k: v for k, v in vars(s).items() if v}
            for s in smes
            if not _is_blank_person(s)
        ],
        use_cases=_clean_items(document.use_cases),
        business_requirements=_clean_items(document.business_requirements),
        data_sources=_clean_items(document.data_sources),
        data_product_preview=_clean_items(document.data_product_preview),
        data_classification=_clean_dict(document.data_classification),
        retention_requirements=_clean_items(document.retention_requirements),
        data_governance_artifacts=_clean_dict(document.data_governance_artifacts),
        out_of_scope=_clean_strings(document.out_of_scope),
        open_questions=_clean_items(document.open_questions),
        assumptions=_clean_strings(document.assumptions),
    )


def validate_edit_basics(
    document: OnePagerDocument,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> list[ValidationError]:
    """Check a (normalized) edited document beyond the lenient schema tier.

    ``one_pager_status`` and ``one_pager_authorized_users`` need a Business
    Domain, a Product Type, and a complete Owner / SME list on every save, so
    these are checked on Save Draft too. A value that was valid when stored
    stays allowed even if the reference value has since been deactivated.
    """
    errors: list[ValidationError] = []
    if not document.business_domain:
        errors.append(ValidationError("businessDomain", "Business Domain is required."))
    elif document.business_domain not in allowed_domains:
        errors.append(
            ValidationError("businessDomain", "Select an active Business Domain.")
        )
    if not document.data_product_type:
        errors.append(ValidationError("dataProductType", "Product Type is required."))
    elif (
        document.data_product_type not in allowed_types
        or document.data_product_type not in DATA_PRODUCT_TYPES
    ):
        errors.append(
            ValidationError("dataProductType", "Select an active Product Type.")
        )
    owner = PersonRef(
        name=document.owner_name,
        initials=document.owner_initials,
        email=document.owner_email,
        team=document.owner_team,
    )
    smes = [person_from_dict(s) for s in document.smes]
    errors.extend(validate_owner_and_smes(owner, smes))
    return errors


# ============================================================================
# Create tier
# ============================================================================


def _check_length(
    errors: list[ValidationError], path: str, value: str | None, max_len: int
) -> None:
    if value and len(value) > max_len:
        errors.append(ValidationError(path, f"Must be at most {max_len} characters."))


def _validate_person(
    errors: list[ValidationError], path: str, person: PersonRef
) -> None:
    if not person.name:
        errors.append(ValidationError(f"{path}.name", "Name is required."))
    _check_length(errors, f"{path}.name", person.name, MAX_NAME_LENGTH)
    _check_length(errors, f"{path}.team", person.team, MAX_NAME_LENGTH)

    if not person.initials:
        errors.append(ValidationError(f"{path}.initials", "Initials are required."))
    elif not INITIALS_PATTERN.match(person.initials):
        errors.append(
            ValidationError(f"{path}.initials", "Initials must be 2-5 letters.")
        )

    if not person.email:
        errors.append(ValidationError(f"{path}.email", "Email is required."))
    elif len(person.email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(person.email):
        errors.append(ValidationError(f"{path}.email", "Enter a valid email address."))


def _validate_basics(
    errors: list[ValidationError],
    data: NewOnePagerInput,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> None:
    if not data.data_product:
        errors.append(ValidationError("dataProduct", "Data Product is required."))
    elif not DATA_PRODUCT_PATTERN.match(data.data_product):
        errors.append(ValidationError("dataProduct", DATA_PRODUCT_RULE))

    if not data.product_name:
        errors.append(ValidationError("productName", "Product Name is required."))
    _check_length(errors, "productName", data.product_name, MAX_NAME_LENGTH)

    if not data.description:
        errors.append(ValidationError("description", "Description is required."))
    _check_length(errors, "description", data.description, MAX_TEXT_LENGTH)
    _check_length(
        errors,
        "businessProblemStatement",
        data.business_problem_statement,
        MAX_TEXT_LENGTH,
    )

    if not data.business_domain:
        errors.append(ValidationError("businessDomain", "Business Domain is required."))
    elif data.business_domain not in allowed_domains:
        errors.append(
            ValidationError("businessDomain", "Select an active Business Domain.")
        )

    if not data.data_product_type:
        errors.append(ValidationError("dataProductType", "Product Type is required."))
    elif (
        data.data_product_type not in allowed_types
        or data.data_product_type not in DATA_PRODUCT_TYPES
    ):
        errors.append(
            ValidationError("dataProductType", "Select an active Product Type.")
        )


def validate_owner_and_smes(
    owner: PersonRef, smes: list[PersonRef]
) -> list[ValidationError]:
    """Validate the (normalized) Owner and SMEs.

    Their initials grant edit access (``one_pager_authorized_users``), so every
    person needs a name, valid initials and an email, and nobody may appear
    twice.
    """
    errors: list[ValidationError] = []
    _validate_person(errors, "dataProductOwner", owner)

    seen_initials: set[str] = set()
    for i, sme in enumerate(smes):
        path = f"smes[{i}]"
        _validate_person(errors, path, sme)
        if not sme.initials:
            continue
        if sme.initials == owner.initials:
            errors.append(
                ValidationError(
                    f"{path}.initials", "The Owner cannot also be listed as an SME."
                )
            )
        elif sme.initials in seen_initials:
            errors.append(
                ValidationError(f"{path}.initials", "This SME is listed twice.")
            )
        seen_initials.add(sme.initials)
    return errors


def _validate_people(
    errors: list[ValidationError], data: NewOnePagerInput, creator: CurrentUser
) -> None:
    errors.extend(validate_owner_and_smes(data.owner, data.smes))

    # D3: the creator must keep edit access to the Draft they create.
    seen_initials = {s.initials for s in data.smes if s.initials}
    if creator.initials not in {data.owner.initials, *seen_initials}:
        errors.append(
            ValidationError(
                "smes",
                f"Add yourself ({creator.initials}) as the Owner or as an SME "
                "to keep edit access to this One Pager.",
            )
        )


def validate_create(
    data: NewOnePagerInput,
    creator: CurrentUser,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> list[ValidationError]:
    """Validate (already normalized) create input.

    Args:
        data: Input after ``normalize_new_one_pager``.
        creator: The authenticated user creating the One Pager.
        allowed_domains: Active values of ``ref_business_domains``.
        allowed_types: Active values of ``ref_data_product_types``.

    Returns:
        List of validation errors (empty = valid).

    """
    errors: list[ValidationError] = []
    _validate_basics(errors, data, allowed_domains, allowed_types)
    _validate_people(errors, data, creator)
    return errors


# ============================================================================
# JSON Schema
# ============================================================================


def _schema_text(structure_definition: str) -> str:
    """Read a schema file shipped in the wheel, or from the repo in development."""
    name = Path(structure_definition).name
    packaged = resources.files("onepagerapp").joinpath("schemas", name)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo_file = Path(__file__).resolve().parents[2] / "schemas" / name
    if repo_file.is_file():
        return repo_file.read_text(encoding="utf-8")
    msg = f"Schema file not found: {name}"
    raise FileNotFoundError(msg)


@cache
def load_schema(
    structure_definition: str = CURRENT_STRUCTURE_DEFINITION,
) -> dict[str, Any]:
    """Load (and cache) the JSON Schema for a structure definition."""
    schema: dict[str, Any] = json.loads(_schema_text(structure_definition))
    return schema


def _without_presence_keywords(node: object) -> object:
    """Copy a schema node without its ``required``/``minItems``/``minLength``.

    Property *names* are never touched: a property called ``required`` inside
    ``properties`` stays, only the keywords are dropped.
    """
    if isinstance(node, list):
        return [_without_presence_keywords(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in {"properties", "definitions"} and isinstance(value, dict):
            result[key] = {
                name: _without_presence_keywords(sub) for name, sub in value.items()
            }
        elif key not in _PRESENCE_KEYWORDS:
            result[key] = _without_presence_keywords(value)
    return result


@cache
def load_lenient_schema(structure_definition: str) -> dict[str, Any]:
    """Return the lenient-tier schema: shapes and types only, nothing required."""
    lenient = _without_presence_keywords(load_schema(structure_definition))
    if not isinstance(lenient, dict):  # pragma: no cover - schemas are objects
        msg = f"Schema {structure_definition} is not an object"
        raise TypeError(msg)
    return lenient


def resolve_structure_definition(
    document: dict[str, Any],
) -> tuple[str | None, list[ValidationError]]:
    """Return the supported schema a document declares, or an error.

    Documents without ``structureDefinition`` (hand-made or very old) are
    validated against the current schema.
    """
    declared = document.get("structureDefinition")
    if declared in (None, ""):
        return CURRENT_STRUCTURE_DEFINITION, []
    name = Path(str(declared)).name
    if name in SUPPORTED_STRUCTURE_DEFINITIONS:
        return name, []
    return None, [
        ValidationError(
            "structureDefinition",
            f"Unsupported structure definition {declared!r}. Supported: "
            f"{', '.join(SUPPORTED_STRUCTURE_DEFINITIONS)}.",
        )
    ]


def _field_path(parts: Iterable[str | int]) -> str:
    """Render a jsonschema path as ``a.b[0].c``."""
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path


_REQUIRED_MESSAGE = re.compile(r"^'(?P<name>[^']+)' is a required property$")


def _message(error: SchemaError) -> str:
    """User-facing message for a jsonschema error other than ``required``."""
    keyword = error.validator
    value = error.validator_value
    if keyword == "minItems":
        return f"Add at least {value} {'item' if value == 1 else 'items'}."
    if keyword == "minLength":
        return "This field is required."
    if keyword == "enum":
        return f"Must be one of: {', '.join(str(v) for v in value)}."
    if keyword == "pattern":
        return "Has an invalid format."
    if keyword == "type":
        expected = " or ".join(value) if isinstance(value, list) else value
        return f"Must be of type {expected}."
    return error.message


def _translate(error: SchemaError) -> ValidationError:
    """Turn a jsonschema error into a user-facing ValidationError."""
    path = _field_path(error.absolute_path)
    if error.validator == "required":
        match = _REQUIRED_MESSAGE.match(error.message)
        name = match.group("name") if match else ""
        full = f"{path}.{name}" if path and name else path or name
        return ValidationError(full, "This field is required.")
    return ValidationError(path, _message(error))


def _schema_errors(
    document: dict[str, Any], schema: dict[str, Any]
) -> list[ValidationError]:
    validator = Draft7Validator(schema)
    errors = [_translate(e) for e in validator.iter_errors(document)]
    return sorted(errors, key=lambda e: (e.field_path, e.message))


def validate_schema(
    document: dict[str, Any],
    structure_definition: str | None = None,
) -> list[ValidationError]:
    """Validate a YAML-shaped document dict against its full JSON Schema.

    Args:
        document: The document as written to YAML.
        structure_definition: Schema to use; by default the one the document
            declares in ``structureDefinition`` (Data_Model.md §6).

    """
    if structure_definition is None:
        structure_definition, errors = resolve_structure_definition(document)
        if structure_definition is None:
            return errors
    return _schema_errors(document, load_schema(structure_definition))


# ============================================================================
# Lenient tier (Save Draft)
# ============================================================================


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
    _check_length(errors, "productName", document.get("productName"), MAX_NAME_LENGTH)
    if not str(document.get("description") or "").strip():
        errors.append(ValidationError("description", "Description is required."))
    _check_length(errors, "description", document.get("description"), MAX_TEXT_LENGTH)

    structure_definition, version_errors = resolve_structure_definition(document)
    if structure_definition is None:
        return errors + version_errors
    errors += _schema_errors(document, load_lenient_schema(structure_definition))
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
    errors = _schema_errors(document, load_schema(structure_definition))
    return errors + validate_business_rules(document)
