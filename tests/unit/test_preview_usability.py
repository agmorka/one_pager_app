"""Preview usability: status path, What changed, section comments, links."""

from collections.abc import Callable
from datetime import timedelta
from types import ModuleType

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.compare import (
    comparison_base,
    diff_documents,
    suggested_summary,
)
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import save_draft, working_copy
from onepagerapp.locking import acquire_lock
from onepagerapp.models import ChangeLogEntry
from onepagerapp.permissions import get_status_path
from onepagerapp.workflow import reject_one_pager
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    DOMAINS,
    IN_REVIEW_ID,
    NOW,
    TYPES,
    live_lock,
    page_app,
    update_status_row,
)


def _entry(
    version: str, from_status: str, to_status: str, minutes: int
) -> ChangeLogEntry:
    return ChangeLogEntry(
        id=minutes,
        one_pager_id=IN_REVIEW_ID,
        version=version,
        event_type="status_transition",
        author_initials="CJO",
        author_name="CJO",
        summary="",
        created_at=NOW + timedelta(minutes=minutes),
        from_status=from_status,
        to_status=to_status,
        status_field="one_pager_status",
    )


def _review(data_access: MockDataAccess) -> AppTest:
    return page_app(
        "preview.py",
        data_access,
        APPROVER,
        APPROVER_ROLES,
        preview_one_pager_id=IN_REVIEW_ID,
        preview_review_mode=IN_REVIEW_ID,
    )


def _rejected_and_reworked(data_access: MockDataAccess) -> None:
    """Reject OP-0002 (v0.3.0), save a new description as v0.4.0, resubmit."""
    reject_one_pager(
        data_access, IN_REVIEW_ID, APPROVER, "Needs work", roles=APPROVER_ROLES
    )
    acquire_lock(data_access, IN_REVIEW_ID, BOB, "s1")
    doc = working_copy(data_access.read_document(IN_REVIEW_ID))
    doc.description = "A sharper description"
    result = save_draft(
        data_access,
        data_access._document_store,
        IN_REVIEW_ID,
        doc,
        "Reworked",
        BOB,
        "s1",
        allowed_domains=[*DOMAINS, doc.business_domain],
        allowed_types=[*TYPES, doc.data_product_type],
    )
    assert result.ok, result.errors
    update_status_row(data_access, IN_REVIEW_ID, one_pager_status="In Review")


# ============================================================================
# Pure helpers
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "version", "expected"),
    [
        (
            "Draft",
            "0.2.0",
            [("Draft", "current"), ("In Review", "next"), ("Approved", "next")],
        ),
        (
            "Ready for Review",
            "0.2.0",
            [("Draft", "done"), ("In Review", "current"), ("Approved", "next")],
        ),
        (
            "Approved",
            "1.0.0",
            [("Draft", "done"), ("In Review", "done"), ("Approved", "current")],
        ),
        (
            "Draft Update",
            "1.1.0",
            [
                ("Approved v1.0.0", "done"),
                ("Draft Update", "current"),
                ("In Review", "next"),
                ("Approved", "next"),
            ],
        ),
        ("Cancelled", "0.3.0", [("Draft", "done"), ("Cancelled", "cancelled")]),
    ],
)
def test__status__get_status_path__steps_and_position(
    status: str, version: str, expected: list[tuple[str, str]]
) -> None:
    """The path depends on the first version vs an update, and on cancelling."""
    steps = get_status_path(status, version)
    assert [(s.label, s.state) for s in steps] == expected


@pytest.mark.unit
def test__change_log__comparison_base__last_approval_then_rejection() -> None:
    """The last approval is the base; without one, the last rejected version."""
    rejected = _entry("0.3.0", "In Review", "Draft", 1)
    approved = _entry("1.0.0", "In Review", "Approved", 2)
    assert comparison_base([rejected], "0.4.0").version == "0.3.0"
    assert comparison_base([rejected, approved], "1.1.0").version == "1.0.0"
    assert comparison_base([approved], "1.0.0") is None  # the current version
    assert comparison_base([], "0.1.0") is None


