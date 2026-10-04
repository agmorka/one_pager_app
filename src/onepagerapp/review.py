"""Review of One Pagers by Approvers (UI_Design.md §4.3, Backend_Design.md §13).

``get_review_queue`` is the Approver's work queue: every One Pager ``In
Review``, oldest submission first.

Review comments: an Approver adds section-level comments while a One Pager is
``In Review`` (``add_review_comment``); its Owner/SMEs mark them as resolved
while they rework it (``resolve_review_comment``). Comments are never deleted
— they are part of the audit trail.

Pure Python — no Streamlit.
"""

import logging
from collections.abc import Collection
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.models import CurrentUser, OnePagerStatusRow, ReviewComment
from onepagerapp.permissions import (
    EDITABLE_STATUSES,
    PermissionDeniedError,
    can_review,
    is_owner_or_sme,
    require_identity,
)
from onepagerapp.state_machine import IN_REVIEW, Actor, InvalidTransitionError
from onepagerapp.validation import sanitize_text

logger = logging.getLogger(__name__)

REVIEW_DENIED_MESSAGE = "Only Approvers can review One Pagers."
MAX_COMMENT_LENGTH = 2000
COMMENT_REQUIRED_MESSAGE = "Write a comment."
COMMENT_FAILED_MESSAGE = "The comment could not be saved. Please retry."

# Sections an Approver can comment on: top-level schema properties
# (Data_Model.md §3, review_comments.section) in editor-tab order. None is a
# comment on the whole document.
SECTION_LABELS: dict[str | None, str] = {
    None: "Whole document",
    "description": "Description",
    "businessProblemStatement": "Business Problem Statement",
    "useCases": "Use Cases",
    "businessRequirements": "Business Requirements",
    "dataSources": "Data Sources",
    "dataProductPreview": "Data Product Preview",
    "dataClassification": "Classification",
    "retentionRequirements": "Retention Requirements",
    "dataGovernanceArtifacts": "Governance",
    "outOfScope": "Out of Scope",
    "openQuestions": "Open Questions",
    "assumptions": "Assumptions",
}


class CommentError(RuntimeError):
    """A comment could not be stored. The message is safe to show to users."""


def section_label(section: str | None) -> str:
    """Readable name of a comment's section (unknown keys are shown as-is)."""
    return SECTION_LABELS.get(section, section or SECTION_LABELS[None])


def check_can_review(user: CurrentUser | None, roles: Collection[Actor]) -> None:
    """Raise ``PermissionDeniedError`` (logged) unless the user is an Approver."""
    require_identity(user, "review")
    if can_review(roles):
        return
    log_permission_denied("review", user=user.initials if user else None)
    raise PermissionDeniedError(REVIEW_DENIED_MESSAGE)


def get_review_queue(
    data_access: DataAccess,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> list[OnePagerStatusRow]:
    """One Pagers waiting for review, oldest submission first (UI_Design §4.3).

    Raises:
        PermissionDeniedError: The user is not an Approver.

    """
    check_can_review(user, roles)
    return data_access.get_one_pager_status_rows(IN_REVIEW)


def add_review_comment(  # noqa: PLR0913 - every argument is part of the comment
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    section: str | None,
    comment: str,
    *,
    roles: Collection[Actor],
    now: datetime | None = None,
) -> ReviewComment:
    """Add an Approver's comment on a section (Backend_Design.md §13).

    Allowed while the One Pager is ``In Review``, for Approvers who are not
    its Owner/SME (the same segregation of duties as Approve and Reject). The
    comment is unresolved and records the version it was made on.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: Not an Approver, or Owner/SME of this One Pager.
        InvalidTransitionError: The One Pager is not ``In Review``.
        ValueError: Unknown section or empty comment.
        CommentError: Storing failed.

    """
    require_identity(user, "add_review_comment", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
    check_can_review(user, roles)
    if is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id)):
        log_permission_denied(
            "add_review_comment", user=user.initials, one_pager_id=one_pager_id
        )
        msg = "You cannot review a One Pager on which you are Owner or SME."
        raise PermissionDeniedError(msg)
    if row.one_pager_status != IN_REVIEW:
        msg = f"Comments can only be added while the One Pager is {IN_REVIEW}."
        raise InvalidTransitionError(msg)
    if section not in SECTION_LABELS:
        msg = f"Unknown section {section!r}."
        raise ValueError(msg)
    text = sanitize_text(comment)[:MAX_COMMENT_LENGTH]
    if not text:
        raise ValueError(COMMENT_REQUIRED_MESSAGE)

    review_comment = ReviewComment(
        id=0,
        one_pager_id=one_pager_id,
        version=row.version,
        section=section,
        reviewer_initials=user.initials,
        reviewer_name=user.display_name,
        comment=text,
        resolved=False,
        created_at=now or datetime.now(UTC),
    )
    try:
        data_access.add_review_comment(review_comment)
    except Exception as e:
        logger.exception(f"Storing a review comment on {one_pager_id} failed")
        log_event(
            "add_review_comment",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
        )
        raise CommentError(COMMENT_FAILED_MESSAGE) from e
    log_event(
        "add_review_comment",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        section=section,
    )
    return review_comment


def resolve_denied_reason(
    user: CurrentUser | None,
    one_pager_status: str,
    owner_or_sme: bool,  # noqa: FBT001
) -> str | None:
    """Why the user may not resolve comments now, or None if they may."""
    if not (user and owner_or_sme):
        return "Only the Owner or an SME of this One Pager can resolve comments."
    if one_pager_status not in EDITABLE_STATUSES:
        return "Comments are resolved while the One Pager is being reworked (Draft)."
    return None


def resolve_review_comment(
    data_access: DataAccess,
    one_pager_id: str,
    comment_id: int,
    user: CurrentUser,
    *,
    now: datetime | None = None,
) -> None:
    """Mark a review comment as resolved (Backend_Design.md §13).

    Only the Owner/SMEs of the One Pager may, while it is ``Draft`` or
    ``Draft Update``; ``resolved_by`` and ``resolved_at`` are set.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: Not Owner/SME, or the status does not allow it.
        CommentError: The comment is unknown or already resolved, or storing
            failed.

    """
    require_identity(user, "resolve_review_comment", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    reason = resolve_denied_reason(user, row.one_pager_status, owner_or_sme)
    if reason:
        log_permission_denied(
            "resolve_review_comment", user=user.initials, one_pager_id=one_pager_id
        )
        raise PermissionDeniedError(reason)
    try:
        resolved = data_access.resolve_review_comment(
            one_pager_id,
            comment_id,
            resolved_by=user.initials,
            resolved_at=now or datetime.now(UTC),
        )
    except Exception as e:
        logger.exception(f"Resolving comment {comment_id} of {one_pager_id} failed")
        log_event(
            "resolve_review_comment",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
        )
        raise CommentError(COMMENT_FAILED_MESSAGE) from e
    if not resolved:
        msg = "This comment is already resolved."
        raise CommentError(msg)
    log_event(
        "resolve_review_comment",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        comment_id=comment_id,
    )
