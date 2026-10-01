"""User identity helpers.

Derives the corporate initials (the authorization key, Architecture.md §4) from
the authenticated Databricks username, and the display name from the workspace
directory (``directory.py``). The username format (accepted domains, suffixes,
valid initials) is configuration, not code (``AppConfig.user_domains``,
``username_suffixes``, ``initials_pattern``).
"""

from onepagerapp.config import AppConfig
from onepagerapp.directory import DirectoryUser
from onepagerapp.models import CurrentUser
from onepagerapp.state_machine import Actor


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


def resolve_roles(user: CurrentUser | None, config: AppConfig) -> frozenset[Actor]:
    """Group roles (Approver, Admin) of the user.

    Architecture.md §4 backs these roles with Unity Catalog groups whose names
    are not decided yet (Phase 2, item 4.1). Until then the members are
    configured by initials in ``ONE_PAGER_APP_APPROVERS`` and
    ``ONE_PAGER_APP_ADMINS``; switching to the group lookup only changes this
    function. Owner/SME is per record and never returned here.
    """
    if not (user and user.initials):
        return frozenset()
    roles: set[Actor] = set()
    if user.initials in config.approver_initials:
        roles.add(Actor.APPROVER)
    if user.initials in config.admin_initials:
        roles.add(Actor.ADMIN)
    return frozenset(roles)
