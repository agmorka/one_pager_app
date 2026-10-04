"""Business rules for the shared Use Case registry (no Streamlit, no SQL).

Pure functions used by the data access implementations and the Use Cases page:
- clean_use_case_input: normalize form input before validation/storage
- validate_use_case_input: field-level validation messages
- format_use_case_id: UC-### formatting with an overflow guard

Service entry points for the writes (Use Cases page and Editor): check the
user, write through ``DataAccess`` with the user's initials in the audit
columns, and log a security event (Backend_Design.md §14):
- create_use_case, update_use_case, set_use_case_deprecated

Per Requirements_and_Scope.md §14, Use Case IDs are assigned by the application,
never entered by the user.
"""

from collections.abc import Collection
from typing import TYPE_CHECKING

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.models import PRIORITY_OPTIONS, CurrentUser, UseCaseInput
from onepagerapp.permissions import (
    PermissionDeniedError,
    can_manage_use_cases,
    require_identity,
)
from onepagerapp.state_machine import Actor
from onepagerapp.validation import sanitize_text

if TYPE_CHECKING:
    from onepagerapp.data_access.base import DataAccess

USE_CASE_ID_PREFIX = "UC"
USE_CASE_ID_MAX = 999  # UC-### allows three digits (Data_Model.md §4, §8 item 1)

# Field name → (form label, max length). Order is the form/display order.
USE_CASE_FIELDS: dict[str, tuple[str, int]] = {
    "persona": ("Persona", 200),
    "goal": ("Goal", 500),
    "scenario": ("Scenario", 2000),
    "decision_enabled": ("Decision enabled", 1000),
}


def clean_use_case_input(data: UseCaseInput) -> UseCaseInput:
    """Return a copy of the input with HTML tags and outer whitespace removed.

    Args:
        data: Raw values from the create/edit form.

    Returns:
        Normalized UseCaseInput, ready for validation and storage.

    """
    return UseCaseInput(
        persona=sanitize_text(data.persona),
        goal=sanitize_text(data.goal),
        scenario=sanitize_text(data.scenario),
        decision_enabled=sanitize_text(data.decision_enabled),
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


# ============================================================================
# Writes
# ============================================================================

MANAGE_DENIED_MESSAGE = "You are not allowed to manage Use Cases."


def _check_can_manage(
    user: CurrentUser | None, roles: Collection[Actor], action: str
) -> CurrentUser:
    actor = require_identity(user, action)
    if not can_manage_use_cases(actor.initials, roles):
        log_permission_denied(action, user=actor.initials)
        raise PermissionDeniedError(MANAGE_DENIED_MESSAGE)
    return actor


def create_use_case(
    data_access: "DataAccess",
    data: UseCaseInput,
    user: CurrentUser | None,
    *,
    roles: Collection[Actor],
) -> str:
    """Create a Use Case from cleaned, validated input; return its UC-### ID.

    Raises:
        PermissionDeniedError: No recognised user, or not allowed (logged).

    """
    actor = _check_can_manage(user, roles, "create_use_case")
    try:
        use_case_id = data_access.create_use_case(data, actor.initials)
    except Exception:
        log_event("create_use_case", Outcome.FAILED, user=actor.initials)
        raise
    log_event(
        "create_use_case", Outcome.SUCCESS, user=actor.initials, use_case_id=use_case_id
    )
    return use_case_id


def update_use_case(
    data_access: "DataAccess",
    use_case_id: str,
    data: UseCaseInput,
    user: CurrentUser | None,
    *,
    roles: Collection[Actor],
) -> None:
    """Save the edited fields of a Use Case.

    Raises:
        PermissionDeniedError: No recognised user, or not allowed (logged).
        NotFoundError: No Use Case has this ID.

    """
    actor = _check_can_manage(user, roles, "update_use_case")
    try:
        data_access.update_use_case(use_case_id, data, actor.initials)
    except Exception:
        log_event(
            "update_use_case",
            Outcome.FAILED,
            user=actor.initials,
            use_case_id=use_case_id,
        )
        raise
    log_event(
        "update_use_case", Outcome.SUCCESS, user=actor.initials, use_case_id=use_case_id
    )


def set_use_case_deprecated(
    data_access: "DataAccess",
    use_case_id: str,
    *,
    deprecated: bool,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> None:
    """Deprecate (``deprecated=True``) or restore a Use Case.

    Raises:
        PermissionDeniedError: No recognised user, or not allowed (logged).
        NotFoundError: No Use Case has this ID.

    """
    action = "deprecate_use_case" if deprecated else "restore_use_case"
    actor = _check_can_manage(user, roles, action)
    try:
        data_access.set_use_case_deprecated(
            use_case_id, deprecated=deprecated, user_initials=actor.initials
        )
    except Exception:
        log_event(action, Outcome.FAILED, user=actor.initials, use_case_id=use_case_id)
        raise
    log_event(action, Outcome.SUCCESS, user=actor.initials, use_case_id=use_case_id)
