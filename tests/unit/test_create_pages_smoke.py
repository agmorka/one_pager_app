"""AppTest smoke tests for the create flow in local-mock mode.

Streamlit 1.38's AppTest does not render pages registered via st.navigation,
so each page script is run on its own with the services injected into session
state (as app.py would), and st.switch_page is replaced by a recorder.
"""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


@pytest.fixture
def services(tmp_path: Path) -> dict:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    user = make_user("ABR", "Alice Brown")
    return {
        "services_initialized": True,
        "data_access": MockDataAccess(store),
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
    }


def _app(page: str, state: dict) -> AppTest:
    at = AppTest.from_file(str(APP_DIR / "views" / page), default_timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at


def _click(at: AppTest, label: str) -> None:
    next(b for b in at.button if b.label == label).click().run()


@pytest.mark.unit
def test__registry__new_button_opens_editor_in_create_mode(
    services: dict, switched: list[str]
) -> None:
    at = _app("registry.py", services).run()
    assert not at.exception

    at.button(key="registry_new").click().run()

    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "create"


@pytest.mark.unit
def test__editor__without_intent_shows_start_message(
    services: dict, switched: list[str]
) -> None:
    at = _app("editor.py", services).run()
    assert not at.exception
    assert "Start from the Registry" in at.info[0].value


@pytest.mark.unit
def test__editor__owner_prefilled_from_current_user(
    services: dict, switched: list[str]
) -> None:
    at = _app("editor.py", {**services, "editor_mode": "create"}).run()
    assert not at.exception
    assert at.title[0].value == "New One Pager"
    assert at.text_input(key="create_owner_name").value == "Alice Brown"
    assert at.text_input(key="create_owner_initials").value == "ABR"
    assert at.text_input(key="create_owner_email").value == services["current_user"]


@pytest.mark.unit
def test__editor__owner_prefilled_from_the_directory(
    services: dict, switched: list[str]
) -> None:
    user = CurrentUser(
        username="x0wadm@becoc001.onmicrosoft.com",
        initials="X0W",
        display_name="Agnieszka Kępkowska",
        email="agnieszka.kepkowska@bec.dk",
    )
    state = {
        **services,
        "current_user": user.username,
        "current_user_info": user,
        "editor_mode": "create",
    }

    at = _app("editor.py", state).run()

    assert not at.exception
    assert at.text_input(key="create_owner_name").value == "Agnieszka Kępkowska"
    assert at.text_input(key="create_owner_initials").value == "X0W"
    assert at.text_input(key="create_owner_email").value == (
        "agnieszka.kepkowska@bec.dk"
    )


@pytest.mark.unit
def test__editor__empty_submit_shows_errors_and_writes_nothing(
    services: dict, switched: list[str]
) -> None:
    at = _app("editor.py", {**services, "editor_mode": "create"}).run()
    _click(at, "Create Draft")

    errors = [e.value for e in at.error]
    assert "Data Product is required." in errors
    assert "Description is required." in errors
    assert "5 issue(s)" in at.warning[0].value
    assert switched == []
    assert services["data_access"].get_one_pager_status("OP-0003") is None


@pytest.mark.unit
def test__editor__valid_submit_creates_and_opens_preview(
    services: dict, switched: list[str]
) -> None:
    at = _app("editor.py", {**services, "editor_mode": "create"}).run()
    at.text_input(key="create_data_product").input("customer_master")
    at.text_input(key="create_product_name").input("Customer Master")
    at.selectbox(key="create_business_domain").select("Customer")
    at.selectbox(key="create_data_product_type").select("Foundational")
    at.text_area(key="create_description").input("Unified customer view")
    at.run()

    _click(at, "Create Draft")

    assert not at.exception
    assert switched == ["views/preview.py"]
    assert at.session_state["preview_one_pager_id"] == "OP-0003"
    assert "OP-0003 created" in at.session_state["preview_flash"]
    header = services["data_access"].get_one_pager_status("OP-0003")
    assert header.product_name == "Customer Master"
    assert header.one_pager_status == "Draft"


@pytest.mark.unit
def test__preview__shows_created_one_pager(services: dict, switched: list[str]) -> None:
    at = _app("editor.py", {**services, "editor_mode": "create"}).run()
    at.text_input(key="create_data_product").input("customer_master")
    at.text_input(key="create_product_name").input("Customer Master")
    at.selectbox(key="create_business_domain").select("Customer")
    at.selectbox(key="create_data_product_type").select("Foundational")
    at.text_area(key="create_description").input("Unified customer view")
    at.run()
    _click(at, "Create Draft")

    preview = _app(
        "preview.py",
        {
            **services,
            "preview_one_pager_id": "OP-0003",
            "preview_flash": at.session_state["preview_flash"],
        },
    ).run()

    assert not preview.exception
    assert preview.title[0].value == "Customer Master"
    assert "OP-0003 created" in preview.success[0].value
