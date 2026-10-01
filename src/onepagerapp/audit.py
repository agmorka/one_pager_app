"""Structured security-event logging (Architecture.md §8, Backend_Design.md §14).

Every security-relevant event (permission denials, rejected edits, lock
overrides, status transitions, failed writes) goes through ``log_event`` so the
records share one ``key=value`` format that log search can parse.

Records contain only user initials, record IDs, action names and outcomes —
never field values or PII (Architecture.md §8). The one exception is the
username of an account that is refused because it has no initials
(``log_unrecognised_user``).
"""

import logging
import re
from enum import Enum

AUDIT_LOGGER_NAME = "onepagerapp.audit"

audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)

# Values made only of these characters are logged unquoted.
_BARE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/+-]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PERMISSION_DENIED = "permission_denied"
    EDIT_REJECTED = "edit_rejected"
    LOCK_OVERRIDE = "lock_override"
    DUPLICATE_RACE = "duplicate_race"
    COMPENSATION_FAILED = "compensation_failed"


_LEVELS: dict[Outcome, int] = {
    Outcome.SUCCESS: logging.INFO,
    Outcome.FAILED: logging.ERROR,
    Outcome.PERMISSION_DENIED: logging.WARNING,
    Outcome.EDIT_REJECTED: logging.WARNING,
    Outcome.LOCK_OVERRIDE: logging.WARNING,
    Outcome.DUPLICATE_RACE: logging.WARNING,
    Outcome.COMPENSATION_FAILED: logging.ERROR,
}


def _format_value(value: object) -> str:
    """Render one value; quote and escape anything that could split a record."""
    if value is None:
        return "-"
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    if _BARE_VALUE.match(text):
        return text
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def format_event(
    action: str,
    outcome: Outcome,
    *,
    user: str | None,
    one_pager_id: str | None = None,
    **details: object,
) -> str:
    """Build the ``key=value`` message for one security event.

    The fixed keys come first (``action``, ``outcome``, ``one_pager_id`` when
    given, ``user``), then ``details`` in the order passed.
    """
    fields: dict[str, object] = {"action": action, "outcome": outcome}
    if one_pager_id is not None:
        fields["one_pager_id"] = one_pager_id
    fields["user"] = user
    for key, value in details.items():
        if not _KEY.match(key):
            msg = f"Invalid audit field name: {key!r}"
            raise ValueError(msg)
        fields[key] = value
    return " ".join(f"{key}={_format_value(value)}" for key, value in fields.items())


def log_event(
    action: str,
    outcome: Outcome,
    *,
    user: str | None,
    one_pager_id: str | None = None,
    **details: object,
) -> None:
    """Log one security event on the ``onepagerapp.audit`` logger.

    The level follows the outcome: INFO for success, WARNING for denials,
    overrides and races, ERROR for failures.

    Args:
        action: What was attempted, e.g. ``create_one_pager``.
        outcome: How it ended.
        user: Initials of the acting user (never a name or e-mail address).
        one_pager_id: The record acted on, when there is one.
        **details: Extra non-PII context such as ``step`` or ``from_status``.

    """
    audit_logger.log(
        _LEVELS[outcome],
        format_event(action, outcome, user=user, one_pager_id=one_pager_id, **details),
    )


def log_permission_denied(
    action: str, *, user: str | None, one_pager_id: str | None = None
) -> None:
    """Log a failed permission check."""
    log_event(action, Outcome.PERMISSION_DENIED, user=user, one_pager_id=one_pager_id)


def log_unrecognised_user(username: str | None) -> None:
    """Log that a user was refused because their account is not recognised.

    There are no initials to log, so the raw username is logged instead (it is
    the only way to see who was refused; never a token or header value other
    than the username). ``None`` means no username was provided at all.
    """
    log_event(
        "access_app", Outcome.PERMISSION_DENIED, user=None, username=username or None
    )


def log_status_transition(  # noqa: PLR0913 - every field is part of the event
    *,
    one_pager_id: str,
    user: str,
    status_field: str,
    from_status: str | None,
    to_status: str,
    version: str | None = None,
) -> None:
    """Log a successful OP or DP status transition."""
    log_event(
        "status_transition",
        Outcome.SUCCESS,
        user=user,
        one_pager_id=one_pager_id,
        status_field=status_field,
        from_status=from_status,
        to_status=to_status,
        version=version,
    )


def log_lock_override(
    *, one_pager_id: str, user: str, previous_holder: str | None
) -> None:
    """Log that an expired lock was taken over by another user."""
    log_event(
        "acquire_lock",
        Outcome.LOCK_OVERRIDE,
        user=user,
        one_pager_id=one_pager_id,
        previous_holder=previous_holder,
    )
