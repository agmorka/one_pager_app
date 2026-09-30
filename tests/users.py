"""Test users with fixed corporate initials.

Tests build ``CurrentUser`` objects here instead of parsing usernames, so they
do not depend on the configured username format (``auth.py``). The sample
data (``tests/fixtures`` and ``MockDataAccess``) uses these people:

- ``ABR`` Alice Brown: Owner of OP-0001
- ``BSM`` Bob Smith: Owner of OP-0002
- ``CDA`` Charlie Davis: SME of OP-0001
- ``DPI`` Diana Prince: SME of OP-0002
"""

from onepagerapp.models import CurrentUser


def make_user(initials: str, display_name: str | None = None) -> CurrentUser:
    """Return a signed-in user with the given corporate initials."""
    return CurrentUser(
        username=f"{initials.lower()}adm@becoc001.onmicrosoft.com",
        initials=initials,
        display_name=display_name or initials,
    )
