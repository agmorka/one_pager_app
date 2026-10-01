"""User identity helpers.

Derives the corporate initials (the authorization key, Architecture.md §4) from
the authenticated Databricks username, and the display name from the workspace
directory (``directory.py``). The username format (accepted domains, suffixes,
valid initials) is configuration, not code (``AppConfig.user_domains``,
``username_suffixes``, ``initials_pattern``).
"""

import logging
from typing import TYPE_CHECKING

from onepagerapp.config import AppConfig
from onepagerapp.directory import DirectoryUser
from onepagerapp.models import CurrentUser
from onepagerapp.state_machine import Actor

if TYPE_CHECKING:
    from onepagerapp.data_access.base import DataAccess

logger = logging.getLogger(__name__)


def initials_from_username(username: str | None, config: AppConfig) -> str | None:
    """Derive a user's corporate initials from their Databricks username.

    This is the single implementation used everywhere initials are needed
    (authorization, Delta audit columns, change log).

    The username must have an accepted domain. The longest configured suffix is
    stripped from the user part, the rest is upper-cased and must match the
    initials pattern. Nothing is guessed: a username that does not follow the
    format gets no initials, so it can never be mapped to someone else's.

    Examples (default settings):
        "x0wadm@becoc001.onmicrosoft.com" -> "X0W"
        "X0WADM@BECOC001.onmicrosoft.com" -> "X0W"
        "x0w@becoc001.onmicrosoft.com"    -> None  (no "adm" suffix)
        "x0wadm@company.com"              -> None  (unknown domain)
        None / ""                         -> None

    Returns:
        Upper-case initials, or None when the username is not recognised.

    """
    local_part, at, domain = (username or "").strip().partition("@")
    if not at or domain.lower() not in config.user_domains:
        return None
    local_part = local_part.lower()
    for suffix in config.username_suffixes:  # longest first
        if local_part.endswith(suffix):
            initials = local_part.removesuffix(suffix).upper()
            if initials and config.initials_pattern.match(initials):
                return initials
    return None


def display_name(directory_user: DirectoryUser | None, initials: str) -> str:
    """Return the name shown for the user: ``givenName familyName``, ``displayName``.

    Falls back to the initials when the directory has no name (or could not be
    read). A name is never guessed from the username.
    """
    if directory_user is not None:
        name = directory_user.full_name or directory_user.display_name
        if name:
            return name
    return initials


def resolve_current_user(
    username: str, config: AppConfig, directory_user: DirectoryUser | None = None
) -> CurrentUser:
    """Build the CurrentUser for an authenticated username.

    ``initials`` is empty when the username is not recognised
    (``initials_from_username`` returns None); such a user matches no Owner,
    SME, Approver or Admin (and app.py refuses them access). The display name
    comes from ``directory_user`` (``display_name``); it is for display only.
    """
    initials = initials_from_username(username, config)
    return CurrentUser(
        username=username,
        initials=initials or "",
        display_name=display_name(directory_user, initials or "") or username,
        email=(directory_user.email if directory_user else None) or "",
    )


# Role per key of AppConfig.role_groups.
_GROUP_ROLES = {
    "owner_sme": Actor.OWNER_SME_GROUP,
    "approver": Actor.APPROVER,
    "admin": Actor.ADMIN,
}


def resolve_roles(
    user: CurrentUser | None, config: AppConfig, data_access: "DataAccess"
) -> frozenset[Actor]:
    """Group roles of the user: Owner/SME group, Approver, Admin.

    Membership of the groups in ``AppConfig.role_groups`` is checked once, as
    the user (``DataAccess.get_group_memberships``). Every recognised user is
    also a Viewer, which needs no role. Owner/SME of a specific One Pager is
    per record (``one_pager_authorized_users``) and never returned here.

    Fails closed: if the check fails, the user gets no role (Viewer only) and
    the error is logged; the app still opens.
    """
    if not (user and user.initials):
        return frozenset()
    try:
        memberships = data_access.get_group_memberships(config.role_groups)
    except Exception:
        logger.exception(
            "Group membership check failed; %s gets the Viewer role only",
            user.initials,
        )
        return frozenset()
    return frozenset(
        role for key, role in _GROUP_ROLES.items() if memberships.get(key) is True
    )
