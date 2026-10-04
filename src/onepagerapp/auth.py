"""User identity helpers.

Derives the corporate initials (the authorization key, Architecture.md §4) from
the authenticated Databricks username, and the display name from the workspace
directory (``directory.py``). The username format (accepted domains, suffixes,
valid initials) is configuration, not code (``AppConfig.user_domains``,
``username_suffixes``, ``initials_pattern``).
"""

import logging
import re
from collections.abc import Iterable
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


def strip_account_name(name: str, account_names: Iterable[str]) -> str:
    """Remove admin account names such as "X0WADM" from a directory name.

    The directory name of an admin account can carry the account, e.g.
    "Agnieszka Kępkowska (X0WADM)" or "X0WADM - Agnieszka Kępkowska"; the app
    shows only the person's name. Brackets and separators left empty are
    removed too. Matching ignores case and only takes whole words.
    """
    accounts = sorted({a for a in account_names if a}, key=len, reverse=True)
    for account in accounts:
        pattern = rf"(?<!\w){re.escape(account)}(?!\w)"
        name = re.sub(pattern, " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\(\s*\)|\[\s*\]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -\N{EN DASH},;:|/")


def display_name(
    directory_user: DirectoryUser | None,
    initials: str,
    account_names: Iterable[str] = (),
) -> str:
    """Return the name shown for the user: ``givenName familyName``, ``displayName``.

    ``account_names`` (e.g. "X0WADM") are removed from the name
    (``strip_account_name``). Falls back to the initials when the directory
    has no name (or could not be read). A name is never guessed from the
    username.
    """
    if directory_user is not None:
        for candidate in (directory_user.full_name, directory_user.display_name):
            name = strip_account_name(candidate or "", account_names)
            if name:
                return name
    return initials


def admin_account_names(
    username: str | None, initials: str, config: AppConfig
) -> set[str]:
    """Account names that may appear in a directory name: "x0wadm", "X0WADM".

    The username, its user part and the initials with every non-empty suffix;
    never the bare initials (they are shown next to the name anyway, and
    could be part of a real name).
    """
    username = (username or "").strip()
    local_part = username.partition("@")[0]
    names = {username}
    if local_part.upper() != initials.upper():
        names.add(local_part)
    if initials:
        names.update(initials + suffix for suffix in config.username_suffixes if suffix)
    return names


def resolve_current_user(
    username: str, config: AppConfig, directory_user: DirectoryUser | None = None
) -> CurrentUser:
    """Build the CurrentUser for an authenticated username.

    ``initials`` is empty when the username is not recognised
    (``initials_from_username`` returns None); such a user matches no Owner,
    SME, Approver or Admin (and app.py refuses them access). The display name
    comes from ``directory_user`` (``display_name``), without the admin
    account name; it is for display only. The email is the corporate address
    of the initials (``AppConfig.email_for``, x0w@bec.dk), not the directory
    email of the admin account.
    """
    initials = initials_from_username(username, config) or ""
    name = display_name(
        directory_user, initials, admin_account_names(username, initials, config)
    )
    return CurrentUser(
        username=username,
        initials=initials,
        display_name=name or username,
        email=config.email_for(initials),
    )


# Role per key of AppConfig.role_groups.
_GROUP_ROLES = {
    "owner_sme": Actor.OWNER_SME_GROUP,
    "approver": Actor.APPROVER,
    "admin": Actor.ADMIN,
}


def resolve_roles(
    user: CurrentUser | None,
    config: AppConfig,
    data_access: "DataAccess",
    directory_groups: Iterable[str] = (),
) -> frozenset[Actor]:
    """Group roles of the user: Owner/SME group, Approver, Admin.

    A role applies when the user is a member of its group
    (``AppConfig.role_groups``). Group names are compared **ignoring case**
    (``BEC_BECOC001_LHX_DEV_DataPlatEng`` matches the real group
    ``BEC_BECOC001_LHX_dev_DataPlatEng``). Membership is:

    - checked once, as the user, in SQL (``DataAccess.get_group_memberships``,
      which finds the real spelling of the name; covers nested groups), or
    - listed in the user's own directory entry (``directory_groups``, SCIM
      ``Me`` read with the user's token; direct memberships).

    Every recognised user is also a Viewer, which needs no role. Owner/SME of
    a specific One Pager is per record (``one_pager_authorized_users``) and
    never returned here.

    Fails closed: if the SQL check fails, only the directory groups count
    (Viewer only without them) and the error is logged; the app still opens.
    """
    if not (user and user.initials):
        return frozenset()
    groups = config.role_groups
    member_of = {g.casefold() for g in directory_groups if g}
    try:
        memberships = data_access.get_group_memberships(groups)
    except Exception:
        logger.exception(
            "Group membership check failed for %s; only the directory groups count",
            user.initials,
        )
        memberships = {}
    roles = frozenset(
        role
        for key, role in _GROUP_ROLES.items()
        if memberships.get(key) is True or groups[key].casefold() in member_of
    )
    logger.info(
        "Roles of %s: %s (groups: %s)",
        user.initials,
        ", ".join(sorted(r.value for r in roles)) or "Viewer only",
        ", ".join(sorted(set(groups.values()))),
    )
    return roles
