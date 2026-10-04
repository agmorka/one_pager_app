"""Owner/SME sync with the document (Backend_Design.md §11).

``one_pager_authorized_users`` follows the Owner and SMEs of the document.
"""

from dataclasses import dataclass, field

from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import AuthorizedUser, OnePagerDocument


@dataclass
class AuthorizedUsersDiff:
    """Changes that make ``one_pager_authorized_users`` match a document."""

    inserts: list[AuthorizedUser] = field(default_factory=list)
    updates: list[AuthorizedUser] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.inserts or self.updates or self.deletes)


def authorized_users_from_document(
    one_pager_id: str, document: OnePagerDocument
) -> list[AuthorizedUser]:
    """Build the Owner (role ``owner``) and SME (role ``sme``) rows of a document."""
    users = [
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=document.owner_initials,
            user_name=document.owner_name,
            user_email=document.owner_email,
            user_team=document.owner_team,
            role="owner",
        )
    ]
    users.extend(
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=str(sme.get("initials") or ""),
            user_name=str(sme.get("name") or ""),
            user_email=str(sme.get("email") or ""),
            user_team=sme.get("team") or None,
            role="sme",
        )
        for sme in document.smes
    )
    return [u for u in users if u.user_initials]


def diff_authorized_users(
    current: list[AuthorizedUser], desired: list[AuthorizedUser]
) -> AuthorizedUsersDiff:
    """Compare the table rows with the document's Owner/SMEs, keyed by initials.

    New initials are inserted, rows whose name, email, team or role changed
    are updated, and initials no longer listed are deleted.
    """
    by_initials = {u.user_initials: u for u in current}
    wanted = {u.user_initials: u for u in desired}
    diff = AuthorizedUsersDiff()
    for initials, user in wanted.items():
        existing = by_initials.get(initials)
        if existing is None:
            diff.inserts.append(user)
        elif (
            existing.user_name,
            existing.user_email,
            existing.user_team,
            existing.role,
        ) != (user.user_name, user.user_email, user.user_team, user.role):
            diff.updates.append(user)
    diff.deletes = [initials for initials in by_initials if initials not in wanted]
    return diff


def sync_authorized_users(
    data_access: DataAccess,
    current: list[AuthorizedUser],
    desired: list[AuthorizedUser],
) -> AuthorizedUsersDiff:
    """Make ``one_pager_authorized_users`` match ``desired`` (insert/update/delete).

    Inserts run before deletes, so a failure part-way never leaves the One
    Pager with fewer editors than either the old or the new list.
    """
    diff = diff_authorized_users(current, desired)
    if diff.inserts:
        data_access.insert_authorized_users(diff.inserts)
    if diff.updates:
        data_access.update_authorized_users(diff.updates)
    if diff.deletes:
        one_pager_id = (current or desired)[0].one_pager_id
        data_access.delete_authorized_users(one_pager_id, diff.deletes)
    return diff
