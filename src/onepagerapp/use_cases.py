"""Business rules for the shared Use Case registry (no Streamlit, no SQL).

Pure functions used by the data access implementations and the Use Cases page:
- clean_use_case_input: normalize form input before validation/storage
- validate_use_case_input: field-level validation messages
- format_use_case_id: UC-### formatting with an overflow guard

Per Requirements_and_Scope.md §14, Use Case IDs are assigned by the application,
never entered by the user.
"""

import re

from onepagerapp.models import PRIORITY_OPTIONS, UseCaseInput

USE_CASE_ID_PREFIX = "UC"
USE_CASE_ID_MAX = 999  # UC-### allows three digits (Data_Model.md §4, §8 item 1)

# Field name → (form label, max length). Order is the form/display order.
USE_CASE_FIELDS: dict[str, tuple[str, int]] = {
    "persona": ("Persona", 200),
    "goal": ("Goal", 500),
    "scenario": ("Scenario", 2000),
    "decision_enabled": ("Decision enabled", 1000),
}

_HTML_TAG = re.compile(r"<[^>]*>")


def _clean_text(value: str | None) -> str:
    """Strip HTML tags and surrounding whitespace (Testing_Strategy.md §6)."""
    return _HTML_TAG.sub("", value or "").strip()


def clean_use_case_input(data: UseCaseInput) -> UseCaseInput:
    """Return a copy of the input with HTML tags and outer whitespace removed.

    Args:
        data: Raw values from the create/edit form.

    Returns:
        Normalized UseCaseInput, ready for validation and storage.

    """
    return UseCaseInput(
        persona=_clean_text(data.persona),
        goal=_clean_text(data.goal),
        scenario=_clean_text(data.scenario),
        decision_enabled=_clean_text(data.decision_enabled),
        priority=(data.priority or "").strip(),
    )


def validate_use_case_input(data: UseCaseInput) -> dict[str, str]:
    """Validate (already cleaned) Use Case input.

    All fields are mandatory because every use_cases column is NOT NULL.

    Args:
        data: Output of clean_use_case_input.

    Returns:
        Mapping of field name → error message. Empty when the input is valid.

    """
    errors: dict[str, str] = {}
    for name, (label, max_length) in USE_CASE_FIELDS.items():
        value = getattr(data, name)
        if not value:
            errors[name] = f"{label} is required."
        elif len(value) > max_length:
            errors[name] = f"{label} must be at most {max_length} characters."
    if data.priority not in PRIORITY_OPTIONS:
        errors["priority"] = f"Priority must be one of: {', '.join(PRIORITY_OPTIONS)}."
    return errors


def format_use_case_id(value: int) -> str:
    """Format a sequence value as a Use Case ID (e.g. 7 → "UC-007").

    Args:
        value: Positive sequence value from id_sequences.

    Returns:
        The formatted ID.

    Raises:
        ValueError: If the value is outside 1..USE_CASE_ID_MAX.

    """
    if not 1 <= value <= USE_CASE_ID_MAX:
        msg = (
            f"Use Case ID sequence value {value} is outside the UC-### range "
            f"(1..{USE_CASE_ID_MAX})."
        )
        raise ValueError(msg)
    return f"{USE_CASE_ID_PREFIX}-{value:03d}"
