"""Review decisions: Approve and Reject (Backend_Design.md §2, §8, §13)."""

import logging
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    CurrentUser,
    OnePagerDocument,
    OnePagerStatusRow,
    ReviewComment,
)
from onepagerapp.permissions import (
    PermissionDeniedError,
    is_owner_or_sme,
    require_identity,
)
from onepagerapp.review import MAX_COMMENT_LENGTH
from onepagerapp.state_machine import (
    APPROVED,
    DP_STATUS_FIELD,
    DRAFT,
    IN_DEFINITION,
    IN_ENHANCEMENT,
    IN_REVIEW,
    OP_STATUS_FIELD,
    READY_FOR_DEVELOPMENT,
    Actor,
    InvalidTransitionError,
    TransitionRule,
    get_rule,
    guard_failure,
    reject_target,
)
from onepagerapp.validation import sanitize_text
from onepagerapp.versioning import next_major
from onepagerapp.workflow.transitions import (
    TRANSITION_FAILED_MESSAGE,
    TransitionError,
    apply_transitions,
    plan_transitions,
)

logger = logging.getLogger(__name__)

REJECT_COMMENT_REQUIRED = "Explain why the One Pager is rejected."


class CommentRequiredError(ValueError):
    """A mandatory comment (the reason for a Reject) is missing."""


