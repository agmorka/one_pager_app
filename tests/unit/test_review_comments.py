"""Section-level review comments and resolving them (Backend_Design.md §13)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import (
    SECTION_LABELS,
    CommentError,
    add_review_comment,
    resolve_review_comment,
    section_label,
)
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import reject_one_pager
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    DIANA,
    IN_REVIEW_ID,
    NOW,
    update_status_row,
)

OP = IN_REVIEW_ID  # seeded In Review, v0.3.0; Owner BOB, SME DIANA


@pytest.fixture
def comment_ids(mock_data_access: MockDataAccess) -> list[int]:
    """Comment on Use Cases, reject with a reason; return both comment IDs."""
    add_review_comment(
        mock_data_access,
        OP,
        APPROVER,
        "useCases",
        "Link UC-001",
        roles=APPROVER_ROLES,
        now=NOW,
    )
    reject_one_pager(
        mock_data_access,
        OP,
        APPROVER,
        "See comments",
        roles=APPROVER_ROLES,
        now=NOW.replace(minute=5),
    )
    return [c.id for c in mock_data_access.get_review_comments(OP)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("section", "label"),
    [
        (None, "Whole document"),
        ("dataSources", "Data Sources"),
        ("somethingNew", "somethingNew"),
    ],
)
def test__section_key__section_label__readable_label(
    section: str | None, label: str
) -> None:
    """Known sections get their label; unknown keys are shown as they are."""
    # When
    result = section_label(section)

    # Then
    assert result == label


@pytest.mark.unit
def test__editor_sections__section_labels__cover_the_problem_statement() -> None:
    """Every editor section can be commented on."""
    # When / Then
    assert "businessProblemStatement" in SECTION_LABELS


@pytest.mark.unit
def test__approver__add_comment_on_section__stored_unresolved(
    mock_data_access: MockDataAccess,
) -> None:
    """A comment is sanitized and stored on its section; the status stays."""
    # When
    comment = add_review_comment(
        mock_data_access,
        OP,
        APPROVER,
        "dataSources",
        "  Add the <i>refresh</i> frequency ",
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert comment.comment == "Add the refresh frequency"
    [stored] = mock_data_access.get_review_comments(OP)
    assert (stored.section, stored.version, stored.resolved) == (
        "dataSources",
        "0.3.0",
        False,
    )
    assert stored.reviewer_initials == "CJO"
    assert mock_data_access.get_one_pager_status_row(OP).one_pager_status == (
        "In Review"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("one_pager_id", "user", "roles", "section", "text", "error", "match"),
    [
        (OP, APPROVER, set(), None, "x", PermissionDeniedError, ""),
        (OP, DIANA, APPROVER_ROLES, None, "x", PermissionDeniedError, "Owner or SME"),
        (OP, APPROVER, APPROVER_ROLES, "nope", "x", ValueError, "Unknown section"),
        (OP, APPROVER, APPROVER_ROLES, None, " ", ValueError, "Write a comment"),
        (APPROVED_ID, APPROVER, APPROVER_ROLES, None, "x", InvalidTransitionError, ""),
    ],
    ids=["not-approver", "own-one-pager", "unknown-section", "blank", "not-in-review"],
)
def test__guard_violated__add_comment__refused_and_nothing_stored(
    mock_data_access: MockDataAccess,
    one_pager_id: str,
    user: object,
    roles: object,
    section: str | None,
    text: str,
    error: type[Exception],
    match: str,
) -> None:
    """Only Approvers comment on others' One Pagers In Review, on known sections."""
    # When / Then
    with pytest.raises(error, match=match):
        add_review_comment(
            mock_data_access, one_pager_id, user, section, text, roles=roles
        )
    assert mock_data_access.get_review_comments(OP) == []


@pytest.mark.unit
def test__rejected_one_pager__owner_and_sme_resolve__both_resolved(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """The Owner and the SME resolve comments while the One Pager is a Draft."""
    # Given
    first, second = comment_ids

    # When
    resolve_review_comment(mock_data_access, OP, first, BOB, now=NOW)
    resolve_review_comment(mock_data_access, OP, second, DIANA, now=NOW)

    # Then
    comments = mock_data_access.get_review_comments(OP)
    assert [(c.resolved, c.resolved_by, c.resolved_at) for c in comments] == [
        (True, "BSM", NOW),
        (True, "DPI", NOW),
    ]


@pytest.mark.unit
def test__resolved_comment__resolve_again__raises(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """A comment is resolved once."""
    # Given
    resolve_review_comment(mock_data_access, OP, comment_ids[0], BOB, now=NOW)

    # When / Then
    with pytest.raises(CommentError, match="already resolved"):
        resolve_review_comment(mock_data_access, OP, comment_ids[0], BOB)


@pytest.mark.unit
def test__approver__resolve__raises_permission_denied(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """Only the Owner or an SME resolves comments."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        resolve_review_comment(mock_data_access, OP, comment_ids[0], APPROVER)


@pytest.mark.unit
def test__unknown_comment_id__resolve__raises(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """An unknown comment cannot be resolved."""
    # When / Then
    with pytest.raises(CommentError):
        resolve_review_comment(mock_data_access, OP, 999, BOB)


@pytest.mark.unit
def test__one_pager_back_in_review__resolve__raises_permission_denied(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """Comments are resolved while reworking, not during the next review."""
    # Given
    update_status_row(mock_data_access, OP, one_pager_status="In Review")

    # When / Then
    with pytest.raises(PermissionDeniedError, match="reworked"):
        resolve_review_comment(mock_data_access, OP, comment_ids[0], BOB)


@pytest.mark.unit
def test__comment_of_other_one_pager__resolve__raises_and_unchanged(
    mock_data_access: MockDataAccess, comment_ids: list[int]
) -> None:
    """A comment can only be resolved through its own One Pager."""
    # Given Alice owns OP-0001, which is being reworked
    update_status_row(mock_data_access, APPROVED_ID, one_pager_status="Draft Update")

    # When / Then
    with pytest.raises(CommentError):
        resolve_review_comment(mock_data_access, APPROVED_ID, comment_ids[0], ALICE)
    assert not mock_data_access.get_review_comments(OP)[0].resolved
