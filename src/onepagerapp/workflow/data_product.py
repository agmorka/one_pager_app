"""Owner-initiated Data Product transitions (Backend_Design.md §3)."""

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
    DP_STATUS_FIELD,
    Actor,
    InvalidTransitionError,
    TransitionRule,
    get_rule,
    guard_failure,
    transitions_from,
)
from onepagerapp.workflow.transitions import (
    ConfirmationRequiredError,
    apply_transitions,
)


def data_product_options(
    row: OnePagerStatusRow, *, owner_or_sme: bool
) -> list[TransitionRule]:
    """List the DP transitions the Owner/SMEs can make now (Change DP Status)."""
    return [
        rule
        for rule in transitions_from(
            DP_STATUS_FIELD, row.data_product_status, Actor.OWNER_SME
        )
        if guard_failure(
            rule,
            actors=set(),
            one_pager_status=row.one_pager_status,
            data_product_status=row.data_product_status,
            is_owner_or_sme=owner_or_sme,
        )
        is None
    ]


def change_data_product_status(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    to_status: str,
    user: CurrentUser,
    *,
    confirmed: bool = False,
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Start development, activate or deprecate a Data Product (Owner/SMEs only).

    Only owner-initiated rules of ``TRANSITIONS`` qualify (system transitions
    happen through approval and cancel), only while the One Pager is
    ``Approved``, and Deprecate must be ``confirmed``. The version does not
    change; one change-log entry is written.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is not Owner/SME of the One Pager.
        InvalidTransitionError: The transition does not exist, is a system
            transition, or its status guards fail.
        ConfirmationRequiredError: A destructive transition was not confirmed.
        TransitionError: Storing failed; nothing changed.

    """
    require_identity(user, "change_data_product_status", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
    rule = get_rule(DP_STATUS_FIELD, row.data_product_status, to_status)
    if Actor.OWNER_SME not in rule.actors:
        msg = f"{rule.label} happens automatically and cannot be chosen."
        raise InvalidTransitionError(msg)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    if not owner_or_sme:
        log_permission_denied(
            "change_data_product_status", user=user.initials, one_pager_id=one_pager_id
        )
        msg = "Only the Owner or an SME can change the Data Product status."
        raise PermissionDeniedError(msg)
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
        msg = f"Confirm that you want to {rule.label.lower()} this Data Product."
        raise ConfirmationRequiredError(msg)
    return apply_transitions(data_access, row, [rule], user, now=now)
