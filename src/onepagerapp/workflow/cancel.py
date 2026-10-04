"""Cancel a One Pager (Requirements_and_Scope.md §6)."""

import logging
from collections.abc import Collection
from datetime import datetime

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.models import CurrentUser, OnePagerStatusRow
from onepagerapp.permissions import (
    PermissionDeniedError,
    is_owner_or_sme,
    require_identity,
)
from onepagerapp.state_machine import (
    DP_CANCELLED,
    DP_STATUS_FIELD,
    OP_CANCELLED,
    OP_STATUS_FIELD,
    Actor,
    InvalidTransitionError,
    get_rule,
    guard_failure,
)
from onepagerapp.validation import sanitize_text
from onepagerapp.workflow.transitions import apply_transitions

logger = logging.getLogger(__name__)

MAX_REASON_LENGTH = 500


def cancel_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    *,
    reason: str = "",
    roles: Collection[Actor] = (),
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Cancel a One Pager: OP → ``Cancelled`` and, by the system, DP → ``Cancelled``.

    Allowed from ``Draft``, ``Ready for Review`` or ``In Review`` while the
    Data Product is ``In Definition``, for the Owner/SMEs of the One Pager or
    an Admin. Both status changes and their change-log entries are written as
    one action. Any active edit lock is released, whoever holds it
    (Backend_Design.md §2). Cancellation is permanent.

    Args:
        data_access: Tabular data access.
        one_pager_id: The One Pager to cancel.
        user: The acting user.
        reason: Optional reason, appended to the change-log summaries.
        roles: The user's group roles (Admin may cancel any One Pager).
        now: Transition instant (defaults to the current UTC time).

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is neither Owner/SME nor Admin.
        InvalidTransitionError: The statuses do not allow cancelling.
        TransitionError: Storing failed; nothing changed.

    """
    require_identity(user, "cancel_one_pager", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    if not owner_or_sme and Actor.ADMIN not in roles:
        log_permission_denied("cancel", user=user.initials, one_pager_id=one_pager_id)
        msg = "Only the Owner, an SME or an Admin can cancel this One Pager."
        raise PermissionDeniedError(msg)

    op_rule = get_rule(OP_STATUS_FIELD, row.one_pager_status, OP_CANCELLED)
    failure = guard_failure(
        op_rule,
        actors=set(roles),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure:
        raise InvalidTransitionError(failure)
    dp_rule = get_rule(DP_STATUS_FIELD, row.data_product_status, DP_CANCELLED)

    reason = sanitize_text(reason)[:MAX_REASON_LENGTH]
    new_row = apply_transitions(
        data_access, row, [op_rule, dp_rule], user, now=now, note=reason or None
    )
    _release_any_lock(data_access, one_pager_id, user)
    return new_row


def _release_any_lock(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    """Remove the edit lock of a cancelled One Pager, whoever holds it."""
    try:
        lock = data_access.get_lock(one_pager_id)
        if lock is None:
            return
        data_access.delete_lock(
            one_pager_id, locked_by_initials=lock.locked_by_initials
        )
    except Exception:
        logger.exception(f"Releasing the lock of cancelled {one_pager_id} failed")
        return
    log_event(
        "release_lock",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        previous_holder=lock.locked_by_initials,
        reason="cancelled",
    )
