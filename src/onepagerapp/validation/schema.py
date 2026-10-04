"""JSON Schema loading and validation (Data_Model.md §6).

Documents are validated against the schema named in their own
``structureDefinition``, so older documents keep validating against the
version they were written with.
"""

import json
import re
from collections.abc import Iterable
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as SchemaError

from onepagerapp.models import ValidationError

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


def _schema_text(structure_definition: str) -> str:
    """Read a schema file shipped in the wheel, or from the repo in development."""
    name = Path(structure_definition).name
    packaged = resources.files("onepagerapp").joinpath("schemas", name)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo_file = Path(__file__).resolve().parents[3] / "schemas" / name
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
    return str(error.message)


def _translate(error: SchemaError) -> ValidationError:
    """Turn a jsonschema error into a user-facing ValidationError."""
    path = _field_path(error.absolute_path)
    if error.validator == "required":
        match = _REQUIRED_MESSAGE.match(error.message)
        name = match.group("name") if match else ""
        full = f"{path}.{name}" if path and name else path or name
        return ValidationError(full, "This field is required.")
    return ValidationError(path, _message(error))


def schema_errors(
    document: dict[str, Any], schema: dict[str, Any]
) -> list[ValidationError]:
    """Every error of ``document`` against ``schema``, sorted by field path."""
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
    return schema_errors(document, load_schema(structure_definition))