def _check_review_decision(  # noqa: PLR0913 - the context of one review decision
    data_access: DataAccess,
    one_pager_id: str,
    to_status: str,
    user: CurrentUser,
    roles: Collection[Actor],
    action: str,
) -> tuple[OnePagerStatusRow, TransitionRule]:
    """Load the row and check an Approve / Reject may be made by this user.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is not an Approver, or is Owner/SME of
            this One Pager (segregation of duties, Architecture.md §4).
        InvalidTransitionError: The One Pager is not ``In Review``.

    """
    row = require_status_row(data_access, one_pager_id)
    if Actor.APPROVER not in roles:
        log_permission_denied(action, user=user.initials, one_pager_id=one_pager_id)
        msg = "Only an Approver can review this One Pager."
        raise PermissionDeniedError(msg)
    if row.one_pager_status != IN_REVIEW:
        msg = f"The One Pager is {row.one_pager_status}, not {IN_REVIEW}."
        raise InvalidTransitionError(msg)
    rule = get_rule(OP_STATUS_FIELD, row.one_pager_status, to_status)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    failure = guard_failure(
        rule,
        actors=set(roles),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure and owner_or_sme and rule.segregation_of_duties:
        log_permission_denied(action, user=user.initials, one_pager_id=one_pager_id)
        raise PermissionDeniedError(failure)
    if failure:
        raise InvalidTransitionError(failure)
    return row, rule


def reject_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    comment: str,
    *,
    roles: Collection[Actor],
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Reject a One Pager: ``In Review`` → ``Draft``, with a mandatory comment.

    A rejected update (the Data Product was approved before) returns to
    ``Draft Update`` instead, keeping its DP status (Decision_Log.md §16).

    Only an Approver who is not Owner/SME of the One Pager may reject it. The
    comment is stored in ``review_comments`` (document level, unresolved) and
    appended to the change-log summary (Requirements_and_Scope.md §7). The
    version does not change. Write order: the comment first, then the status
    change with its change-log entry; if the status change fails the comment
    is removed again, so a failed Reject changes nothing.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: Not an Approver, or Owner/SME of this One Pager.
        InvalidTransitionError: The One Pager is not ``In Review``.
        CommentRequiredError: The comment is empty.
        TransitionError: Storing failed; nothing changed.

    """
    require_identity(user, "reject_one_pager", one_pager_id)
    now = now or datetime.now(UTC)
    current = data_access.get_one_pager_status_row(one_pager_id)
    target = reject_target(current.data_product_status) if current else DRAFT
    row, rule = _check_review_decision(
        data_access, one_pager_id, target, user, roles, "reject"
    )
    comment = sanitize_text(comment)[:MAX_COMMENT_LENGTH]
    if rule.requires_comment and not comment:
        raise CommentRequiredError(REJECT_COMMENT_REQUIRED)

    review_comment = ReviewComment(
        id=0,
        one_pager_id=one_pager_id,
        version=row.version,
        section=None,
        reviewer_initials=user.initials,
        reviewer_name=user.display_name,
        comment=comment,
        resolved=False,
        created_at=now,
    )
    try:
        data_access.add_review_comment(review_comment)
    except Exception as e:
        logger.exception(f"Storing the reject comment of {one_pager_id} failed")
        log_event(
            rule.action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="add_review_comment",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e

    try:
        return apply_transitions(
            data_access, row, [rule], user, now=now, note=comment, reviewed=True
        )
    except Exception:
        try:
            data_access.delete_review_comment(review_comment)
        except Exception:
            logger.exception(f"Removing the reject comment of {one_pager_id} failed")
            log_event(
                rule.action,
                Outcome.COMPENSATION_FAILED,
                user=user.initials,
                one_pager_id=one_pager_id,
            )
        raise


@dataclass
class ApprovalPlan:
    """What an approval will do: the new version and the transitions."""

    version: str
    rules: list[TransitionRule]

    @property
    def data_product_status(self) -> str | None:
        """The DP status the approval sets, or None if it stays as it is."""
        dp = [r for r in self.rules if r.status_field == DP_STATUS_FIELD]
        return dp[0].to_status if dp else None


def plan_approval(row: OnePagerStatusRow) -> ApprovalPlan:
    """Version and transitions of approving ``row`` (Backend_Design.md §2-3).

    OP ``In Review`` → ``Approved``, plus the system DP transition: ``Ready
    for Development`` on the first approval (the DP is still ``In
    Definition``), ``In Enhancement`` on a re-approval. A DP that is already
    ``In Enhancement`` (updated again before development started) keeps it.

    Raises:
        InvalidTransitionError: The One Pager is not ``In Review``.

    """
    rules = [get_rule(OP_STATUS_FIELD, row.one_pager_status, APPROVED)]
    dp_target = (
        READY_FOR_DEVELOPMENT
        if row.data_product_status == IN_DEFINITION
        else IN_ENHANCEMENT
    )
    if row.data_product_status != dp_target:
        rules.append(get_rule(DP_STATUS_FIELD, row.data_product_status, dp_target))
    return ApprovalPlan(version=next_major(row.version), rules=rules)


def _approved_document(  # noqa: PLR0913 - every argument ends up in the file
    document: OnePagerDocument,
    row: OnePagerStatusRow,
    plan: ApprovalPlan,
    approved_row: OnePagerStatusRow,
    user: CurrentUser,
    now: datetime,
) -> OnePagerDocument:
    """Build the approved version file: operational fields from Delta."""
    timestamp = now.isoformat(timespec="seconds")
    return replace(
        document,
        structure_definition=row.structure_definition,
        data_product=row.data_product,
        one_pager_status=approved_row.one_pager_status,
        data_product_status=approved_row.data_product_status,
        version=plan.version,
        change_log=[
            *document.change_log,
            *(
                {
                    "version": plan.version,
                    "date": timestamp,
                    "author": user.display_name,
                    "summary": rule.summary,
                }
                for rule in plan.rules
            ),
        ],
        raw_content="",
    )


def _discard_if_unreferenced(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    version: str,
) -> None:
    """Remove a version file after a failed action, unless the row points to it.

    If rolling back the status row failed too, the row may still reference
    the file; then it must stay.
    """
    try:
        current = data_access.get_one_pager_status_row(one_pager_id)
    except Exception:
        logger.exception(f"Re-reading {one_pager_id} after a failed action failed")
        return
    if current is not None and current.version != version:
        document_store.discard_unreferenced(one_pager_id, version)


def approve_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
    *,
    roles: Collection[Actor],
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Approve a One Pager (Backend_Design.md §8, without Git until Phase 8).

    Only an Approver who is not Owner/SME of the One Pager may approve it.
    The version becomes ``1.0.0`` or the next MAJOR; the DP status changes as
    in ``plan_approval``; one change-log entry per status change is written.

    Write order as for a save: the approved version file first (nothing
    references it yet), then the status row and the change log in one
    ``apply_transitions``; if that fails the file is discarded again, so a
    failed approval changes nothing.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: Not an Approver, or Owner/SME of this One Pager.
        InvalidTransitionError: The One Pager is not ``In Review``.
        TransitionError: Storing failed; nothing changed.

    """
    require_identity(user, "approve_one_pager", one_pager_id)
    now = now or datetime.now(UTC)
    row, _ = _check_review_decision(
        data_access, one_pager_id, APPROVED, user, roles, "approve"
    )
    plan = plan_approval(row)
    approved_row = plan_transitions(row, plan.rules)

    document = data_access.read_document(one_pager_id, row.version)
    if document is None:
        msg = f"The document of {one_pager_id} v{row.version} could not be found."
        raise TransitionError(msg)
    approved = _approved_document(document, row, plan, approved_row, user, now)

    # A file for this version can only be left over from an approval that
    # failed before the status row pointed to it.
    document_store.discard_unreferenced(one_pager_id, plan.version)
    try:
        document_store.write(one_pager_id, approved, plan.version)
    except RuntimeError as e:
        logger.exception(f"Writing the approved {one_pager_id} failed")
        log_event(
            "approve",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="write_document",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e

    try:
        new_row = apply_transitions(
            data_access,
            row,
            plan.rules,
            user,
            now=now,
            reviewed=True,
            version=plan.version,
        )
    except Exception:
        _discard_if_unreferenced(
            data_access, document_store, one_pager_id, plan.version
        )
        raise
    log_event(
        "approve",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        version=plan.version,
    )
    return new_row
