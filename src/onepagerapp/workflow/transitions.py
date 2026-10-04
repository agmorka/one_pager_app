"""Executing status transitions (Backend_Design.md §2-3).

``apply_transitions`` executes rules from ``state_machine.TRANSITIONS`` (the
single source of truth for allowed transitions) and enforces the valid OP/DP
status combinations. Every workflow action changes statuses through it.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event, log_status_transition
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import ChangeLogEntry, CurrentUser, OnePagerStatusRow
from onepagerapp.state_machine import (
    TRANSITIONS,
    InvalidTransitionError,
    TransitionRule,
    check_combination,
    serialize_state_machine,
)

logger = logging.getLogger(__name__)


TRANSITION_FAILED_MESSAGE = "The status could not be changed. Please retry."
TRANSITION_CONFLICT_MESSAGE = (
    "This One Pager was changed by someone else in the meantime. Reload the "
    "page and try again."
)


class TransitionError(RuntimeError):
    """A transition could not be stored. The message is safe to show to users."""


class ConfirmationRequiredError(ValueError):
    """The transition is destructive and was not confirmed (e.g. Deprecate)."""


def _transition_entry(
    rule: TransitionRule,
    row: OnePagerStatusRow,
    user: CurrentUser,
    now: datetime,
    note: str | None,
) -> ChangeLogEntry:
    summary = f"{rule.summary}: {note}" if note else rule.summary
    return ChangeLogEntry(
        id=0,
        one_pager_id=row.one_pager_id,
        version=row.version,
        event_type=rule.event_type,
        author_initials=user.initials,
        author_name=user.display_name,
        summary=summary,
        created_at=now,
        from_status=rule.from_status,
        to_status=rule.to_status,
        status_field=rule.status_field,
    )


def plan_transitions(
    row: OnePagerStatusRow, rules: list[TransitionRule]
) -> OnePagerStatusRow:
    """Return the status row after applying ``rules`` in order (nothing stored).

    Raises:
        InvalidTransitionError: A rule does not start from the status the row
            has at that point.
        InvalidStatusCombinationError: The resulting OP/DP pair is not allowed.

    """
    new_row = replace(row)
    for rule in rules:
        if TRANSITIONS.get(rule.key) is not rule:
            msg = f"{rule.action} is not a registered transition."
            raise InvalidTransitionError(msg)
        current = getattr(new_row, rule.status_field)
        if current != rule.from_status:
            msg = (
                f"Cannot {rule.label.lower()}: the status is {current}, "
                f"not {rule.from_status}."
            )
            raise InvalidTransitionError(msg)
        setattr(new_row, rule.status_field, rule.to_status)
    check_combination(new_row.one_pager_status, new_row.data_product_status)
    return new_row


def apply_transitions(  # noqa: PLR0913 - every argument is part of the change
    data_access: DataAccess,
    row: OnePagerStatusRow,
    rules: list[TransitionRule],
    user: CurrentUser,
    *,
    now: datetime | None = None,
    note: str | None = None,
    reviewed: bool = False,
    version: str | None = None,
) -> OnePagerStatusRow:
    """Execute one user action made of one or more status transitions.

    All status changes are written with one conditional update of the
    ``one_pager_status`` row (it must still have the status and version the
    caller read), then one change-log entry per transition in a single insert.
    If the change log cannot be written, the row is put back, so an action is
    never half-done (e.g. never stranded in ``Ready for Review``).

    Permission, validation and confirmation guards are the caller's job; this
    function enforces the transition rules and the valid status combinations.

    Args:
        data_access: Tabular data access.
        row: The status row as read by the caller.
        rules: Transitions to apply, in order.
        user: The acting user (change-log author, ``last_updated_by``).
        now: Transition instant (defaults to the current UTC time).
        note: Text appended to every change-log summary (e.g. a comment).
        reviewed: The action is a review decision (Approve / Reject): sets
            ``reviewed_at`` and ``reviewed_by`` (Data_Model.md §3).
        version: New document version (Approve); the change-log entries are
            written for it. By default the version does not change.

    Returns:
        The stored status row.

    Raises:
        InvalidTransitionError: A rule does not apply to the current status.
        InvalidStatusCombinationError: The result is not a valid OP/DP pair.
        TransitionError: Storing failed or the row changed meanwhile.

    """
    now = now or datetime.now(UTC)
    new_row = plan_transitions(row, rules)
    new_row.last_updated_at = now
    new_row.last_updated_by = user.initials
    if reviewed:
        new_row.reviewed_at = now
        new_row.reviewed_by = user.initials
    if version:
        new_row.version = version
    entries = [_transition_entry(rule, new_row, user, now, note) for rule in rules]
    action = rules[0].action if rules else "transition"

    try:
        updated = data_access.update_one_pager_status(
            new_row,
            expected_version=row.version,
            expected_status=row.one_pager_status,
        )
    except Exception as e:
        logger.exception(f"{action} on {row.one_pager_id} failed")
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="update_one_pager_status",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e
    if not updated:
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="status_row_changed",
        )
        raise TransitionError(TRANSITION_CONFLICT_MESSAGE)

    try:
        data_access.append_change_log_entries(entries)
    except Exception as e:
        logger.exception(f"Change log of {action} on {row.one_pager_id} failed")
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="append_change_log",
        )
        _undo_transition(data_access, row, new_row, user, action)
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e

    for rule in rules:
        log_status_transition(
            one_pager_id=row.one_pager_id,
            user=user.initials,
            status_field=rule.status_field,
            from_status=rule.from_status,
            to_status=rule.to_status,
            version=new_row.version,
        )
    return new_row


def _undo_transition(
    data_access: DataAccess,
    previous: OnePagerStatusRow,
    written: OnePagerStatusRow,
    user: CurrentUser,
    action: str,
) -> None:
    """Best-effort rollback of a status update whose change log failed."""
    try:
        restored = data_access.update_one_pager_status(
            previous,
            expected_version=written.version,
            expected_status=written.one_pager_status,
        )
    except Exception:
        logger.exception(f"Rolling back {action} on {previous.one_pager_id} failed")
        restored = False
    if not restored:
        log_event(
            action,
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=previous.one_pager_id,
        )


def get_workflow_reference() -> dict[str, list[dict[str, object]]]:
    """Transitions and valid OP/DP combinations for the Help page (Backend §14).

    Rendered from ``TRANSITIONS`` / ``VALID_COMBINATIONS``, so the Help page
    always describes the rules the application enforces.
    """
    return serialize_state_machine()
