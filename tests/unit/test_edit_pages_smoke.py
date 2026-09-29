"""AppTest smoke tests for the Editor in edit mode (local-mock mode).

As in test_create_pages_smoke.py, each page script runs on its own with the
services injected into session state, and st.switch_page is recorded.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import NewOnePagerInput, PersonRef
from onepagerapp.workflow import create_one_pager
from tests.conftest import FIXTURES_DIR

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
    user = resolve_current_user("alice.brown@company.com")
    result = create_one_pager(
        NewOnePagerInput(
            data_product="customer_master",
            product_name="Customer Master",
            business_domain="Customer",
            data_product_type="Foundational",
            description="Unified customer view",
            owner=PersonRef("Alice Brown", "AB", "alice.brown@company.com"),
            smes=[PersonRef("Diana Prince", "DPR", "diana@bec.dk")],
        ),
        user,
        data_access,
        store,
        now=NOW,
    )
    assert result.one_pager_id == "OP-0003"
    return {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
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
def test__preview__edit_disabled_for_non_owner(
    services: dict, switched: list[str]
) -> None:
    # Alice is not Owner/SME of OP-0002 (and it is In Review).
    at = _app("preview.py", {**services, "preview_one_pager_id": "OP-0002"}).run()
    assert at.button(key="preview_edit").disabled


@pytest.mark.unit
def test__editor__edit_mode_prefills_basics_and_locks(
    services: dict, switched: list[str]
) -> None:
    at = _editor(services).run()

    assert not at.exception
    assert at.title[0].value == "Editing: Customer Master (OP-0003)"
    assert at.text_input(key="edit_product_name").value == "Customer Master"
    assert at.text_input(key="edit_owner_initials").value == "AB"
    assert at.text_area(key="edit_description").value == "Unified customer view"
    lock = services["data_access"].get_lock("OP-0003")
    assert lock.locked_by_initials == "AB"


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

    sme = resolve_current_user("dpr@bec.dk")
    acquire_lock(services["data_access"], "OP-0003", sme, "other-session")

    at = _editor(services).run()

    assert not at.exception
    assert "Locked by Dpr" in at.warning[0].value
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
    assert "Describe what you changed." in at.warning[0].value
    row = services["data_access"].get_one_pager_status_row("OP-0003")
    assert row.version == "0.1.0"
