"""Review queue (UI_Design.md §4.3) and the interim Approver/Admin roles."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.auth import resolve_current_user, resolve_roles
from onepagerapp.config import AppConfig
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError, can_review
from onepagerapp.review import get_review_queue
from onepagerapp.state_machine import Actor

APPROVER = resolve_current_user("cjo@bec.dk")


def _config(**overrides: str) -> AppConfig:
    return AppConfig(ONE_PAGER_APP_VOLUME_PATH="/Volumes/x", **overrides)


@pytest.mark.unit
def test__resolve_roles__from_configured_initials() -> None:
    config = _config(ONE_PAGER_APP_APPROVERS="cjo, XY", ONE_PAGER_APP_ADMINS="adm")

    assert resolve_roles(APPROVER, config) == {Actor.APPROVER}
    assert resolve_roles(resolve_current_user("adm@bec.dk"), config) == {Actor.ADMIN}
    assert resolve_roles(resolve_current_user("ab@bec.dk"), config) == frozenset()
    assert resolve_roles(None, config) == frozenset()


@pytest.mark.unit
def test__resolve_roles__nobody_by_default() -> None:
    assert resolve_roles(APPROVER, _config()) == frozenset()


@pytest.mark.unit
def test__can_review__approvers_only() -> None:
    assert can_review({Actor.APPROVER})
    assert not can_review({Actor.ADMIN})
    assert not can_review(set())


@pytest.mark.unit
def test__review_queue__in_review_only_oldest_first(
    mock_data_access: MockDataAccess,
) -> None:
    older = replace(
        mock_data_access._status_rows["OP-0001"],
        one_pager_id="OP-0009",
        one_pager_status="In Review",
        data_product_status="In Definition",
        last_updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    )
    mock_data_access._status_rows["OP-0009"] = older

    queue = get_review_queue(mock_data_access, APPROVER, {Actor.APPROVER})

    assert [r.one_pager_id for r in queue] == ["OP-0009", "OP-0002"]
    assert all(r.one_pager_status == "In Review" for r in queue)


@pytest.mark.unit
def test__review_queue__denied_for_non_approvers(
    mock_data_access: MockDataAccess,
) -> None:
    with pytest.raises(PermissionDeniedError):
        get_review_queue(mock_data_access, APPROVER, {Actor.ADMIN})
