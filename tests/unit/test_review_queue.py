"""Review queue (UI_Design.md §4.3) and the Approver role."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError, can_review
from onepagerapp.review import get_review_queue
from onepagerapp.state_machine import Actor
from tests.users import make_user

APPROVER = make_user("CJO")


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
