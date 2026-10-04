"""Update an approved One Pager (Requirements_and_Scope.md §5, Backend §6)."""

import logging
from datetime import datetime

from onepagerapp.audit import log_permission_denied
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.models import CurrentUser, OnePagerStatusRow
from onepagerapp.permissions import (
    PermissionDeniedError,
    is_owner_or_sme,
    require_identity,
)
from onepagerapp.state_machine import (
    DRAFT_UPDATE,
    OP_STATUS_FIELD,
    InvalidTransitionError,
    get_rule,
    guard_failure,
)
from onepagerapp.workflow.transitions import (
    TRANSITION_FAILED_MESSAGE,
    ConfirmationRequiredError,
    TransitionError,
    apply_transitions,
)

logger = logging.getLogger(__name__)


def start_update(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    *,
    confirmed: bool = False,
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Update an approved One Pager: ``Approved`` → ``Draft Update``.

    Only its Owner/SMEs may, after confirming. The approved version is the
    working copy: it is read from the volume (from Git once Phase 8 lands),
    and because version files are immutable the next Save Draft writes
    ``MAJOR.1.0`` beside it, so the approved file itself is never changed.
    The version and the Data Product status stay as they are. No edit lock is
    taken: it is acquired when the Owner opens the Editor (Backend_Design §6).

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is not Owner/SME of the One Pager.
        InvalidTransitionError: The One Pager is not ``Approved``.
        ConfirmationRequiredError: The update was not confirmed.
        TransitionError: The approved document cannot be read, or storing
            failed; nothing changed.

    """
    require_identity(user, "start_update", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    if not owner_or_sme:
        log_permission_denied("update", user=user.initials, one_pager_id=one_pager_id)
        msg = "Only the Owner or an SME can update this One Pager."
        raise PermissionDeniedError(msg)
    rule = get_rule(OP_STATUS_FIELD, row.one_pager_status, DRAFT_UPDATE)
    failure = guard_failure(
        rule,
        actors=set(),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure:
        raise InvalidTransitionError(failure)
    if rule.requires_confirmation and not confirmed:
        msg = "Confirm that you want to start an update of this One Pager."
        raise ConfirmationRequiredError(msg)

    try:
        approved = data_access.read_document(one_pager_id, row.version)
    except RuntimeError as e:
        logger.exception(f"Reading the approved {one_pager_id} failed")
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e
    if approved is None:
        msg = (
            f"The approved version v{row.version} of {one_pager_id} could not be "
            "found, so it cannot be updated."
        )
        raise TransitionError(msg)
    return apply_transitions(data_access, row, [rule], user, now=now)
