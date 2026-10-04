"""Roles from group membership (identity plan Phase 6, Architecture.md §4)."""

import logging

import pytest

from onepagerapp.auth import resolve_roles
from onepagerapp.config import AppConfig
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, UseCaseInput
from onepagerapp.permissions import (
    PermissionDeniedError,
    can_create_one_pager,
    can_manage_use_cases,
)
from onepagerapp.state_machine import Actor
from onepagerapp.use_cases import (
    create_use_case,
    set_use_case_deprecated,
    update_use_case,
)
from onepagerapp.workflow import create_one_pager
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

USER = make_user("X0W")
DATAPLATENG_DEV = "BEC_BECOC001_LHX_DEV_DataPlatEng"


def _config(**overrides: str) -> AppConfig:
    return AppConfig(
        ONE_PAGER_APP_VOLUME_PATH="/Volumes/x",
        ONE_PAGER_APP_ENVIRONMENT="DEV",
        **overrides,
    )


def _data_access(*groups: str) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(FIXTURES_DIR), groups=frozenset(groups))


class _Recording(MockDataAccess):
    def __init__(self, memberships: dict[str, bool]) -> None:
        super().__init__(OnePagerDocumentStore(FIXTURES_DIR))
        self.memberships = memberships
        self.asked: list[dict[str, str]] = []

    def get_group_memberships(self, groups: dict[str, str]) -> dict[str, bool]:
        self.asked.append(groups)
        return self.memberships


class _Failing(MockDataAccess):
    def get_group_memberships(self, groups: dict[str, str]) -> dict[str, bool]:  # noqa: ARG002
        msg = "warehouse unavailable"
        raise RuntimeError(msg)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("memberships", "expected"),
    [
        ({"owner_sme": True}, {Actor.OWNER_SME_GROUP}),
        ({"approver": True}, {Actor.APPROVER}),
        ({"admin": True}, {Actor.ADMIN}),
        (
            {"owner_sme": True, "approver": True, "admin": False},
            {Actor.OWNER_SME_GROUP, Actor.APPROVER},
        ),
        ({"owner_sme": False, "approver": False, "admin": False}, set()),
        ({}, set()),
    ],
)
def test__resolve_roles__per_group_result(
    memberships: dict[str, bool], expected: set[Actor]
) -> None:
    data_access = _Recording(memberships)

    assert resolve_roles(USER, _config(), data_access) == expected
    assert data_access.asked == [_config().role_groups]


@pytest.mark.unit
def test__resolve_roles__dataplateng_member_gets_every_role() -> None:
    roles = resolve_roles(USER, _config(), _data_access(DATAPLATENG_DEV))

    assert roles == {Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN}


@pytest.mark.unit
def test__resolve_roles__non_member_is_viewer() -> None:
    assert resolve_roles(USER, _config(), _data_access("Other")) == frozenset()


@pytest.mark.unit
def test__resolve_roles__dedicated_groups() -> None:
    config = _config(
        ONE_PAGER_APP_GROUP_OWNER_SME="OPA-OwnerSME-{env}",
        ONE_PAGER_APP_GROUP_APPROVER="OPA-Approver-{env}",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",
    )

    roles = resolve_roles(USER, config, _data_access("OPA-Approver-DEV"))

    assert roles == {Actor.APPROVER}


@pytest.mark.unit
def test__resolve_roles__query_error_is_viewer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        roles = resolve_roles(
            USER, _config(), _Failing(OnePagerDocumentStore(FIXTURES_DIR))
        )

    assert roles == frozenset()
    assert "Group membership check failed" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    "group", [DATAPLATENG_DEV, "BEC_BECOC001_LHX_dev_DataPlatEng"]
)
def test__resolve_roles__group_names_ignore_case(group: str) -> None:
    roles = resolve_roles(USER, _config(), _data_access(group))

    assert roles == {Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN}


@pytest.mark.unit
def test__resolve_roles__directory_groups_ignore_case() -> None:
    roles = resolve_roles(
        USER,
        _config(),
        _Recording({}),
        directory_groups=["BEC_BECOC001_LHX_dev_DataPlatEng"],
    )

    assert roles == {Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN}


@pytest.mark.unit
def test__resolve_roles__directory_groups_when_the_query_fails() -> None:
    roles = resolve_roles(
        USER,
        _config(),
        _Failing(OnePagerDocumentStore(FIXTURES_DIR)),
        directory_groups=["BEC_BECOC001_LHX_dev_DataPlatEng"],
    )

    assert roles == {Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN}


@pytest.mark.unit
@pytest.mark.parametrize("user", [None, CurrentUser("guest@x.dk", "", "Guest")])
def test__resolve_roles__no_recognised_user_no_query(user: CurrentUser | None) -> None:
    data_access = _Recording({"admin": True})

    assert resolve_roles(user, _config(), data_access) == frozenset()
    assert data_access.asked == []


# ============================================================================
# Create and Use Case management follow the Owner/SME group (step 4)
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "allowed"),
    [
        ({Actor.OWNER_SME_GROUP}, True),
        ({Actor.OWNER_SME_GROUP, Actor.APPROVER}, True),
        ({Actor.APPROVER, Actor.ADMIN}, False),
        (set(), False),
    ],
)
def test__create_and_manage_follow_the_owner_sme_group(
    roles: set[Actor], allowed: bool
) -> None:
    assert can_create_one_pager(USER, roles) is allowed
    assert can_manage_use_cases(USER.initials, roles) is allowed


@pytest.mark.unit
def test__viewer_cannot_create_a_one_pager(
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    with pytest.raises(PermissionDeniedError):
        create_one_pager(
            valid_input, USER, mock_data_access, document_store, roles=frozenset()
        )

    assert mock_data_access.get_one_pager_status_row("OP-0003") is None


@pytest.mark.unit
def test__viewer_cannot_manage_use_cases(mock_data_access: MockDataAccess) -> None:
    data = UseCaseInput("p", "g", "s", "d", "High")
    before = mock_data_access.get_use_case("UC-001")

    with pytest.raises(PermissionDeniedError):
        create_use_case(mock_data_access, data, USER, roles=frozenset())
    with pytest.raises(PermissionDeniedError):
        update_use_case(mock_data_access, "UC-001", data, USER, roles={Actor.ADMIN})
    with pytest.raises(PermissionDeniedError):
        set_use_case_deprecated(
            mock_data_access, "UC-001", deprecated=True, user=USER, roles=frozenset()
        )

    assert mock_data_access.get_use_case("UC-001") == before
