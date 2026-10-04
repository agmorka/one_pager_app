"""Roles from group membership (identity plan Phase 6, Architecture.md §4)."""

import logging
from pathlib import Path

import pytest

from onepagerapp.auth import resolve_roles
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
from tests.helpers import (
    ALL_GROUP_ROLES,
    INTERIM_GROUP_DEV,
    UNRECOGNISED,
    config,
    failing,
    make_user,
    mock_data_access,
)

USER = make_user("X0W")
DEV = config(ONE_PAGER_APP_ENVIRONMENT="DEV")
DIRECTORY_SPELLING = "BEC_BECOC001_LHX_dev_DataPlatEng"


def _member_of(tmp_path: Path, *groups: str) -> MockDataAccess:
    """Return mock data in which the user is a member of ``groups``."""
    return mock_data_access(tmp_path, groups=frozenset(groups))


def _answering(
    monkeypatch: pytest.MonkeyPatch, data_access: MockDataAccess, answer: dict
) -> list[dict[str, str]]:
    """Make the membership query answer ``answer``; return the groups asked."""
    asked: list[dict[str, str]] = []

    def get_group_memberships(groups: dict[str, str]) -> dict[str, bool]:
        asked.append(groups)
        return answer

    monkeypatch.setattr(data_access, "get_group_memberships", get_group_memberships)
    return asked


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
def test__membership_answer__resolve_roles__one_role_per_true_group(
    mock_data_access: MockDataAccess,
    monkeypatch: pytest.MonkeyPatch,
    memberships: dict[str, bool],
    expected: set[Actor],
) -> None:
    """Each group the user is a member of gives its role; one query asks all."""
    # Given
    asked = _answering(monkeypatch, mock_data_access, memberships)

    # When
    roles = resolve_roles(USER, DEV, mock_data_access)

    # Then
    assert roles == expected
    assert asked == [DEV.role_groups]


@pytest.mark.unit
@pytest.mark.parametrize("group", [INTERIM_GROUP_DEV, DIRECTORY_SPELLING])
def test__interim_group_member__resolve_roles__every_role(
    tmp_path: Path, group: str
) -> None:
    """A DataPlatEng member gets every role, whatever the case of the name."""
    # When
    roles = resolve_roles(USER, DEV, _member_of(tmp_path, group))

    # Then
    assert roles == ALL_GROUP_ROLES


@pytest.mark.unit
def test__member_of_no_role_group__resolve_roles__viewer(tmp_path: Path) -> None:
    """Without a role group the user is a Viewer."""
    # When
    roles = resolve_roles(USER, DEV, _member_of(tmp_path, "Other"))

    # Then
    assert roles == frozenset()


@pytest.mark.unit
def test__dedicated_role_groups__resolve_roles__role_of_that_group(
    tmp_path: Path,
) -> None:
    """With one group per role, membership decides each role."""
    # Given
    dedicated = config(
        ONE_PAGER_APP_ENVIRONMENT="DEV",
        ONE_PAGER_APP_GROUP_OWNER_SME="OPA-OwnerSME-{env}",
        ONE_PAGER_APP_GROUP_APPROVER="OPA-Approver-{env}",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",
    )

    # When
    roles = resolve_roles(USER, dedicated, _member_of(tmp_path, "OPA-Approver-DEV"))

    # Then
    assert roles == {Actor.APPROVER}


@pytest.mark.unit
def test__membership_query_fails__resolve_roles__viewer_and_logged(
    mock_data_access: MockDataAccess,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed check falls back to Viewer (fail closed) and is logged."""
    # Given
    monkeypatch.setattr(mock_data_access, "get_group_memberships", failing())

    # When
    with caplog.at_level(logging.ERROR):
        roles = resolve_roles(USER, DEV, mock_data_access)

    # Then
    assert roles == frozenset()
    assert "Group membership check failed" in caplog.text


@pytest.mark.unit
def test__directory_groups_in_other_case__resolve_roles__every_role(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory groups count, ignoring case."""
    # Given
    _answering(monkeypatch, mock_data_access, {})

    # When
    roles = resolve_roles(
        USER, DEV, mock_data_access, directory_groups=[DIRECTORY_SPELLING]
    )

    # Then
    assert roles == ALL_GROUP_ROLES


@pytest.mark.unit
def test__membership_query_fails_with_directory_groups__resolve_roles__every_role(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory groups still apply when the query fails."""
    # Given
    monkeypatch.setattr(mock_data_access, "get_group_memberships", failing())

    # When
    roles = resolve_roles(
        USER, DEV, mock_data_access, directory_groups=[DIRECTORY_SPELLING]
    )

    # Then
    assert roles == ALL_GROUP_ROLES


@pytest.mark.unit
@pytest.mark.parametrize("user", [None, UNRECOGNISED], ids=["none", "no-initials"])
def test__unrecognised_user__resolve_roles__no_roles_and_no_query(
    mock_data_access: MockDataAccess,
    monkeypatch: pytest.MonkeyPatch,
    user: CurrentUser | None,
) -> None:
    """Without a recognised user no membership is checked."""
    # Given
    asked = _answering(monkeypatch, mock_data_access, {"admin": True})

    # When
    roles = resolve_roles(user, DEV, mock_data_access)

    # Then
    assert roles == frozenset()
    assert asked == []


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
def test__roles__can_create_and_manage__owner_sme_group_only(
    roles: set[Actor], allowed: bool
) -> None:
    """Creating One Pagers and managing Use Cases need the Owner/SME group."""
    # When
    results = (
        can_create_one_pager(USER, roles),
        can_manage_use_cases(USER.initials, roles),
    )

    # Then
    assert results == (allowed, allowed)


@pytest.mark.unit
def test__viewer__create_one_pager__raises_and_writes_nothing(
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A Viewer cannot create a One Pager."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        create_one_pager(
            valid_input, USER, mock_data_access, document_store, roles=frozenset()
        )
    assert mock_data_access.get_one_pager_status_row("OP-0003") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "write",
    [
        lambda da, data: create_use_case(da, data, USER, roles=frozenset()),
        lambda da, data: update_use_case(da, "UC-001", data, USER, roles={Actor.ADMIN}),
        lambda da, _: set_use_case_deprecated(
            da, "UC-001", deprecated=True, user=USER, roles=frozenset()
        ),
    ],
    ids=["create", "update-as-admin", "deprecate"],
)
def test__user_without_owner_sme_group__use_case_write__raises_and_unchanged(
    mock_data_access: MockDataAccess, write: object
) -> None:
    """Viewers and Admins cannot manage Use Cases."""
    # Given
    before = mock_data_access.get_use_case("UC-001")
    data = UseCaseInput("p", "g", "s", "d", "High")

    # When / Then
    with pytest.raises(PermissionDeniedError):
        write(mock_data_access, data)  # type: ignore[operator]
    assert mock_data_access.get_use_case("UC-001") == before
