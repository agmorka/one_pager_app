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

Modules: ``rules`` (patterns, limits, messages), ``sanitize``, ``basics``
(create tier and the Basics of every save), ``schema`` (JSON Schema) and
``tiers`` (lenient and strict). Everything is re-exported here, so callers
import from ``onepagerapp.validation``.
"""

from onepagerapp.validation.basics import (
    validate_create,
    validate_edit_basics,
    validate_owner_and_smes,
)
from onepagerapp.validation.rules import (
    DATA_PRODUCT_PATTERN,
    DATA_PRODUCT_RULE,
    DATA_PRODUCT_TYPES,
    EMAIL_PATTERN,
    INITIALS_RULE,
    MAX_EMAIL_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    initials_pattern,
    set_initials_pattern,
)
from onepagerapp.validation.sanitize import (
    normalize_document,
    normalize_new_one_pager,
    person_from_dict,
    sanitize_text,
)
from onepagerapp.validation.schema import (
    CURRENT_STRUCTURE_DEFINITION,
    SUPPORTED_STRUCTURE_DEFINITIONS,
    load_lenient_schema,
    load_schema,
    resolve_structure_definition,
    validate_schema,
)
from onepagerapp.validation.tiers import (
    CDE_ONLY_MESSAGE,
    RETENTION_REQUIRED_MESSAGE,
    validate_business_rules,
    validate_lenient,
    validate_strict,
)

__all__ = [
    "CDE_ONLY_MESSAGE",
    "CURRENT_STRUCTURE_DEFINITION",
    "DATA_PRODUCT_PATTERN",
    "DATA_PRODUCT_RULE",
    "DATA_PRODUCT_TYPES",
    "EMAIL_PATTERN",
    "INITIALS_RULE",
    "MAX_EMAIL_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_TEXT_LENGTH",
    "RETENTION_REQUIRED_MESSAGE",
    "SUPPORTED_STRUCTURE_DEFINITIONS",
    "initials_pattern",
    "load_lenient_schema",
    "load_schema",
    "normalize_document",
    "normalize_new_one_pager",
    "person_from_dict",
    "resolve_structure_definition",
    "sanitize_text",
    "set_initials_pattern",
    "validate_business_rules",
    "validate_create",
    "validate_edit_basics",
    "validate_lenient",
    "validate_owner_and_smes",
    "validate_schema",
    "validate_strict",
]
