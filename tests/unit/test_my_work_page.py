"""AppTest smoke tests for the My work landing page and its service."""

from datetime import timedelta

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.my_work import get_my_work
from onepagerapp.timeutils import age_label
from tests.helpers import (
    ALICE,
    ALL_GROUP_ROLES,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    CREATOR_ROLES,
    IN_REVIEW_ID,
    NOW,
    page_app,
)


@pytest.mark.unit
def test__owner_of_in_review__get_my_work__listed_as_waiting(
    mock_data_access: MockDataAccess,
) -> None:
    """Bob owns OP-0002 (In Review): it waits for review, nothing to review."""
    # When
    work = get_my_work(mock_data_access, BOB, ALL_GROUP_ROLES)

    # Then
    assert [r.one_pager_id for r in work.waiting_for_review] == [IN_REVIEW_ID]
    assert work.drafts == []
    assert work.review_queue == []  # his own One Pager is never in his queue


@pytest.mark.unit
def test__approver__get_my_work__review_queue_listed(
    mock_data_access: MockDataAccess,
) -> None:
    """An Approver who owns nothing sees the In Review One Pager to review."""
    # When
    work = get_my_work(mock_data_access, APPROVER, APPROVER_ROLES)

    # Then
    assert [r.one_pager_id for r in work.review_queue] == [IN_REVIEW_ID]
    assert work.action_count == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(hours=3), "today"),
        (timedelta(days=1), "1 day"),
        (timedelta(days=5), "5 days"),
    ],
)
def test__timestamp__age_label__whole_days(delta: timedelta, expected: str) -> None:
    """Ages are shown in whole days."""
    assert age_label(NOW - delta, now=NOW) == expected


@pytest.mark.unit
def test__owner__open_my_work__sections_rendered(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Alice sees her approved One Pager and can open it."""
    # When
    at = page_app("my_work.py", mock_data_access, ALICE, CREATOR_ROLES).run()

    # Then
    assert not at.exception
    assert at.title[0].value.startswith("My work")
    assert [m.label for m in at.metric][:3] == [
        "Drafts to finish",
        "Comments to resolve",
        "Waiting for review",
    ]
    at.button(key="mywork_approved_open_OP-0001").click().run()
    assert switched == ["views/preview.py"]


@pytest.mark.unit
def test__approver__open_my_work__review_button_opens_review_mode(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Approver's queue on My work opens Preview in review mode."""
    # Given
    at = page_app("my_work.py", mock_data_access, APPROVER, APPROVER_ROLES).run()
    assert "Waiting for your review" in [s.value for s in at.subheader]

    # When
    at.button(key=f"mywork_review_{IN_REVIEW_ID}").click().run()

    # Then
    assert switched == ["views/preview.py"]
    assert at.session_state["preview_review_mode"] == IN_REVIEW_ID


@pytest.mark.unit
def test__viewer_with_nothing__open_my_work__points_to_registry(
    mock_data_access: MockDataAccess,
) -> None:
    """A user with no One Pagers is pointed to the Registry."""
    # When
    at = page_app("my_work.py", mock_data_access, APPROVER, frozenset()).run()

    # Then
    assert not at.exception
    assert "Nothing needs your attention" in at.info[0].value
    assert at.button(key="mywork_registry_empty")
