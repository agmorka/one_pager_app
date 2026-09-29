"""One Pager content validation (Backend_Design.md §4).

Implements the **create** tier used when a new One Pager is created
(New_One_Pager_Plan D2): the lenient tier (``productName`` + ``description``)
plus the keys the storage layer needs (``dataProduct``, ``businessDomain``,
``dataProductType``, ``dataProductOwner``). Also provides input sanitization
(Architecture.md §8) and JSON Schema validation of a complete document.

Errors are returned as a list of ``ValidationError`` objects, never raised.
"""

import json
import re
from dataclasses import replace
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef, ValidationError

# Schema version written into new documents and one_pager_status (D9). Resolved
# as a file name inside the ``schemas`` directory.
CURRENT_STRUCTURE_DEFINITION = "structure_one_pager_v_1.json"

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


def _validate_people(
    errors: list[ValidationError], data: NewOnePagerInput, creator: CurrentUser
) -> None:
    _validate_person(errors, "dataProductOwner", data.owner)

    seen_initials: set[str] = set()
    for i, sme in enumerate(data.smes):
        path = f"smes[{i}]"
        _validate_person(errors, path, sme)
        if not sme.initials:
            continue
        if sme.initials == data.owner.initials:
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

    # D3: the creator must keep edit access to the Draft they create.
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


def validate_schema(
    document: dict[str, Any],
    structure_definition: str = CURRENT_STRUCTURE_DEFINITION,
) -> list[ValidationError]:
    """Validate a YAML-shaped document dict against its JSON Schema."""
    validator = Draft7Validator(load_schema(structure_definition))
    return [
        ValidationError(".".join(str(p) for p in error.absolute_path), error.message)
        for error in sorted(validator.iter_errors(document), key=str)
    ]