@pytest.mark.unit
def test__changed_description__diff_and_summary__only_that_section(
    mock_data_access: MockDataAccess,
) -> None:
    """Only the changed section is reported; metadata never is."""
    old = mock_data_access.read_document(APPROVED_ID)
    new = working_copy(old)
    new.description = "Something else"
    new.version = "9.9.9"

    changes = diff_documents(old, new)

    assert [(c.label, c.kind) for c in changes] == [("Description", "changed")]
    assert suggested_summary(old, new) == "Updated Description"


# ============================================================================
# Page
# ============================================================================


@pytest.mark.unit
def test__resubmitted_after_rejection__review_mode__what_changed_first(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Reviewers see what changed since the version they rejected, first."""
    # Given
    _rejected_and_reworked(mock_data_access)

    # When
    at = _review(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.tabs[0].label == "What changed"
    assert any("v0.3.0" in c.value for c in at.caption)
    assert any(e.label == "Description — Changed" for e in at.expander)


@pytest.mark.unit
def test__first_version__review_mode__no_what_changed_tab(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A first version has nothing to compare with."""
    at = _review(mock_data_access).run()
    assert at.tabs[0].label == "Content"
    assert "What changed" not in [t.label for t in at.tabs]


@pytest.mark.unit
def test__review_mode__comment_on_section__dialog_preselects_it(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Comment on this section opens the comment dialog on that section."""
    # Given
    at = _review(mock_data_access).run()

    # When
    at.button(key="preview_comment_on_dataSources").click().run()

    # Then
    assert not at.exception
    assert at.selectbox(key="preview_comment_section_dataSources").value == (
        "dataSources"
    )


@pytest.mark.unit
def test__review_mode__back_to_review_queue__mode_left_with_confirmation(
    import_app_module: Callable[[str], ModuleType], switched: list[str]
) -> None:
    """After a decision the Approver returns to the queue with the outcome."""
    # Given
    navigation = import_app_module("adapters.navigation")
    st.session_state["preview_review_mode"] = IN_REVIEW_ID

    # When
    navigation.back_to_review_queue("OP-0002 was approved as v1.0.0.")

    # Then
    assert switched == ["views/review.py"]
    assert "preview_review_mode" not in st.session_state
    assert st.session_state["review_flash"] == "OP-0002 was approved as v1.0.0."


@pytest.mark.unit
def test__linked_use_case__click_its_button__use_cases_page_on_it(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Linked Use Cases open on the Use Cases page."""
    # Given
    at = _review(mock_data_access).run()
    button = next(b for b in at.button if str(b.key).startswith("preview_open_uc_"))
    use_case_id = button.label

    # When
    button.click().run()

    # Then
    assert switched == ["views/use_cases.py"]
    assert at.session_state["uc_selected_id"] == use_case_id


@pytest.mark.unit
def test__locked_by_other__owner_opens_preview__reason_shown_and_lock_near_actions(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Why Edit is unavailable is written out, not only in a tooltip."""
    # Given (OP-0002 back in Draft, locked by Diana; Bob is its Owner)
    update_status_row(mock_data_access, IN_REVIEW_ID, one_pager_status="Draft")
    mock_data_access._locks[IN_REVIEW_ID] = live_lock(
        IN_REVIEW_ID, ALICE, timedelta(minutes=20)
    )

    # When
    at = page_app(
        "preview.py",
        mock_data_access,
        BOB,
        frozenset(),
        preview_one_pager_id=IN_REVIEW_ID,
    ).run()

    # Then
    assert not at.exception
    assert at.button(key="preview_edit").disabled
    assert any(c.value.startswith("**Edit**:") for c in at.caption)
    assert any("Locked by Alice Brown" in w.value for w in at.warning)


@pytest.mark.unit
def test__preview__click_back__registry(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Preview has a way back to the Registry."""
    at = page_app(
        "preview.py",
        mock_data_access,
        ALICE,
        frozenset(),
        preview_one_pager_id=APPROVED_ID,
    ).run()
    at.button(key="preview_back").click().run()
    assert switched == ["views/registry.py"]
