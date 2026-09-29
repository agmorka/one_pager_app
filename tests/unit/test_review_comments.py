"""Section-level review comments and resolving them (Backend_Design.md §13)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import (
    SECTION_LABELS,
    CommentError,
    add_review_comment,
    resolve_review_comment,
    section_label,
)
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import reject_one_pager

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
APPROVER = resolve_current_user("cjo@bec.dk")
OWNER = resolve_current_user("bob.smith@company.com")  # Owner of OP-0002
SME = resolve_current_user("dp@bec.dk")  # SME of OP-0002
ROLES = frozenset({Actor.APPROVER})
OP = "OP-0002"  # seeded In Review, v0.3.0


@pytest.mark.unit
def test__section_labels_cover_every_editor_section() -> None:
    assert section_label(None) == "Whole document"
    assert section_label("dataSources") == "Data Sources"
    assert section_label("somethingNew") == "somethingNew"
    assert "businessProblemStatement" in SECTION_LABELS


@pytest.mark.unit
def test__add_comment__stored_unresolved_on_the_section(
    mock_data_access: MockDataAccess,
) -> None:
    comment = add_review_comment(
        mock_data_access,
        OP,
        APPROVER,
        "dataSources",
        "  Add the <i>refresh</i> frequency ",
        roles=ROLES,
        now=NOW,
    )

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
def test__add_comment__guards(mock_data_access: MockDataAccess) -> None:
    with pytest.raises(PermissionDeniedError):
        add_review_comment(mock_data_access, OP, APPROVER, None, "x", roles=set())
    with pytest.raises(PermissionDeniedError, match="Owner or SME"):
        add_review_comment(mock_data_access, OP, SME, None, "x", roles=ROLES)
    with pytest.raises(ValueError, match="Unknown section"):
        add_review_comment(mock_data_access, OP, APPROVER, "nope", "x", roles=ROLES)
    with pytest.raises(ValueError, match="Write a comment"):
        add_review_comment(mock_data_access, OP, APPROVER, None, " ", roles=ROLES)
    with pytest.raises(InvalidTransitionError):
        add_review_comment(
            mock_data_access, "OP-0001", APPROVER, None, "x", roles=ROLES
        )
    assert mock_data_access.get_review_comments(OP) == []


def _rejected_with_comments(data_access: MockDataAccess) -> list[int]:
    add_review_comment(
        data_access, OP, APPROVER, "useCases", "Link UC-001", roles=ROLES, now=NOW
    )
    reject_one_pager(
        data_access,
        OP,
        APPROVER,
        "See comments",
        roles=ROLES,
        now=NOW.replace(minute=5),
    )
    return [c.id for c in data_access.get_review_comments(OP)]


@pytest.mark.unit
def test__resolve__by_owner_or_sme_while_draft(
    mock_data_access: MockDataAccess,
) -> None:
    first, second = _rejected_with_comments(mock_data_access)

    resolve_review_comment(mock_data_access, OP, first, OWNER, now=NOW)
    resolve_review_comment(mock_data_access, OP, second, SME, now=NOW)

    comments = mock_data_access.get_review_comments(OP)
    assert [(c.resolved, c.resolved_by, c.resolved_at) for c in comments] == [
        (True, "BS", NOW),
        (True, "DP", NOW),
    ]
    with pytest.raises(CommentError, match="already resolved"):
        resolve_review_comment(mock_data_access, OP, first, OWNER)


@pytest.mark.unit
def test__resolve__guards(mock_data_access: MockDataAccess) -> None:
    [first, _] = _rejected_with_comments(mock_data_access)

    with pytest.raises(PermissionDeniedError, match="Owner or an SME"):
        resolve_review_comment(mock_data_access, OP, first, APPROVER)
    with pytest.raises(CommentError):
        resolve_review_comment(mock_data_access, OP, 999, OWNER)

    rows = mock_data_access._status_rows
    rows[OP] = replace(rows[OP], one_pager_status="In Review")
    with pytest.raises(PermissionDeniedError, match="reworked"):
        resolve_review_comment(mock_data_access, OP, first, OWNER)


@pytest.mark.unit
def test__resolve__only_comments_of_that_one_pager(
    mock_data_access: MockDataAccess,
) -> None:
    [first, _] = _rejected_with_comments(mock_data_access)
    rows = mock_data_access._status_rows
    rows["OP-0001"] = replace(rows["OP-0001"], one_pager_status="Draft Update")
    alice = resolve_current_user("alice.brown@company.com")  # Owner of OP-0001

    with pytest.raises(CommentError):
        resolve_review_comment(mock_data_access, "OP-0001", first, alice)
    assert not mock_data_access.get_review_comments(OP)[0].resolved
