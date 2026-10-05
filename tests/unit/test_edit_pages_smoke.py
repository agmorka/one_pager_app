"""AppTest smoke tests for the Editor in edit mode and Preview actions.

Each page script runs on its own with the services injected into session
state, and st.switch_page is recorded (see ``tests.helpers.page_app``).
"""

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.locking import acquire_lock
from onepagerapp.review import add_review_comment
from onepagerapp.workflow import (
    cancel_one_pager,
    change_data_product_status,
    reject_one_pager,
    start_update,
)
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    IN_REVIEW_ID,
    NEW_ID,
    button_labelled,
    editor_page,
    fill_all_sections,
    make_user,
    page_app,
    switch_tab,
    update_status_row,
)

TAB_NAMES = [
    "Basics",
    "Business Problem",
    "Use Cases",
    "Business Requirements",
    "Data Sources",
    "Data Product Preview",
    "Classification",
    "Governance",
    "Scope & Questions",
    "Review",
]


def _preview(data_access: MockDataAccess, one_pager_id: str = NEW_ID) -> AppTest:
    """Return the Preview of ``one_pager_id`` for Alice."""
    return page_app("preview.py", data_access, preview_one_pager_id=one_pager_id)


def _action_keys(at: AppTest) -> set[str]:
    """Return the keys of the Preview action buttons."""
    return {b.key for b in at.button if b.key and b.key.startswith("preview_")}


def _needs_attention(at: AppTest) -> str:
    """Return the Editor caption listing the tabs that need attention."""
    return next(c.value for c in at.caption if "Needs attention" in c.value)


def _complete_and_save(at: AppTest) -> None:
    """Fill every section in the Editor and save the Draft."""
    fill_all_sections(at.session_state["edit_document"])
    at.text_input(key="edit_change_summary").input("Complete").run()
    button_labelled(at, "Save Draft").click().run()


def _rejected_with_comment(data_access: MockDataAccess) -> int:
    """Send OP-0003 to review and reject it with one comment; return its ID."""
    update_status_row(data_access, NEW_ID, one_pager_status="In Review")
    add_review_comment(
        data_access,
        NEW_ID,
        APPROVER,
        "dataSources",
        "Add sources",
        roles=APPROVER_ROLES,
    )
    reject_one_pager(
        data_access, NEW_ID, APPROVER, "See comments", roles=APPROVER_ROLES
    )
    return data_access.get_review_comments(NEW_ID)[0].id


# ============================================================================
# Opening the Editor
# ============================================================================


