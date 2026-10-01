"""AppTest smoke tests for the Editor in edit mode (local-mock mode).

As in test_create_pages_smoke.py, each page script runs on its own with the
services injected into session state, and st.switch_page is recorded.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import NewOnePagerInput, PersonRef
from onepagerapp.workflow import create_one_pager
from tests.conftest import FIXTURES_DIR
from tests.users import CREATOR_ROLES, make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


@pytest.fixture
def services(tmp_path: Path) -> dict:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    data_access = MockDataAccess(store)
    user = make_user("ABR", "Alice Brown")
    result = create_one_pager(
        NewOnePagerInput(
            data_product="customer_master",
            product_name="Customer Master",
            business_domain="Customer",
            data_product_type="Foundational",
            description="Unified customer view",
            owner=PersonRef("Alice Brown", "ABR", "alice.brown@company.com"),
            smes=[PersonRef("Diana Prince", "DPR", "diana@bec.dk")],
        ),
        user,
        data_access,
        store,
        now=NOW,
        roles=CREATOR_ROLES,
    )
    assert result.one_pager_id == "OP-0003"
    return {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
        "current_user_roles": CREATOR_ROLES,
    }


def _app(page: str, state: dict) -> AppTest:
    at = AppTest.from_file(str(APP_DIR / "views" / page), default_timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at


def _editor(services: dict, one_pager_id: str = "OP-0003") -> AppTest:
    return _app(
        "editor.py",
        {**services, "editor_mode": "edit", "editor_one_pager_id": one_pager_id},
    )


def _button(at: AppTest, label: str):  # noqa: ANN202
    return next(b for b in at.button if b.label == label)


@pytest.mark.unit
def test__preview__edit_opens_editor_in_edit_mode(
    services: dict, switched: list[str]
) -> None:
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0003"}).run()
    assert not at.exception
    edit = at.button(key="preview_edit")
    assert not edit.disabled

    edit.click().run()

    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "edit"
    assert at.session_state["editor_one_pager_id"] == "OP-0003"


@pytest.mark.unit
def test__preview__edit_hidden_for_non_owner(
    services: dict, switched: list[str]
) -> None:
    # Alice is not Owner/SME of OP-0002 (and it is In Review).
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0002"}).run()
    assert "preview_edit" not in {b.key for b in at.button}


@pytest.mark.unit
def test__editor__edit_mode_prefills_basics_and_locks(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()

    assert not at.exception
    assert at.title[0].value == "Editing: Customer Master (OP-0003)"
    assert at.text_input(key="edit_product_name").value == "Customer Master"
    assert at.text_input(key="edit_owner_initials").value == "ABR"
    assert at.text_area(key="edit_description").value == "Unified customer view"
    lock = services["data_access"].get_lock("OP-0003")
    assert lock.locked_by_initials == "ABR"


@pytest.mark.unit
def test__editor__switching_tabs_keeps_input(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()
    at.text_input(key="edit_product_name").input("Customer Master v2").run()

    at.radio(key="edit_active_tab").set_value("Business Problem").run()
    at.text_area(key="edit_problem").input("Scattered data").run()
    at.radio(key="edit_active_tab").set_value("Basics").run()

    assert not at.exception
    assert at.text_input(key="edit_product_name").value == "Customer Master v2"
    assert at.session_state["edit_document"].business_problem_statement == (
        "Scattered data"
    )


@pytest.mark.unit
def test__editor__lock_held_by_other_user_blocks_editing(
    services: dict, switched: list[str]
) -> None:
    from onepagerapp.locking import acquire_lock  # noqa: PLC0415

    sme = make_user("DPR")
    acquire_lock(services["data_access"], "OP-0003", sme, "other-session")

    at = _editor(services).run()

    assert not at.exception
    assert "Locked by DPR" in at.warning[0].value
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__editor__not_authorized_user_sees_error(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services, "OP-0002").run()
    assert not at.exception
    assert "Owner or an SME" in at.error[0].value


@pytest.mark.unit
def test__editor__close_releases_lock_and_opens_preview(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()
    _button(at, "Close editor").click().run()

    assert switched == ["views/preview.py"]
    assert services["data_access"].get_lock("OP-0003") is None
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__editor__save_draft_bumps_version(services: dict, switched: list[str]) -> None:
    at = _editor(services).run()
    at.text_input(key="edit_product_name").input("Customer Master v2")
    at.text_input(key="edit_change_summary").input("Renamed the product")
    at.run()

    _button(at, "Save Draft").click().run()

    assert not at.exception
    assert "Saved as v0.2.0" in at.success[0].value
    assert at.text_input(key="edit_change_summary").value == ""
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert (row.version, row.product_name) == ("0.2.0", "Customer Master v2")
    # Saving does not release the lock (Backend_Design.md §6).
    assert services["data_access"].get_lock("OP-0003") is not None


@pytest.mark.unit
def test__editor__save_without_summary_shows_error(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()
    _button(at, "Save Draft").click().run()

    assert not at.exception
    assert any("Describe what you changed." in m.value for m in at.markdown)
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert row.version == "0.1.0"


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


def _tab(at: AppTest, name: str) -> AppTest:
    return at.radio(key="edit_active_tab").set_value(name).run()


@pytest.mark.unit
def test__editor__every_tab_renders(services: dict, switched: list[str]) -> None:
    at = _editor(services).run()
    assert list(at.radio(key="edit_active_tab").options) == TAB_NAMES
    for name in TAB_NAMES:
        _tab(at, name)
        assert not at.exception, name


@pytest.mark.unit
def test__editor__add_requirement_gets_br_id(
    services: dict, switched: list[str]
) -> None:
    at = _tab(_editor(services).run(), "Business Requirements")
    at.button(key="edit_br_add").click().run()
    at.text_area(key="edit_br_f_requirement").input("Daily refresh")
    at.selectbox(key="edit_br_f_priority").select("High")
    at.button(key="edit_br_form_ok").click().run()

    assert not at.exception
    assert at.session_state["edit_document"].business_requirements == [
        {"id": "BR-001", "requirement": "Daily refresh", "priority": "High"}
    ]


@pytest.mark.unit
def test__editor__link_use_case_and_save(services: dict, switched: list[str]) -> None:
    at = _tab(_editor(services).run(), "Use Cases")
    at.selectbox(key="edit_uc_pick").select("UC-001")
    at.button(key="edit_uc_link").click().run()
    assert at.session_state["edit_document"].use_case_ids == ["UC-001"]

    at.text_input(key="edit_change_summary").input("Linked UC-001").run()
    _button(at, "Save Draft").click().run()

    assert not at.exception
    data_access = services["data_access"]
    assert data_access.get_linked_use_case_ids("OP-0003") == ["UC-001"]

    at.button(key="edit_uc_unlink_UC-001").click().run()
    assert at.session_state["edit_document"].use_case_ids == []


@pytest.mark.unit
def test__editor__create_use_case_inline(services: dict, switched: list[str]) -> None:
    at = _tab(_editor(services).run(), "Use Cases")
    for name in ("persona", "goal", "scenario", "decision_enabled"):
        at.text_area(key=f"edit_uc_new_{name}").input(f"New {name}")
    at.selectbox(key="edit_uc_new_priority").select("High")
    at.button(key="edit_uc_create").click().run()

    assert not at.exception
    [use_case_id] = at.session_state["edit_document"].use_case_ids
    assert services["data_access"].get_use_case(use_case_id).persona == "New persona"


@pytest.mark.unit
def test__editor__non_cde_element_drops_cde_fields(
    services: dict, switched: list[str]
) -> None:
    at = _tab(_editor(services).run(), "Data Product Preview")
    at.button(key="edit_dpp_add").click().run()
    at.text_input(key="edit_dpp_f_elementName").input("segment")
    at.text_input(key="edit_dpp_f_dataType").input("STRING")
    at.text_area(key="edit_dpp_f_description").input("Segment")
    at.text_input(key="edit_dpp_f_cdeCriticalityTiering").input("Tier 1")
    at.button(key="edit_dpp_form_ok").click().run()

    [element] = at.session_state["edit_document"].data_product_preview
    assert element["isCriticalDataElement"] is False
    assert "cdeCriticalityTiering" not in element


@pytest.mark.unit
def test__editor__classification_updates_document(
    services: dict, switched: list[str]
) -> None:
    at = _tab(_editor(services).run(), "Classification")
    at.selectbox(key="edit_class_level").select("Internal")
    at.checkbox(key="edit_class_pii").check().run()

    assert not at.exception
    assert at.session_state["edit_document"].data_classification == {
        "classificationLevel": "Internal",
        "containsPII": True,
        "containsSensitiveData": False,
    }
    assert "Required" in at.info[0].value


@pytest.mark.unit
def test__editor__badges_and_summary_link_to_tabs(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()

    badges = next(c.value for c in at.caption if "Needs attention" in c.value)
    assert "Use Cases 🔴 1" in badges
    assert "Basics" not in badges
    issue = next(b for b in at.button if b.key.startswith("edit_issue_submit_Data S"))
    assert issue.label == "This field is required."

    issue.click().run()

    assert at.radio(key="edit_active_tab").value == "Data Sources"
    assert at.button(key="edit_ds_add")


@pytest.mark.unit
def test__editor__badges_follow_input(services: dict, switched: list[str]) -> None:
    at = _tab(_editor(services).run(), "Business Problem")
    at.text_area(key="edit_problem").input("Scattered data").run()
    badges = next(c.value for c in at.caption if "Needs attention" in c.value)
    assert "Business Problem" not in badges


def _action_keys(at: AppTest) -> set[str]:
    return {b.key for b in at.button if b.key and b.key.startswith("preview_")}


@pytest.mark.unit
def test__preview__actions_follow_role_and_status(
    services: dict, switched: list[str]
) -> None:
    owner_draft = _app(
        "preview.py", {**services, "preview_one_pager_id": "OP-0003"}
    ).run()
    assert {"preview_edit", "preview_cancel", "preview_export_pdf"} <= _action_keys(
        owner_draft
    )
    assert "preview_approve" not in _action_keys(owner_draft)

    # Alice is not Owner/SME of OP-0002 (In Review): read-only.
    viewer = _app("preview.py", {**services, "preview_one_pager_id": "OP-0002"}).run()
    assert not viewer.exception
    assert _action_keys(viewer) == {"preview_export_pdf"}
    assert any("Waiting for an Approver" in c.value for c in viewer.caption)


@pytest.mark.unit
def test__editor__submit_for_review(services: dict, switched: list[str]) -> None:
    from tests.unit.test_editing_links import fill_all_sections  # noqa: PLC0415

    at = _editor(services).run()
    fill_all_sections(at.session_state["edit_document"])
    at.text_input(key="edit_change_summary").input("Complete").run()
    assert at.button(key="edit_submit").disabled  # unsaved changes
    _button(at, "Save Draft").click().run()
    assert not at.button(key="edit_submit").disabled

    at.button(key="edit_submit").click().run()

    assert not at.exception
    assert switched == ["views/preview.py"]
    assert "now In Review" in at.session_state["preview_flash"]
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert row.one_pager_status == "In Review"
    assert services["data_access"].get_lock("OP-0003") is None


@pytest.mark.unit
def test__editor__submit_blocked_by_validation(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()
    at.button(key="edit_submit").click().run()

    assert not at.exception
    assert switched == []
    assert "Submit for Review is blocked" in at.error[0].value


@pytest.mark.unit
def test__preview__cancel_asks_for_confirmation(
    services: dict, switched: list[str]
) -> None:
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0003"}).run()
    at.button(key="preview_cancel").click().run()

    assert not at.exception
    assert at.text_area(key="preview_cancel_reason")  # the dialog is open
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert row.one_pager_status == "Draft"


@pytest.mark.unit
def test__preview__cancelled_one_pager_is_read_only(
    services: dict, switched: list[str]
) -> None:
    from onepagerapp.workflow import cancel_one_pager  # noqa: PLC0415

    cancel_one_pager(services["data_access"], "OP-0003", services["current_user_info"])
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0003"}).run()

    assert not at.exception
    assert _action_keys(at) == {"preview_export_pdf"}
    assert any("cancelled (read-only)" in c.value for c in at.caption)


@pytest.mark.unit
def test__preview__change_dp_status_opens_menu(
    services: dict, switched: list[str]
) -> None:
    # OP-0001: Approved / Ready for Development, owned by Alice.
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0001"}).run()
    assert {"preview_update", "preview_change_dp_status"} <= _action_keys(at)
    assert not at.button(key="preview_change_dp_status").disabled
    assert not at.button(key="preview_update").disabled

    at.button(key="preview_change_dp_status").click().run()

    assert not at.exception
    assert list(at.selectbox(key="preview_dp_target").options) == [
        "Start development → In Development"
    ]


@pytest.mark.unit
def test__preview__renders_after_transition_on_seeded_one_pager(
    services: dict, switched: list[str]
) -> None:
    """Seeded change-log rows are naive, new ones UTC-aware; both must sort."""
    from onepagerapp.workflow import change_data_product_status  # noqa: PLC0415

    change_data_product_status(
        services["data_access"],
        "OP-0001",
        "In Development",
        services["current_user_info"],
    )
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0001"}).run()

    assert not at.exception
    assert any("Development started" in m.value for m in at.markdown)


def _reject_with_comment(services: dict) -> int:
    """Send OP-0003 through review and back (rejected with one comment)."""
    from onepagerapp.review import add_review_comment  # noqa: PLC0415
    from onepagerapp.state_machine import Actor  # noqa: PLC0415
    from onepagerapp.workflow import reject_one_pager  # noqa: PLC0415

    data_access = services["data_access"]
    rows = data_access._status_rows
    rows["OP-0003"].one_pager_status = "In Review"
    approver = make_user("CJO")
    roles = {Actor.APPROVER}
    add_review_comment(
        data_access, "OP-0003", approver, "dataSources", "Add sources", roles=roles
    )
    reject_one_pager(data_access, "OP-0003", approver, "See comments", roles=roles)
    return data_access.get_review_comments("OP-0003")[0].id


@pytest.mark.unit
def test__editor__review_tab_checklist_and_blocked_submit(
    services: dict, switched: list[str]
) -> None:
    at = _tab(_editor(services).run(), "Review")

    assert not at.exception
    assert any(m.value == "✅ Basics" for m in at.markdown)
    check = at.button(key="edit_review_check_Data Sources")
    assert "issue(s)" in check.label
    assert at.button(key="edit_review_submit").disabled
    assert "No review comments." in [c.value for c in at.caption]

    check.click().run()
    assert at.radio(key="edit_active_tab").value == "Data Sources"


@pytest.mark.unit
def test__editor__review_tab_resolves_comments(
    services: dict, switched: list[str]
) -> None:
    comment_id = _reject_with_comment(services)
    at = _tab(_editor(services).run(), "Review")

    assert not at.exception
    assert any("Data Sources" in m.value for m in at.markdown)
    at.button(key=f"edit_resolve_{comment_id}").click().run()

    assert not at.exception
    comment = services["data_access"].get_review_comments("OP-0003")[0]
    assert (comment.resolved, comment.resolved_by) == (True, "ABR")
    assert "The comment was marked as resolved." in [s.value for s in at.success]
    assert f"edit_resolve_{comment_id}" not in {b.key for b in at.button}


@pytest.mark.unit
def test__editor__review_tab_submits_when_complete(
    services: dict, switched: list[str]
) -> None:
    from tests.unit.test_editing_links import fill_all_sections  # noqa: PLC0415

    at = _editor(services).run()
    fill_all_sections(at.session_state["edit_document"])
    at.text_input(key="edit_change_summary").input("Complete").run()
    _tab(at, "Review")
    assert at.button(key="edit_review_submit").disabled  # unsaved changes
    _button(at, "Save Draft").click().run()
    assert not at.button(key="edit_review_submit").disabled

    at.button(key="edit_review_submit").click().run()

    assert not at.exception
    assert switched == ["views/preview.py"]
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert row.one_pager_status == "In Review"


@pytest.mark.unit
def test__preview__update_asks_for_confirmation(
    services: dict, switched: list[str]
) -> None:
    # OP-0001: Approved / Ready for Development, owned by Alice.
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0001"}).run()
    at.button(key="preview_update").click().run()

    assert not at.exception
    assert any("working copy for editing" in m.value for m in at.markdown)
    row = services["data_access"].get_one_pager_status_row("OP-0001")
    assert row.one_pager_status == "Approved"  # nothing happens before Confirm


@pytest.mark.unit
def test__editor__opens_approved_version_after_update(
    services: dict, switched: list[str]
) -> None:
    from onepagerapp.workflow import start_update  # noqa: PLC0415

    alice = services["current_user_info"]
    start_update(services["data_access"], "OP-0001", alice, confirmed=True)

    at = _editor(services, "OP-0001").run()

    assert not at.exception
    assert at.title[0].value == "Editing: Person Master Data (OP-0001)"
    assert services["data_access"].get_lock("OP-0001").locked_by_initials == "ABR"
