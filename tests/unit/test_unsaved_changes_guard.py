"""Unsaved-changes guard: dirty detection and the navigation guard."""

from pathlib import Path
from types import ModuleType

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.editing import has_unsaved_changes, working_copy
from onepagerapp.models import OnePagerDocument
from tests.unit.test_edit_pages_smoke import _button, _editor, _tab
from tests.unit.test_edit_pages_smoke import services as services  # noqa: PLC0414

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def edit_mode(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    from adapters import edit_mode  # noqa: PLC0415

    return edit_mode


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _doc() -> OnePagerDocument:
    return OnePagerDocument(
        structure_definition="structure_one_pager_v_2.json",
        data_product="p",
        product_name="Product",
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.1.0",
        description="Desc",
        owner_name="A",
        owner_initials="AB",
        owner_email="a@b.dk",
        owner_team=None,
    )


@pytest.mark.unit
def test__has_unsaved_changes() -> None:
    saved = _doc()
    working = working_copy(saved)
    assert not has_unsaved_changes(saved, working)

    working.description = "  Desc  "
    working.smes.append({"name": "", "initials": ""})
    assert not has_unsaved_changes(saved, working)  # normalization only

    working.assumptions.append("New assumption")
    assert has_unsaved_changes(saved, working)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("page", "mode", "editor_id", "edit_id", "dirty", "expected"),
    [
        ("Registry", None, None, None, False, "none"),  # nothing open
        ("Editor", "edit", "OP-1", "OP-1", True, "none"),  # still editing
        ("Registry", "edit", "OP-1", "OP-1", True, "confirm"),
        ("Registry", "edit", "OP-1", "OP-1", False, "release"),
        ("Editor", "create", "OP-1", "OP-1", True, "confirm"),  # + New meanwhile
        ("Editor", "edit", "OP-2", "OP-1", False, "release"),  # other One Pager
    ],
)
def test__guard_action(
    edit_mode: ModuleType,
    page: str,
    mode: str | None,
    editor_id: str | None,
    edit_id: str | None,
    dirty: bool,
    expected: str,
) -> None:
    assert (
        edit_mode.guard_action(
            on_editor_page=page == "Editor",
            editor_mode=mode,
            editor_one_pager_id=editor_id,
            edit_one_pager_id=edit_id,
            dirty=dirty,
        )
        == expected
    )


@pytest.mark.unit
def test__editor__close_with_unsaved_changes_asks_first(
    services: dict,  # noqa: F811
    switched: list[str],
) -> None:
    at = _tab(_editor(services).run(), "Business Problem")
    at.text_area(key="edit_problem").input("Changed").run()
    assert any("Unsaved changes" in c.value for c in at.caption)

    _button(at, "Close editor").click().run()

    assert not at.exception
    assert switched == []  # the confirmation dialog is shown instead
    assert services["data_access"].get_lock("OP-0003") is not None


def _guard_script() -> None:
    import streamlit as st  # noqa: PLC0415
    from adapters.edit_mode import navigation_guard  # noqa: PLC0415

    navigation_guard(
        "Registry", st.session_state.data_access, st.session_state.current_user_info
    )
    st.write("page body")


def _guard_app(state: dict) -> AppTest:
    at = AppTest.from_function(_guard_script, default_timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__guard__clean_session_is_closed_and_lock_released(
    services: dict,  # noqa: F811
    switched: list[str],
) -> None:
    editor = _editor(services).run()
    state = dict(editor.session_state.filtered_state)
    assert services["data_access"].get_lock("OP-0003") is not None

    at = _guard_app(state).run()

    assert not at.exception
    assert services["data_access"].get_lock("OP-0003") is None
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__guard__dirty_session_keeps_lock_and_warns(
    services: dict,  # noqa: F811
    switched: list[str],
) -> None:
    editor = _tab(_editor(services).run(), "Business Problem")
    editor.text_area(key="edit_problem").input("Changed").run()
    state = dict(editor.session_state.filtered_state)

    at = _guard_app(state).run()

    assert not at.exception
    assert "Unsaved changes in the Editor" in at.sidebar.warning[0].value
    assert services["data_access"].get_lock("OP-0003") is not None
    assert at.session_state["edit_document"].business_problem_statement == "Changed"

    at.sidebar.button(key="guard_return").click().run()
    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "edit"
