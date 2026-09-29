"""User identity helpers.

Derives the corporate initials (the authorization key, Architecture.md §4) and
a display name from the authenticated Databricks username. The extraction is
isolated here so it can be updated if the username format changes.
"""

import re

from onepagerapp.models import CurrentUser

# Corporate usernames look like "<initials>ADM@BECOC001.onmicrosoft.com"
# (Requirements_and_Scope.md §2), e.g. "MJOADM@..." -> "MJO".
_CORPORATE_USERNAME = re.compile(r"^([A-Za-z]{2,4})ADM$", re.IGNORECASE)
_PLAIN_INITIALS = re.compile(r"^[A-Za-z]{2,4}$")
_SEPARATORS = re.compile(r"[.\-_\s]+")
_MIN_PARTS = 2
_MAX_PARTS = 3


def initials_from_username(username: str | None) -> str:
    """Derive a user's corporate initials from their Databricks identity.

    This is the single implementation used everywhere initials are needed
    (authorization, Delta audit columns, change log); ``permissions.
    extract_initials`` delegates here.

    Examples:
        "MJOADM@BECOC001.onmicrosoft.com" -> "MJO"  (documented corporate format)
        "mjo@bec.dk"                      -> "MJO"  (bare initials)
        "alice.brown@company.com"         -> "AB"
        "local-dev-user@mock"             -> "LDU"  (fallback: first letters)
        None / ""                         -> "??"

    Returns:
        Upper-case initials, or "??" when nothing usable is available.

    """
    local_part = (username or "").split("@", 1)[0].strip()
    corporate = _CORPORATE_USERNAME.match(local_part)
    if corporate:
        return corporate.group(1).upper()
    if _PLAIN_INITIALS.match(local_part):
        return local_part.upper()
    tokens = [token for token in _SEPARATORS.split(local_part) if token]
    if len(tokens) >= _MIN_PARTS:
        return "".join(token[0] for token in tokens[:_MAX_PARTS]).upper()
    if tokens:
        return tokens[0][:3].upper()
    return "??"


def display_name_from_username(username: str) -> str:
    """Best-effort human-readable name from a username (no directory lookup).

    Examples:
        "alice.brown@company.com" -> "Alice Brown"
        "MJOADM@BECOC001.onmicrosoft.com" -> "MJO"

    """
    if not username:
        return "Unknown user"
    local_part = username.split("@", 1)[0].strip()
    corporate = _CORPORATE_USERNAME.match(local_part)
    if corporate:
        return corporate.group(1).upper()
    parts = [p for p in _SEPARATORS.split(local_part) if p]
    return " ".join(p.capitalize() for p in parts) or username


def resolve_current_user(username: str) -> CurrentUser:
    """Build the CurrentUser for an authenticated username."""
    return CurrentUser(
        username=username,
        initials=initials_from_username(username),
        display_name=display_name_from_username(username),
    )
