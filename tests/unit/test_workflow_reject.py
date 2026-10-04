"""Reject: In Review → Draft with a mandatory comment (Req §6, Backend §13)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    CommentRequiredError,
    TransitionError,
    reject_one_pager,
)
from tests.helpers import (
    ADMIN_ROLES,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    DIANA,
    IN_REVIEW_ID,
    NOW,
    failing,
    update_status_row,
)


@pytest.mark.unit
def test__one_pager_in_review__reject__back_to_draft_same_version(
    mock_data_access: MockDataAccess,
) -> None:
    """Rejecting returns the One Pager to Draft and records the reviewer."""
    # When
    row = reject_one_pager(
        mock_data_access,
        IN_REVIEW_ID,
        APPROVER,
        "Incomplete",
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert (row.one_pager_status, row.data_product_status) == ("Draft", "In Definition")
    assert row.version == "0.3.0"
    assert (row.reviewed_by, row.reviewed_at) == ("CJO", NOW)
    stored = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert stored.one_pager_status == "Draft"


@pytest.mark.unit
def test__comment_with_html__reject__stores_sanitized_whole_document_comment(
    mock_data_access: MockDataAccess,
) -> None:
    """The reason is stored as an unresolved comment on the whole document."""
    # When
    reject_one_pager(
        mock_data_access,
        IN_REVIEW_ID,
        APPROVER,
        "  Data sources are <b>incomplete</b>  ",
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    [comment] = mock_data_access.get_review_comments(IN_REVIEW_ID)
    assert comment.comment == "Data sources are incomplete"
    assert comment.section is None
    assert comment.resolved is False
    assert (comment.reviewer_initials, comment.version) == ("CJO", "0.3.0")
    entry = mock_data_access.get_change_log(IN_REVIEW_ID)[0]
    assert (entry.from_status, entry.to_status) == ("In Review", "Draft")
    assert entry.summary == "One Pager rejected: Data sources are incomplete"


@pytest.mark.unit
def test__blank_comment__reject__raises_comment_required(
    mock_data_access: MockDataAccess,
) -> None:
    """A comment that is empty after sanitizing is refused."""
    # When / Then
    with pytest.raises(CommentRequiredError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, " <p> </p> ", roles=APPROVER_ROLES
        )
    row = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert row.one_pager_status == "In Review"
    assert mock_data_access.get_review_comments(IN_REVIEW_ID) == []


@pytest.mark.unit
def test__admin_without_approver_role__reject__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """Only Approvers can reject."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, "No", roles=ADMIN_ROLES
        )


@pytest.mark.unit
def test__approver_who_is_sme__reject__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """Segregation of duties: an SME cannot review their own One Pager."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="Owner or SME"):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, DIANA, "No", roles=APPROVER_ROLES
        )


@pytest.mark.unit
def test__approved_one_pager__reject__raises_invalid_transition(
    mock_data_access: MockDataAccess,
) -> None:
    """Only a One Pager In Review can be rejected."""
    # When / Then
    with pytest.raises(InvalidTransitionError):
        reject_one_pager(
            mock_data_access, APPROVED_ID, APPROVER, "No", roles=APPROVER_ROLES
        )


@pytest.mark.unit
def test__change_log_write_fails__reject__removes_the_comment(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed transition leaves no comment behind."""
    # Given
    monkeypatch.setattr(
        mock_data_access, "append_change_log_entries", failing("warehouse down")
    )

    # When / Then
    with pytest.raises(TransitionError):
        reject_one_pager(
            mock_data_access, IN_REVIEW_ID, APPROVER, "Incomplete", roles=APPROVER_ROLES
        )
    row = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert row.one_pager_status == "In Review"
    assert mock_data_access.get_review_comments(IN_REVIEW_ID) == []


@pytest.mark.unit
def test__one_pager_rejected_before__reject_again__keeps_both_comments(
    mock_data_access: MockDataAccess,
) -> None:
    """Rejection comments of earlier review cycles are kept."""
    # Given a rejection, then a new submit
    reject_one_pager(
        mock_data_access, IN_REVIEW_ID, APPROVER, "First", roles=APPROVER_ROLES, now=NOW
    )
    update_status_row(mock_data_access, IN_REVIEW_ID, one_pager_status="In Review")

    # When
    reject_one_pager(
        mock_data_access,
        IN_REVIEW_ID,
        APPROVER,
        "Second",
        roles=APPROVER_ROLES,
        now=NOW.replace(hour=11),
    )

    # Then
    comments = mock_data_access.get_review_comments(IN_REVIEW_ID)
    assert [c.comment for c in comments] == ["First", "Second"]
    assert len({c.id for c in comments}) == 2
