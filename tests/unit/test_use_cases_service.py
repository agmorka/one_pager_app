"""Use Case writes: actor in the audit columns plus one security event each.

Identity plan Phase 3, step 3.
"""

import logging

import pytest

from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser, UseCaseInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.use_cases import (
    create_use_case,
    set_use_case_deprecated,
    update_use_case,
)
from tests.users import CREATOR_ROLES, make_user

X0W = make_user("X0W")
DATA = UseCaseInput(
    persona="Analyst",
    goal="See churn",
    scenario="Monthly review",
    decision_enabled="Retention budget",
    priority="High",
)


class _BrokenWrites(MockDataAccess):
    def update_use_case(self, *_: object) -> None:
        msg = "warehouse unavailable"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__use_case_writes__record_actor_and_log(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        use_case_id = create_use_case(mock_data_access, DATA, X0W, roles=CREATOR_ROLES)
        update_use_case(mock_data_access, "UC-001", DATA, X0W, roles=CREATOR_ROLES)
        set_use_case_deprecated(
            mock_data_access, "UC-002", deprecated=True, user=X0W, roles=CREATOR_ROLES
        )
        set_use_case_deprecated(
            mock_data_access, "UC-002", deprecated=False, user=X0W, roles=CREATOR_ROLES
        )

    created = mock_data_access.get_use_case(use_case_id)
    assert created.created_by == "X0W"
    assert created.last_updated_by == "X0W"
    assert mock_data_access.get_use_case("UC-001").last_updated_by == "X0W"
    assert mock_data_access.get_use_case("UC-002").last_updated_by == "X0W"
    assert caplog.messages == [
        f"action=create_use_case outcome=success user=X0W use_case_id={use_case_id}",
        "action=update_use_case outcome=success user=X0W use_case_id=UC-001",
        "action=deprecate_use_case outcome=success user=X0W use_case_id=UC-002",
        "action=restore_use_case outcome=success user=X0W use_case_id=UC-002",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("user", [CurrentUser("guest@x.dk", "", "Guest"), None])
def test__use_case_writes__refuse_unrecognised_user(
    mock_data_access: MockDataAccess, user: CurrentUser | None
) -> None:
    before = mock_data_access.get_use_case("UC-001")

    with pytest.raises(PermissionDeniedError):
        create_use_case(mock_data_access, DATA, user, roles=CREATOR_ROLES)
    with pytest.raises(PermissionDeniedError):
        update_use_case(mock_data_access, "UC-001", DATA, user, roles=CREATOR_ROLES)
    with pytest.raises(PermissionDeniedError):
        set_use_case_deprecated(
            mock_data_access, "UC-001", deprecated=True, user=user, roles=CREATOR_ROLES
        )

    assert mock_data_access.get_use_case("UC-001") == before


@pytest.mark.unit
def test__use_case_write__failure_is_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    broken = _BrokenWrites(mock_data_access._document_store)

    with (
        caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME),
        pytest.raises(RuntimeError),
    ):
        update_use_case(broken, "UC-001", DATA, X0W, roles=CREATOR_ROLES)

    assert caplog.messages == [
        "action=update_use_case outcome=failed user=X0W use_case_id=UC-001"
    ]
