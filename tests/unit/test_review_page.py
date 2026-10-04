"""AppTest smoke tests for the Review page (UI_Design.md §4.3)."""

from collections.abc import Callable
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.state_machine import Actor
from tests.helpers import (
    ADMIN_ROLES,
    APPROVER,
    APPROVER_ROLES,
    IN_REVIEW_ID,
    failing,
    page_app,
)


def _review_page(
    data_access: MockDataAccess, roles: frozenset[Actor] = APPROVER_ROLES
) -> AppTest:
    """Return the Review page for the Approver CJO."""
    return page_app("review.py", data_access, APPROVER, roles)


@pytest.mark.unit
def test__one_pager_in_review__open_review_page__listed_with_count(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The queue lists OP-0002 and counts it."""
    # When
    at = _review_page(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.title[0].value == "Review Queue"
    assert at.metric[0].value == "1"
    assert any(IN_REVIEW_ID in m.value for m in at.markdown)


@pytest.mark.unit
def test__queued_one_pager__click_open__preview_in_review_mode(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Opening an item switches to the Preview in review mode."""
    # Given
    at = _review_page(mock_data_access).run()

    # When
    at.button(key="review_open_OP-0002").click().run()

    # Then
    assert switched == ["views/preview.py"]
    assert at.session_state["preview_one_pager_id"] == IN_REVIEW_ID
    assert at.session_state["preview_review_mode"] == IN_REVIEW_ID


@pytest.mark.unit
def test__nothing_in_review__open_review_page__empty_message(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """An empty queue says so."""
    # Given
    mock_data_access._status_rows.pop(IN_REVIEW_ID)

    # When
    at = _review_page(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.info[0].value == "Nothing waiting for your review."


@pytest.mark.unit
def test__viewer__open_review_page__approvers_only_message(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Non-approvers see an explanation and no queue."""
    # When
    at = _review_page(mock_data_access, roles=frozenset()).run()

    # Then
    assert not at.exception
    assert "Only Approvers" in at.info[0].value
    assert not at.metric


@pytest.mark.unit
def test__queue_fails_to_load__open_review_page__friendly_error_with_retry(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A load error hides the internals and offers a retry."""
    # Given
    monkeypatch.setattr(
        mock_data_access,
        "get_one_pager_status_rows",
        failing("warehouse unreachable: secret internals"),
    )

    # When
    at = _review_page(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.error[0].value == "Couldn't load the review queue. Please retry."
    assert "secret" not in at.error[0].value
    assert at.button(key="review_retry")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "shown"),
    [(APPROVER_ROLES, True), (frozenset(), False), (ADMIN_ROLES, False)],
    ids=["approver", "viewer", "admin"],
)
def test__roles__navigation_entries__review_page_for_approvers_only(
    import_app_module: Callable[[str], ModuleType],
    roles: frozenset[Actor],
    shown: bool,
) -> None:
    """Only Approvers see the Review page."""
    # Given
    app = import_app_module("app")

    # When
    titles = [title for _, title in app.navigation_entries(roles)]

    # Then
    assert ("Review" in titles) is shown
