"""The signed-in user's work list, for the "My work" landing page.

Collects what needs the user's attention across One Pagers: their drafts,
review comments to resolve, their One Pagers waiting for review, the
Approver's review queue, and the edit locks they hold.

Pure Python — no Streamlit.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field

from onepagerapp.data_access.base import DataAccess
from onepagerapp.locking import get_active_locks
from onepagerapp.models import (
    CurrentUser,
    OnePagerStatusRow,
    RegistryFilter,
    RegistryRow,
)
from onepagerapp.permissions import EDITABLE_STATUSES, can_review
from onepagerapp.state_machine import APPROVED, IN_REVIEW, READY_FOR_REVIEW, Actor

logger = logging.getLogger(__name__)

# One Pagers a user is Owner/SME of are few; one page reads them all.
MY_ONE_PAGERS_LIMIT = 500


@dataclass
class MyWork:
    """What needs the user's attention.

    Attributes:
        drafts: The user's One Pagers in Draft / Draft Update.
        open_comments: One Pager ID -> number of unresolved review comments,
            for the user's drafts that have any.
        waiting_for_review: The user's One Pagers Ready for Review / In Review.
        approved: The user's approved One Pagers.
        review_queue: One Pagers waiting for the user's review (Approvers
            only; the user's own One Pagers are left out), oldest first.
        locked_by_me: IDs of the One Pagers the user holds an edit lock on.

    """

    drafts: list[RegistryRow] = field(default_factory=list)
    open_comments: dict[str, int] = field(default_factory=dict)
    waiting_for_review: list[RegistryRow] = field(default_factory=list)
    approved: list[RegistryRow] = field(default_factory=list)
    review_queue: list[OnePagerStatusRow] = field(default_factory=list)
    locked_by_me: set[str] = field(default_factory=set)

    @property
    def action_count(self) -> int:
        """Items the user is expected to act on: drafts plus reviews."""
        return len(self.drafts) + len(self.review_queue)


def my_one_pagers(data_access: DataAccess, initials: str) -> list[RegistryRow]:
    """Return the One Pagers where ``initials`` are Owner or SME."""
    page = data_access.get_registry(
        RegistryFilter(authorized_initials=initials), 1, MY_ONE_PAGERS_LIMIT
    )
    return page.rows


def pending_review_count(
    data_access: DataAccess, roles: Collection[Actor]
) -> int | None:
    """Count the One Pagers In Review for an Approver; None for other users."""
    if not can_review(roles):
        return None
    return len(data_access.get_one_pager_status_rows(IN_REVIEW))


def get_my_work(
    data_access: DataAccess, user: CurrentUser, roles: Collection[Actor]
) -> MyWork:
    """Collect the user's work list (see ``MyWork``)."""
    work = MyWork()
    if not user.initials:
        return work
    mine = my_one_pagers(data_access, user.initials)
    for row in mine:
        if row.one_pager_status in EDITABLE_STATUSES:
            work.drafts.append(row)
        elif row.one_pager_status in (READY_FOR_REVIEW, IN_REVIEW):
            work.waiting_for_review.append(row)
        elif row.one_pager_status == APPROVED:
            work.approved.append(row)
    for row in work.drafts:
        unresolved = sum(
            not c.resolved for c in data_access.get_review_comments(row.one_pager_id)
        )
        if unresolved:
            work.open_comments[row.one_pager_id] = unresolved
    if can_review(roles):
        own = {row.one_pager_id for row in mine}
        work.review_queue = [
            row
            for row in data_access.get_one_pager_status_rows(IN_REVIEW)
            if row.one_pager_id not in own
        ]
    ids = [row.one_pager_id for row in mine]
    if ids:
        work.locked_by_me = {
            one_pager_id
            for one_pager_id, lock in get_active_locks(data_access, ids).items()
            if lock.locked_by_initials == user.initials
        }
    return work
