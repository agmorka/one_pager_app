"""Use Case writes: actor in the audit columns plus one security event each.

Identity plan Phase 3, step 3.
"""

from collections.abc import Callable

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser, UseCaseInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.use_cases import (
    create_use_case,
    set_use_case_deprecated,
    update_use_case,
)
from tests.helpers import (
    CREATOR_ROLES,
    UNRECOGNISED,
    audit_messages,
    capture_audit,
    failing,
    make_user,
)

X0W = make_user("X0W")
DATA = UseCaseInput(
    persona="Analyst",
    goal="See churn",
    scenario="Monthly review",
    decision_enabled="Retention budget",
    priority="High",
)
WRITES: dict[str, Callable[[MockDataAccess, CurrentUser | None], object]] = {
    "create": lambda da, user: create_use_case(da, DATA, user, roles=CREATOR_ROLES),
    "update": lambda da, user: update_use_case(
        da, "UC-001", DATA, user, roles=CREATOR_ROLES
    ),
    "deprecate": lambda da, user: set_use_case_deprecated(
        da, "UC-001", deprecated=True, user=user, roles=CREATOR_ROLES
    ),
}


@pytest.mark.unit
def test__owner_sme__create_use_case__actor_recorded_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """A create stores the actor in both audit columns and logs an event."""
    # Given
    capture_audit(caplog)

    # When
    use_case_id = create_use_case(mock_data_access, DATA, X0W, roles=CREATOR_ROLES)

    # Then
    created = mock_data_access.get_use_case(use_case_id)
    assert (created.created_by, created.last_updated_by) == ("X0W", "X0W")
    assert audit_messages(caplog) == [
        f"action=create_use_case outcome=success user=X0W use_case_id={use_case_id}"
    ]


@pytest.mark.unit
def test__owner_sme__update_use_case__actor_recorded_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """An update stores the actor and logs an event."""
    # Given
    capture_audit(caplog)

    # When
    update_use_case(mock_data_access, "UC-001", DATA, X0W, roles=CREATOR_ROLES)

    # Then
    assert mock_data_access.get_use_case("UC-001").last_updated_by == "X0W"
    assert audit_messages(caplog) == [
        "action=update_use_case outcome=success user=X0W use_case_id=UC-001"
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("deprecated", "action"), [(True, "deprecate"), (False, "restore")]
)
def test__owner_sme__set_use_case_deprecated__actor_recorded_and_logged(
    mock_data_access: MockDataAccess,
    caplog: pytest.LogCaptureFixture,
    deprecated: bool,
    action: str,
) -> None:
    """Deprecating and restoring store the actor and log their own action."""
    # Given
    capture_audit(caplog)

    # When
    set_use_case_deprecated(
        mock_data_access,
        "UC-002",
        deprecated=deprecated,
        user=X0W,
        roles=CREATOR_ROLES,
    )

    # Then
    assert mock_data_access.get_use_case("UC-002").last_updated_by == "X0W"
    assert audit_messages(caplog) == [
        f"action={action}_use_case outcome=success user=X0W use_case_id=UC-002"
    ]


@pytest.mark.unit
@pytest.mark.parametrize("user", [UNRECOGNISED, None], ids=["no-initials", "none"])
@pytest.mark.parametrize("write", WRITES.values(), ids=WRITES.keys())
def test__unrecognised_user__use_case_write__raises_and_changes_nothing(
    mock_data_access: MockDataAccess,
    user: CurrentUser | None,
    write: Callable[[MockDataAccess, CurrentUser | None], object],
) -> None:
    """A user without initials cannot write Use Cases."""
    # Given
    before = mock_data_access.get_use_case("UC-001")

    # When / Then
    with pytest.raises(PermissionDeniedError):
        write(mock_data_access, user)
    assert mock_data_access.get_use_case("UC-001") == before


@pytest.mark.unit
def test__warehouse_fails__update_use_case__failure_logged(
    mock_data_access: MockDataAccess,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed write is logged as a failed security event."""
    # Given
    monkeypatch.setattr(
        mock_data_access, "update_use_case", failing("warehouse unavailable")
    )
    capture_audit(caplog)

    # When / Then
    with pytest.raises(RuntimeError):
        update_use_case(mock_data_access, "UC-001", DATA, X0W, roles=CREATOR_ROLES)
    assert audit_messages(caplog) == [
        "action=update_use_case outcome=failed user=X0W use_case_id=UC-001"
    ]
