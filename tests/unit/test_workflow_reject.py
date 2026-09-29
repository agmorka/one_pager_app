"""Reject: In Review → Draft with a mandatory comment (Req §6, Backend §13)."""

from datetime import UTC, datetime
from typing import NoReturn

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import ChangeLogEntry
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import (
    CommentRequiredError,
    TransitionError,
    reject_one_pager,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
APPROVER = resolve_current_user("cjo@bec.dk")
ROLES = frozenset({Actor.APPROVER})
IN_REVIEW_ID = "OP-0002"  # seeded In Review, Owner BS, SME DP, v0.3.0


@pytest.mark.unit
def test__reject__back_to_draft_with_comment(
    mock_data_access: MockDataAccess,
) -> None:
    row = reject_one_pager(
        mock_data_access,
        IN_REVIEW_ID,
        APPROVER,
        "  Data sources are <b>incomplete</b>  ",
        roles=ROLES,
        now=NOW,
    )

    assert (row.one_pager_status, row.data_product_status) == ("Draft", "In Definition")
    assert row.version == "0.3.0"
    assert (row.reviewed_by, row.reviewed_at) == ("CJO", NOW)
    stored = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert stored.one_pager_status == "Draft"

    [comment] = mock_data_access.get_review_comments(IN_REVIEW_ID)
    assert comment.comment == "Data sources are incomplete"
    assert comment.section is None
    assert comment.resolved is False
    assert (comment.reviewer_initials, comment.version) == ("CJO", "0.3.0")

    entry = mock_data_access.get_change_log(IN_REVIEW_ID)[0]
    assert (entry.from_status, entry.to_status) == ("In Review", "Draft")
    assert entry.summary == "One Pager rejected: Data sources are incomplete"


@pytest.mark.unit
def test__reject__comment_is_mandatory(mock_data_access: MockDataAccess) -> None:
    with pytest.raises(CommentRequiredError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, " <p> </p> ", roles=ROLES
        )

    assert mock_data_access.get_one_pager_status_row(IN_REVIEW_ID).one_pager_status == (
        "In Review"
    )
    assert mock_data_access.get_review_comments(IN_REVIEW_ID) == []


@pytest.mark.unit
def test__reject__approvers_only(mock_data_access: MockDataAccess) -> None:
    with pytest.raises(PermissionDeniedError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, "No", roles={Actor.ADMIN}
        )


@pytest.mark.unit
def test__reject__segregation_of_duties(mock_data_access: MockDataAccess) -> None:
    sme = resolve_current_user("dp@bec.dk")  # SME of OP-0002
    with pytest.raises(PermissionDeniedError, match="Owner or SME"):
        reject_one_pager(mock_data_access, IN_REVIEW_ID, sme, "No", roles=ROLES)


@pytest.mark.unit
def test__reject__only_in_review(mock_data_access: MockDataAccess) -> None:
    with pytest.raises(InvalidTransitionError):
        reject_one_pager(mock_data_access, "OP-0001", APPROVER, "No", roles=ROLES)


@pytest.mark.unit
def test__reject__failed_transition_removes_the_comment(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(entries: list[ChangeLogEntry]) -> NoReturn:
        msg = "warehouse down"
        raise RuntimeError(msg)

    monkeypatch.setattr(mock_data_access, "append_change_log_entries", fail)

    with pytest.raises(TransitionError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, "Incomplete", roles=ROLES
        )

    assert mock_data_access.get_one_pager_status_row(IN_REVIEW_ID).one_pager_status == (
        "In Review"
    )
    assert mock_data_access.get_review_comments(IN_REVIEW_ID) == []


@pytest.mark.unit
def test__rejection_comments_are_kept_across_cycles(
    mock_data_access: MockDataAccess,
) -> None:
    reject_one_pager(
        mock_data_access, IN_REVIEW_ID, APPROVER, "First", roles=ROLES, now=NOW
    )
    row = mock_data_access._status_rows[IN_REVIEW_ID]
    row.one_pager_status = "In Review"

    later = NOW.replace(hour=11)
    reject_one_pager(
        mock_data_access, IN_REVIEW_ID, APPROVER, "Second", roles=ROLES, now=later
    )

    comments = mock_data_access.get_review_comments(IN_REVIEW_ID)
    assert [c.comment for c in comments] == ["First", "Second"]
    assert len({c.id for c in comments}) == 2
