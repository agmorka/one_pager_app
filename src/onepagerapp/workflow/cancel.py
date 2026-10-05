"""Cancel a One Pager (Requirements_and_Scope.md §6)."""

import logging
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.documents import OnePagerDocumentStore, change_log_item
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
    TransitionRule,
    get_rule,
    guard_failure,
)
from onepagerapp.validation import sanitize_text
from onepagerapp.versioning import next_major
from onepagerapp.workflow.transitions import (
    TRANSITION_FAILED_MESSAGE,
    TransitionError,
    apply_transitions,
    discard_if_unreferenced,
    plan_transitions,
)

logger = logging.getLogger(__name__)

MAX_REASON_LENGTH = 500


def cancel_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
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

    Like an approval, cancelling closes the One Pager with the next MAJOR
    version (0.2.0 -> 1.0.0): the cancelled version file is written first
    (nothing references it yet), then the status row points to it; if that
    fails the file is discarded again, so a failed cancel changes nothing.

    Args:
        data_access: Tabular data access.
        document_store: Store of the YAML documents (the cancelled version).
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
    rules = [op_rule, dp_rule]

    now = now or datetime.now(UTC)
    reason = sanitize_text(reason)[:MAX_REASON_LENGTH]
    version = next_major(row.version)
    _write_cancelled_version(data_access, document_store, row, rules, version, user)
    try:
        new_row = apply_transitions(
            data_access,
            row,
            rules,
            user,
            now=now,
            note=reason or None,
            version=version,
        )
    except Exception:
        discard_if_unreferenced(data_access, document_store, one_pager_id, version)
        raise
    _release_any_lock(data_access, one_pager_id, user)
    return new_row


def _write_cancelled_version(  # noqa: PLR0913 - the parts of one version file
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    row: OnePagerStatusRow,
    rules: list[TransitionRule],
    version: str,
    user: CurrentUser,
) -> None:
    """Write the cancelled version file: operational fields from Delta.

    Raises:
        TransitionError: The document could not be read or written.

    """
    one_pager_id = row.one_pager_id
    document = data_access.read_document(one_pager_id, row.version)
    if document is None:
        msg = f"The document of {one_pager_id} v{row.version} could not be found."
        raise TransitionError(msg)
    cancelled_row = plan_transitions(row, rules)
    cancelled = replace(
        document,
        structure_definition=row.structure_definition,
        data_product=row.data_product,
        one_pager_status=cancelled_row.one_pager_status,
        data_product_status=cancelled_row.data_product_status,
        version=version,
        change_log=[
            *document.change_log,
            *(change_log_item(version, rule.summary) for rule in rules),
        ],
        raw_content="",
    )
    # A file for this version can only be left over from a cancel that failed
    # before the status row pointed to it.
    document_store.discard_unreferenced(one_pager_id, version)
    try:
        document_store.write(one_pager_id, cancelled, version)
    except RuntimeError as e:
        logger.exception(f"Writing the cancelled {one_pager_id} failed")
        log_event(
            "cancel",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="write_document",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e


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
