"""Review queue (UI_Design.md §4.3) and the Approver role."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError, can_review
from onepagerapp.review import get_review_queue
from onepagerapp.state_machine import Actor
from tests.helpers import ADMIN_ROLES, APPROVER, APPROVER_ROLES, IN_REVIEW_ID


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "allowed"),
    [(APPROVER_ROLES, True), (ADMIN_ROLES, False), (frozenset(), False)],
    ids=["approver", "admin", "viewer"],
)
def test__roles__can_review__approvers_only(
    roles: frozenset[Actor], allowed: bool
) -> None:
    """Only Approvers review."""
    # When
    result = can_review(roles)

    # Then
    assert result is allowed


@pytest.mark.unit
def test__older_one_pager_in_review__get_review_queue__in_review_oldest_first(
    mock_data_access: MockDataAccess,
) -> None:
    """The queue holds In Review items, the longest waiting first."""
    # Given an extra One Pager In Review since 1 September
    older = replace(
        mock_data_access._status_rows["OP-0001"],
        one_pager_id="OP-0009",
        one_pager_status="In Review",
        data_product_status="In Definition",
        last_updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    )
    mock_data_access._status_rows["OP-0009"] = older

    # When
    queue = get_review_queue(mock_data_access, APPROVER, APPROVER_ROLES)

    # Then
    assert [r.one_pager_id for r in queue] == ["OP-0009", IN_REVIEW_ID]
    assert all(r.one_pager_status == "In Review" for r in queue)


@pytest.mark.unit
def test__admin_without_approver_role__get_review_queue__refused(
    mock_data_access: MockDataAccess,
) -> None:
    """The queue is for Approvers only."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        get_review_queue(mock_data_access, APPROVER, ADMIN_ROLES)
