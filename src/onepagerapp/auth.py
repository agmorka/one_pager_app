"""User identity helpers.

Derives the corporate initials (the authorization key, Architecture.md §4) and
a display name from the authenticated Databricks username. The extraction is
isolated here so it can be updated if the username format changes.
"""

import re

from onepagerapp.models import CurrentUser

# Corporate admin accounts: <INITIALS>ADM@BECOC001.onmicrosoft.com -> INITIALS
_CORPORATE_PATTERN = re.compile(r"^([A-Za-z]{2,5})ADM@", re.IGNORECASE)
_SEPARATORS = re.compile(r"[.\-_\s]+")
_MIN_PARTS = 2


def initials_from_username(username: str) -> str:
    """Extract initials from a Databricks username, email or display name.

    Examples:
        "MJOADM@BECOC001.onmicrosoft.com" -> "MJO"
        "alice.brown@company.com" -> "AB"
        "Alice Brown" -> "AB"
        "local-dev-user@mock" -> "LD"

    Returns:
        Uppercase initials, or "?" for an empty username.

    """
    if not username:
        return "?"

    match = _CORPORATE_PATTERN.match(username)
    if match:
        return match.group(1).upper()

    local_part = username.split("@", 1)[0]
    parts = [p for p in _SEPARATORS.split(local_part) if p]
    if len(parts) >= _MIN_PARTS:
        return (parts[0][0] + parts[1][0]).upper()
    return local_part[:3].upper()


def display_name_from_username(username: str) -> str:
    """Best-effort human-readable name from a username (no directory lookup).

    Examples:
        "alice.brown@company.com" -> "Alice Brown"
        "MJOADM@BECOC001.onmicrosoft.com" -> "MJO"

    """
    if not username:
        return "Unknown user"
    match = _CORPORATE_PATTERN.match(username)
    if match:
        return match.group(1).upper()
    local_part = username.split("@", 1)[0]
    parts = [p for p in _SEPARATORS.split(local_part) if p]
    return " ".join(p.capitalize() for p in parts) or username


def resolve_current_user(username: str) -> CurrentUser:
    """Build the CurrentUser for an authenticated username."""
    return CurrentUser(
        username=username,
        initials=initials_from_username(username),
        display_name=display_name_from_username(username),
    )
