"""Review of One Pagers by Approvers (UI_Design.md §4.3, Backend_Design.md §13).

``get_review_queue`` is the Approver's work queue: every One Pager ``In
Review``, oldest submission first.

Pure Python — no Streamlit.
"""

from collections.abc import Collection

from onepagerapp.audit import log_permission_denied
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import CurrentUser, OnePagerStatusRow
from onepagerapp.permissions import PermissionDeniedError, can_review
from onepagerapp.state_machine import IN_REVIEW, Actor

REVIEW_DENIED_MESSAGE = "Only Approvers can review One Pagers."


def check_can_review(
    user: CurrentUser | None, roles: Collection[Actor]
) -> None:
    """Raise ``PermissionDeniedError`` (logged) unless the user is an Approver."""
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
