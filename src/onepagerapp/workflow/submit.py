"""Submit for Review (Backend_Design.md §2)."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.editing import require_lock, submission_issues
from onepagerapp.locking import release_lock
from onepagerapp.models import CurrentUser, OnePagerStatusRow, ValidationError
from onepagerapp.permissions import (
    PermissionDeniedError,
    check_can_edit,
    require_identity,
)
from onepagerapp.state_machine import (
    IN_REVIEW,
    OP_STATUS_FIELD,
    READY_FOR_REVIEW,
    get_rule,
)
from onepagerapp.workflow.transitions import TransitionError, apply_transitions

logger = logging.getLogger(__name__)

SUBMIT_LOCK_MESSAGE = (
    "Open the One Pager in the Editor to submit it: your session must hold "
    "its edit lock."
)


@dataclass
class SubmitResult:
    """Outcome of ``submit_for_review``: the stored row, or what blocks it."""

    status_row: OnePagerStatusRow | None = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status_row is not None and not self.errors


def submit_for_review(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    *,
    now: datetime | None = None,
) -> SubmitResult:
    """Submit for Review: Draft / Draft Update → Ready for Review → In Review.

    One user action (Requirements_and_Scope.md §6): the stored document of the
    current version must pass the strict tier; then both transitions are
    written by ``apply_transitions`` (one status update, both change-log
    entries in one insert, rolled back together), so the One Pager is never
    left in ``Ready for Review``. The version does not change. Afterwards the
    edit lock is released (Backend_Design.md §6).

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user may not submit it (not Owner/SME, or
            not ``Draft``/``Draft Update``), or their session does not hold
            the edit lock.
        TransitionError: Storing failed; nothing changed.

    """
    require_identity(user, "submit_for_review", one_pager_id)
    now = now or datetime.now(UTC)
    row = require_status_row(data_access, one_pager_id)
    check_can_edit(
        user,
        one_pager_id,
        row.one_pager_status,
        data_access.get_authorized_users(one_pager_id),
    )
    try:
        require_lock(data_access, one_pager_id, user, session_id, now, "submit")
    except PermissionDeniedError as e:
        raise type(e)(SUBMIT_LOCK_MESSAGE) from e

    document = data_access.read_document(one_pager_id, row.version)
    if document is None:
        msg = f"The document of {one_pager_id} v{row.version} could not be found."
        raise TransitionError(msg)
    errors = submission_issues(document, row)
    if errors:
        log_event(
            "submit",
            Outcome.EDIT_REJECTED,
            user=user.initials,
            one_pager_id=one_pager_id,
            reason="strict_validation",
            issues=len(errors),
        )
        return SubmitResult(errors=errors)

    rules = [
        get_rule(OP_STATUS_FIELD, row.one_pager_status, READY_FOR_REVIEW),
        get_rule(OP_STATUS_FIELD, READY_FOR_REVIEW, IN_REVIEW),
    ]
    new_row = apply_transitions(data_access, row, rules, user, now=now)

    try:
        release_lock(data_access, one_pager_id, user)
    except Exception:
        # The lock expires on its own; the submission itself succeeded.
        logger.exception(f"Releasing the lock after submitting {one_pager_id} failed")
    return SubmitResult(status_row=new_row)