@pytest.mark.unit
def test__owners_draft__click_edit_on_preview__editor_in_edit_mode(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Edit on the Preview opens the Editor on that One Pager."""
    # Given
    at = _preview(alices_draft).run()
    assert not at.exception
    assert not at.button(key="preview_edit").disabled

    # When
    at.button(key="preview_edit").click().run()

    # Then
    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "edit"
    assert at.session_state["editor_one_pager_id"] == NEW_ID


@pytest.mark.unit
def test__one_pager_of_others_in_review__open_preview__no_edit(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Alice is not Owner/SME of OP-0002 (and it is In Review)."""
    # When
    at = _preview(alices_draft, IN_REVIEW_ID).run()

    # Then
    assert "preview_edit" not in {b.key for b in at.button}


@pytest.mark.unit
def test__owners_draft__open_editor__basics_prefilled_and_locked(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The Editor shows the stored values and takes the lock."""
    # When
    at = editor_page(alices_draft).run()

    # Then
    assert not at.exception
    assert at.title[0].value == "Editing: Customer Master (OP-0003)"
    assert at.text_input(key="edit_product_name").value == "Customer Master"
    assert at.text_input(key="edit_owner_initials").value == "ABR"
    assert at.text_area(key="edit_description").value == "Unified customer view"
    assert alices_draft.get_lock(NEW_ID).locked_by_initials == "ABR"


@pytest.mark.unit
def test__draft_locked_by_sme__open_editor__warning_and_no_document(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Another user's lock blocks editing."""
    # Given
    acquire_lock(alices_draft, NEW_ID, make_user("DPR"), "other-session")

    # When
    at = editor_page(alices_draft).run()

    # Then
    assert not at.exception
    assert "Locked by DPR" in at.warning[0].value
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__one_pager_of_others__open_editor__permission_error(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A user who is neither Owner nor SME cannot edit."""
    # When
    at = editor_page(alices_draft, IN_REVIEW_ID).run()

    # Then
    assert not at.exception
    assert "Owner or an SME" in at.error[0].value


@pytest.mark.unit
def test__update_started__open_editor__approved_version_opened(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """After Update the Editor opens the Approved version with a lock."""
    # Given
    start_update(alices_draft, APPROVED_ID, ALICE, confirmed=True)

    # When
    at = editor_page(alices_draft, APPROVED_ID).run()

    # Then
    assert not at.exception
    assert at.title[0].value == "Editing: Person Master Data (OP-0001)"
    assert alices_draft.get_lock(APPROVED_ID).locked_by_initials == "ABR"


# ============================================================================
# Editing
# ============================================================================


@pytest.mark.unit
def test__editor_open__type_in_field__no_lock_write(
    alices_draft: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-run soon after the lock was written does not write the heartbeat."""
    # Given
    at = editor_page(alices_draft).run()
    writes: list[object] = []
    write_lock = alices_draft.write_lock
    monkeypatch.setattr(
        alices_draft,
        "write_lock",
        lambda lock, **kw: writes.append(lock) or write_lock(lock, **kw),
    )

    # When
    at.text_input(key="edit_product_name").input("Customer Master v2").run()

    # Then
    assert not at.exception
    assert writes == []
    assert alices_draft.get_lock(NEW_ID).locked_by_initials == "ABR"


@pytest.mark.unit
def test__changed_product_name__switch_tabs_and_back__input_kept(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Switching tabs keeps what was typed on each tab."""
    # Given
    at = editor_page(alices_draft).run()
    at.text_input(key="edit_product_name").input("Customer Master v2").run()
    switch_tab(at, "Business Problem")
    at.text_area(key="edit_problem").input("Scattered data").run()

    # When
    switch_tab(at, "Basics")

    # Then
    assert not at.exception
    assert at.text_input(key="edit_product_name").value == "Customer Master v2"
    document = at.session_state["edit_document"]
    assert document.business_problem_statement == "Scattered data"


@pytest.mark.unit
def test__open_editor__visit_every_tab__each_renders(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """All tabs exist in order and render without errors."""
    # Given
    at = editor_page(alices_draft).run()
    assert list(at.radio(key="edit_active_tab").options) == TAB_NAMES

    # When / Then
    for name in TAB_NAMES:
        switch_tab(at, name)
        assert not at.exception, name


@pytest.mark.unit
def test__requirement_form_filled__click_ok__requirement_gets_br_id(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A new business requirement gets the next BR ID."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Business Requirements")
    at.button(key="edit_br_add").click().run()
    at.text_area(key="edit_br_f_requirement").input("Daily refresh")
    at.selectbox(key="edit_br_f_priority").select("High")

    # When
    at.button(key="edit_br_form_ok").click().run()

    # Then
    assert not at.exception
    assert at.session_state["edit_document"].business_requirements == [
        {"id": "BR-001", "requirement": "Daily refresh", "priority": "High"}
    ]


@pytest.mark.unit
def test__use_case_linked__save_draft__reference_stored(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A linked Use Case is stored as a reference on save."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Use Cases")
    at.selectbox(key="edit_uc_pick").select("UC-001")
    at.button(key="edit_uc_link").click().run()
    assert at.session_state["edit_document"].use_case_ids == ["UC-001"]
    at.text_input(key="edit_change_summary").input("Linked UC-001").run()

    # When
    button_labelled(at, "Save Draft").click().run()

    # Then
    assert not at.exception
    assert alices_draft.get_linked_use_case_ids(NEW_ID) == ["UC-001"]


@pytest.mark.unit
def test__linked_use_case__click_unlink__removed_from_document(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Unlinking removes the Use Case from the working copy."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Use Cases")
    at.selectbox(key="edit_uc_pick").select("UC-001")
    at.button(key="edit_uc_link").click().run()

    # When
    at.button(key="edit_uc_unlink_UC-001").click().run()

    # Then
    assert at.session_state["edit_document"].use_case_ids == []


@pytest.mark.unit
def test__new_use_case_form_filled__click_create__created_and_linked(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A Use Case can be created inline and is linked at once."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Use Cases")
    for name in ("persona", "goal", "scenario", "decision_enabled"):
        at.text_area(key=f"edit_uc_new_{name}").input(f"New {name}")
    at.selectbox(key="edit_uc_new_priority").select("High")

    # When
    at.button(key="edit_uc_create").click().run()

    # Then
    assert not at.exception
    [use_case_id] = at.session_state["edit_document"].use_case_ids
    assert alices_draft.get_use_case(use_case_id).persona == "New persona"


@pytest.mark.unit
def test__non_cde_element_with_tier__click_ok__cde_fields_dropped(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """CDE-only fields are not kept on a non-CDE element."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Data Product Preview")
    at.button(key="edit_dpp_add").click().run()
    at.text_input(key="edit_dpp_f_elementName").input("segment")
    at.text_input(key="edit_dpp_f_dataType").input("STRING")
    at.text_area(key="edit_dpp_f_description").input("Segment")
    at.text_input(key="edit_dpp_f_cdeCriticalityTiering").input("Tier 1")

    # When
    at.button(key="edit_dpp_form_ok").click().run()

    # Then
    [element] = at.session_state["edit_document"].data_product_preview
    assert element["isCriticalDataElement"] is False
    assert "cdeCriticalityTiering" not in element


@pytest.mark.unit
def test__classification_tab__choose_level_and_pii__document_updated(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Classification inputs update the document; retention is then required."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Classification")
    at.selectbox(key="edit_class_level").select("Internal")

    # When
    at.checkbox(key="edit_class_pii").check().run()

    # Then
    assert not at.exception
    assert at.session_state["edit_document"].data_classification == {
        "classificationLevel": "Internal",
        "containsPII": True,
        "containsSensitiveData": False,
    }
    assert "Required" in at.info[0].value


@pytest.mark.unit
def test__incomplete_draft__open_editor__badges_list_tabs_with_issues(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Tabs with issues are named with their count; Basics is fine."""
    # When
    at = editor_page(alices_draft).run()

    # Then
    badges = _needs_attention(at)
    assert "Use Cases (1)" in badges
    assert "Basics" not in badges


@pytest.mark.unit
def test__issue_in_summary__click_it__its_tab_opened(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Each issue in the summary links to its tab."""
    # Given
    at = editor_page(alices_draft).run()
    issue = next(b for b in at.button if b.key.startswith("edit_issue_submit_Data S"))
    assert issue.label == "This field is required."

    # When
    issue.click().run()

    # Then
    assert at.radio(key="edit_active_tab").value == "Data Sources"
    assert at.button(key="edit_ds_add")


@pytest.mark.unit
def test__problem_statement_missing__type_it__badge_cleared(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Badges follow the input without saving."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Business Problem")

    # When
    at.text_area(key="edit_problem").input("Scattered data").run()

    # Then
    assert "Business Problem" not in _needs_attention(at)


# ============================================================================
# Saving, closing and submitting
# ============================================================================


@pytest.mark.unit
def test__renamed_with_summary__click_save_draft__new_version_lock_kept(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Save Draft bumps the version, clears the summary and keeps the lock."""
    # Given
    at = editor_page(alices_draft).run()
    at.text_input(key="edit_product_name").input("Customer Master v2")
    at.text_input(key="edit_change_summary").input("Renamed the product")
    at.run()

    # When
    button_labelled(at, "Save Draft").click().run()

    # Then
    assert not at.exception
    assert "Saved as v0.2.0" in at.success[0].value
    assert at.text_input(key="edit_change_summary").value == ""
    row = alices_draft.get_one_pager_status_row(NEW_ID)
    assert (row.version, row.product_name) == ("0.2.0", "Customer Master v2")
    assert alices_draft.get_lock(NEW_ID) is not None


@pytest.mark.unit
def test__no_summary__click_save_draft__summary_error_no_version(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A save without a change summary is refused."""
    # Given
    at = editor_page(alices_draft).run()

    # When
    button_labelled(at, "Save Draft").click().run()

    # Then
    assert not at.exception
    assert any("Describe what you changed." in m.value for m in at.markdown)
    assert alices_draft.get_one_pager_status_row(NEW_ID).version == "0.1.0"


@pytest.mark.unit
def test__clean_editor__click_close__lock_released_preview_opened(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Closing releases the lock and returns to the Preview."""
    # Given
    at = editor_page(alices_draft).run()

    # When
    button_labelled(at, "Close editor").click().run()

    # Then
    assert switched == ["views/preview.py"]
    assert alices_draft.get_lock(NEW_ID) is None
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__unsaved_complete_document__open_editor__submit_disabled_until_saved(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Submit waits until the changes are saved."""
    # Given
    at = editor_page(alices_draft).run()
    fill_all_sections(at.session_state["edit_document"])
    at.text_input(key="edit_change_summary").input("Complete").run()
    assert at.button(key="edit_submit").disabled

    # When
    button_labelled(at, "Save Draft").click().run()

    # Then
    assert not at.button(key="edit_submit").disabled


@pytest.mark.unit
def test__saved_complete_draft__click_submit__in_review_lock_released(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Submit moves the One Pager to In Review and returns to the Preview."""
    # Given
    at = editor_page(alices_draft).run()
    _complete_and_save(at)

    # When
    at.button(key="edit_submit").click().run()

    # Then
    assert not at.exception
    assert switched == ["views/preview.py"]
    assert "now In Review" in at.session_state["preview_flash"]
    row = alices_draft.get_one_pager_status_row(NEW_ID)
    assert row.one_pager_status == "In Review"
    assert alices_draft.get_lock(NEW_ID) is None


@pytest.mark.unit
def test__incomplete_draft__click_submit__blocked_with_error(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Strict validation errors block the submit."""
    # Given
    at = editor_page(alices_draft).run()

    # When
    at.button(key="edit_submit").click().run()

    # Then
    assert not at.exception
    assert switched == []
    assert "Submit for Review is blocked" in at.error[0].value


# ============================================================================
# Review tab
# ============================================================================


@pytest.mark.unit
def test__incomplete_draft__open_review_tab__checklist_and_submit_disabled(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The Review tab lists the sections and blocks the submit."""
    # When
    at = switch_tab(editor_page(alices_draft).run(), "Review")

    # Then
    assert not at.exception
    assert any(m.value == "Basics: no issues" for m in at.markdown)
    assert "issue(s)" in at.button(key="edit_review_check_Data Sources").label
    assert at.button(key="edit_review_submit").disabled
    assert "No review comments." in [c.value for c in at.caption]


@pytest.mark.unit
def test__review_tab__click_section_check__its_tab_opened(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A checklist entry links to its tab."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Review")

    # When
    at.button(key="edit_review_check_Data Sources").click().run()

    # Then
    assert at.radio(key="edit_active_tab").value == "Data Sources"


@pytest.mark.unit
def test__rejected_with_comment__click_resolve_on_review_tab__resolved(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The Owner resolves review comments on the Review tab."""
    # Given
    comment_id = _rejected_with_comment(alices_draft)
    at = switch_tab(editor_page(alices_draft).run(), "Review")
    assert any("Data Sources" in m.value for m in at.markdown)

    # When
    at.button(key=f"edit_resolve_{comment_id}").click().run()

    # Then
    assert not at.exception
    comment = alices_draft.get_review_comments(NEW_ID)[0]
    assert (comment.resolved, comment.resolved_by) == (True, "ABR")
    assert "The comment was marked as resolved." in [s.value for s in at.success]
    assert f"edit_resolve_{comment_id}" not in {b.key for b in at.button}


@pytest.mark.unit
def test__saved_complete_draft__submit_on_review_tab__in_review(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The Review tab submits once the complete document is saved."""
    # Given
    at = editor_page(alices_draft).run()
    fill_all_sections(at.session_state["edit_document"])
    at.text_input(key="edit_change_summary").input("Complete").run()
    switch_tab(at, "Review")
    assert at.button(key="edit_review_submit").disabled
    button_labelled(at, "Save Draft").click().run()
    assert not at.button(key="edit_review_submit").disabled

    # When
    at.button(key="edit_review_submit").click().run()

    # Then
    assert not at.exception
    assert switched == ["views/preview.py"]
    row = alices_draft.get_one_pager_status_row(NEW_ID)
    assert row.one_pager_status == "In Review"


# ============================================================================
# Preview actions
# ============================================================================


@pytest.mark.unit
def test__owners_draft__open_preview__edit_cancel_export_no_approve(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The Owner of a Draft can edit, cancel and export."""
    # When
    at = _preview(alices_draft).run()

    # Then
    keys = _action_keys(at)
    assert {"preview_edit", "preview_cancel", "preview_export_pdf"} <= keys
    assert "preview_approve" not in keys


@pytest.mark.unit
def test__one_pager_of_others_in_review__open_preview__read_only_with_hint(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Somebody else's One Pager In Review can only be exported."""
    # When
    at = _preview(alices_draft, IN_REVIEW_ID).run()

    # Then
    assert not at.exception
    assert _action_keys(at) == {"preview_export_pdf"}
    assert any("Waiting for an Approver" in c.value for c in at.caption)


@pytest.mark.unit
def test__owners_draft__click_cancel__asks_for_reason_first(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Cancel opens a dialog; nothing changes before confirming."""
    # Given
    at = _preview(alices_draft).run()

    # When
    at.button(key="preview_cancel").click().run()

    # Then
    assert not at.exception
    assert at.text_area(key="preview_cancel_reason")
    assert alices_draft.get_one_pager_status_row(NEW_ID).one_pager_status == "Draft"


@pytest.mark.unit
def test__cancelled_one_pager__open_preview__read_only(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """A cancelled One Pager can only be exported."""
    # Given
    cancel_one_pager(alices_draft, NEW_ID, ALICE)

    # When
    at = _preview(alices_draft).run()

    # Then
    assert not at.exception
    assert _action_keys(at) == {"preview_export_pdf"}
    assert any("cancelled (read-only)" in c.value for c in at.caption)


@pytest.mark.unit
def test__owners_approved_one_pager__click_change_dp_status__menu_offers_next(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """OP-0001 (Ready for Development) offers to start development."""
    # Given
    at = _preview(alices_draft, APPROVED_ID).run()
    assert {"preview_update", "preview_change_dp_status"} <= _action_keys(at)
    assert not at.button(key="preview_change_dp_status").disabled
    assert not at.button(key="preview_update").disabled

    # When
    at.button(key="preview_change_dp_status").click().run()

    # Then
    assert not at.exception
    assert list(at.selectbox(key="preview_dp_target").options) == [
        "Start development → In Development"
    ]


@pytest.mark.unit
def test__seeded_one_pager_after_transition__open_preview__change_log_renders(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Seeded change-log rows are naive, new ones UTC-aware; both must sort."""
    # Given
    change_data_product_status(alices_draft, APPROVED_ID, "In Development", ALICE)

    # When
    at = _preview(alices_draft, APPROVED_ID).run()

    # Then
    assert not at.exception
    assert any("Development started" in m.value for m in at.markdown)


@pytest.mark.unit
def test__owners_approved_one_pager__click_update__asks_first(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Update explains the working copy; nothing happens before Confirm."""
    # Given
    at = _preview(alices_draft, APPROVED_ID).run()

    # When
    at.button(key="preview_update").click().run()

    # Then
    assert not at.exception
    assert any("working copy for editing" in m.value for m in at.markdown)
    row = alices_draft.get_one_pager_status_row(APPROVED_ID)
    assert row.one_pager_status == "Approved"
