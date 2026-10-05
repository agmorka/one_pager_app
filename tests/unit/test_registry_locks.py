"""Registry lock indicator (UI_Design.md §4.1, Requirements_and_Scope.md §10)."""

from datetime import timedelta

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from tests.helpers import (
    APPROVED_ID,
    BOB,
    IN_REVIEW_ID,
    MAJA,
    failing,
    live_lock,
    page_app,
)


@pytest.fixture
def locked(mock_data_access: MockDataAccess) -> MockDataAccess:
    """Lock OP-0001 by MJO (active) and OP-0002 by BSM (expired)."""
    mock_data_access._locks = {
        APPROVED_ID: live_lock(APPROVED_ID, MAJA, timedelta(minutes=20)),
        IN_REVIEW_ID: live_lock(IN_REVIEW_ID, BOB, -timedelta(minutes=1)),
    }
    return mock_data_access


@pytest.mark.unit
def test__active_and_expired_locks__open_registry__holder_of_active_lock_only(
    locked: MockDataAccess, switched: list[str]
) -> None:
    """Only active locks are shown, with their holder."""
    # When
    at = page_app("registry.py", locked, roles=frozenset()).run()

    # Then
    assert not at.exception
    locks = list(at.dataframe[0].value["Being edited by"])
    assert any(cell.startswith("\N{LOCK} MJO since ") for cell in locks)
    assert not any("BSM" in cell for cell in locks)


@pytest.mark.unit
def test__locks_unreadable__open_registry__table_shown_with_unknown_lock(
    locked: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock read error does not break the table."""
    # Given
    monkeypatch.setattr(locked, "get_locks", failing("locks table unreachable"))

    # When
    at = page_app("registry.py", locked, roles=frozenset()).run()

    # Then
    assert not at.exception
    assert not at.error
    assert any("Lock status is unavailable" in c.value for c in at.caption)
    table = at.dataframe[0].value
    assert APPROVED_ID in list(table["ID"])
    assert "?" in list(table["Being edited by"])
