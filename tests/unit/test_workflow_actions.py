"""Preview workflow action handlers (the dialogs themselves need a browser)."""

from collections.abc import Callable
from types import ModuleType

import pytest
import streamlit as st

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    IN_REVIEW_ID,
    NEW_ID,
    make_user,
    update_status_row,
)


@pytest.fixture
def actions(
    monkeypatch: pytest.MonkeyPatch, import_app_module: Callable[[str], ModuleType]
) -> ModuleType:
    """Return ``adapters.workflow_actions`` with a plain dict as session state."""
    monkeypatch.setattr(st, "session_state", {})
    return import_app_module("adapters.workflow_actions")


@pytest.mark.unit
def test__owners_draft__cancel_and_report__cancelled_with_flash(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A successful cancel reports no error and flashes a message."""
    # When
    error = actions.cancel_and_report(alices_draft, NEW_ID, ALICE, "Dup")

    # Then
    assert error is None
    row = alices_draft.get_one_pager_status_row(NEW_ID)
    assert row.one_pager_status == "Cancelled"
    assert st.session_state["preview_flash"] == "OP-0003 was cancelled."


@pytest.mark.unit
def test__cancelled_one_pager__cancel_and_report__returns_transition_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Cancelling twice reports why."""
    # Given
    update_status_row(
        alices_draft,
        NEW_ID,
        one_pager_status="Cancelled",
        data_product_status="Cancelled",
    )

    # When
    error = actions.cancel_and_report(alices_draft, NEW_ID, ALICE, "")

    # Then
    assert "cannot change from Cancelled" in error


@pytest.mark.unit
def test__stranger__cancel_and_report__returns_permission_message(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A permission error is shown as a message."""
    # When
    error = actions.cancel_and_report(alices_draft, NEW_ID, make_user("XYZ"), "")

    # Then
    assert error == "Only the Owner, an SME or an Admin can cancel this One Pager."


@pytest.mark.unit
def test__ready_for_development__change_dp_status_and_report__flashes_change(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A successful DP change reports no error and flashes the new status."""
    # When
    error = actions.change_dp_status_and_report(
        alices_draft, APPROVED_ID, "In Development", ALICE, confirmed=True
    )

    # Then
    assert error is None
    assert st.session_state["preview_flash"] == (
        "Data Product status changed to In Development."
    )


@pytest.mark.unit
def test__in_development__change_dp_status_to_deprecated__returns_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A transition the state machine refuses is reported."""
    # Given
    update_status_row(alices_draft, APPROVED_ID, data_product_status="In Development")

    # When
    error = actions.change_dp_status_and_report(
        alices_draft, APPROVED_ID, "Deprecated", ALICE, confirmed=True
    )

    # Then
    assert "cannot change from In Development to Deprecated" in error


@pytest.mark.unit
def test__blank_reason__reject_and_report__asks_for_reason(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Rejecting without a reason changes nothing."""
    # When
    error = actions.reject_and_report(
        alices_draft, IN_REVIEW_ID, APPROVER, " ", APPROVER_ROLES
    )

    # Then
    assert error == "Explain why the One Pager is rejected."
    row = alices_draft.get_one_pager_status_row(IN_REVIEW_ID)
    assert row.one_pager_status == "In Review"


@pytest.mark.unit
def test__review_mode__reject_and_report__draft_and_review_mode_left(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A rejection flashes a message and leaves review mode."""
    # Given
    st.session_state["preview_review_mode"] = IN_REVIEW_ID

    # When
    error = actions.reject_and_report(
        alices_draft, IN_REVIEW_ID, APPROVER, "Data sources missing", APPROVER_ROLES
    )

    # Then
    assert error is None
    row = alices_draft.get_one_pager_status_row(IN_REVIEW_ID)
    assert row.one_pager_status == "Draft"
    assert "rejected" in st.session_state["preview_flash"]
    assert "preview_review_mode" not in st.session_state


@pytest.mark.unit
def test__approver_who_is_owner__reject_and_report__returns_self_review_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Self-review is reported as a message."""
    # When
    error = actions.reject_and_report(
        alices_draft, IN_REVIEW_ID, BOB, "No", APPROVER_ROLES
    )

    # Then
    assert error == "You cannot review a One Pager on which you are Owner or SME."


@pytest.mark.unit
def test__review_mode__approve_and_report__flashes_new_version(
    actions: ModuleType,
    alices_draft: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """An approval reports the new version and DP status and leaves review mode."""
    # Given
    store = document_store
    st.session_state["preview_review_mode"] = IN_REVIEW_ID

    # When
    error = actions.approve_and_report(
        alices_draft, store, IN_REVIEW_ID, APPROVER, APPROVER_ROLES
    )

    # Then
    assert error is None
    assert st.session_state["preview_flash"] == (
        "OP-0002 was approved as v1.0.0. The Data Product is now Ready for Development."
    )
    assert "preview_review_mode" not in st.session_state


@pytest.mark.unit
def test__approved_one_pager__approve_and_report__returns_status_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Approving a One Pager that is no longer In Review is reported."""
    # When
    error = actions.approve_and_report(
        alices_draft,
        alices_draft._document_store,
        APPROVED_ID,
        APPROVER,
        APPROVER_ROLES,
    )

    # Then
    assert error == "The One Pager is Approved, not In Review."


@pytest.mark.unit
def test__blank_comment__add_comment_and_report__asks_for_comment(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """An empty review comment is refused."""
    # When
    error = actions.add_comment_and_report(
        alices_draft, IN_REVIEW_ID, APPROVER, "useCases", " ", APPROVER_ROLES
    )

    # Then
    assert error == "Write a comment."


@pytest.mark.unit
def test__comment__add_comment_and_report__added_with_flash(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """A review comment is stored and a message flashed."""
    # When
    error = actions.add_comment_and_report(
        alices_draft, IN_REVIEW_ID, APPROVER, "useCases", "Link UC-001", APPROVER_ROLES
    )

    # Then
    assert error is None
    assert st.session_state["preview_flash"] == "Your review comment was added."
    assert len(alices_draft.get_review_comments(IN_REVIEW_ID)) == 1


@pytest.mark.unit
def test__one_pager_in_review__resolve_comment_and_report__returns_rework_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Comments can only be resolved once the One Pager is back in Draft."""
    # Given
    actions.add_comment_and_report(
        alices_draft, IN_REVIEW_ID, APPROVER, "useCases", "Link UC-001", APPROVER_ROLES
    )
    [comment] = alices_draft.get_review_comments(IN_REVIEW_ID)

    # When
    error = actions.resolve_comment_and_report(
        alices_draft, IN_REVIEW_ID, comment.id, BOB
    )

    # Then
    assert error == (
        "Comments are resolved while the One Pager is being reworked (Draft)."
    )


@pytest.mark.unit
def test__approved_one_pager__update_and_report__draft_update_with_flash(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Starting an update reports success."""
    # When
    error = actions.update_and_report(alices_draft, APPROVED_ID, ALICE)

    # Then
    assert error is None
    assert "Draft Update" in st.session_state["preview_flash"]
    row = alices_draft.get_one_pager_status_row(APPROVED_ID)
    assert row.one_pager_status == "Draft Update"


@pytest.mark.unit
def test__one_pager_in_draft_update__update_and_report__returns_status_error(
    actions: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Updating twice is reported."""
    # Given
    actions.update_and_report(alices_draft, APPROVED_ID, ALICE)

    # When
    error = actions.update_and_report(alices_draft, APPROVED_ID, ALICE)

    # Then
    assert error == "One Pager status cannot change from Draft Update to Draft Update."
