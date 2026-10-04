"""Unsaved-changes guard: dirty detection and the navigation guard."""

from collections.abc import Callable
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import has_unsaved_changes, working_copy
from onepagerapp.models import OnePagerDocument
from tests.helpers import NEW_ID, button_labelled, editor_page, switch_tab


@pytest.fixture
def edit_mode(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return ``adapters.edit_mode``."""
    return import_app_module("adapters.edit_mode")


@pytest.fixture
def saved() -> OnePagerDocument:
    """Return a minimal saved Draft document."""
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
        owner_initials="ABR",
        owner_email="a@b.dk",
        owner_team=None,
    )


def _guard_script() -> None:
    """Run the navigation guard as the Registry page would."""
    import streamlit as st  # noqa: PLC0415

    from adapters.edit_mode import navigation_guard  # noqa: PLC0415

    navigation_guard(
        "Registry", st.session_state.data_access, st.session_state.current_user_info
    )
    st.write("page body")


def _registry_after(editor: AppTest) -> AppTest:
    """Run the guard with the Editor's session state, as on leaving the Editor."""
    at = AppTest.from_function(_guard_script, default_timeout=30)
    for key, value in editor.session_state.filtered_state.items():
        at.session_state[key] = value
    return at.run()


def _edited_problem(data_access: MockDataAccess) -> AppTest:
    """Open the Editor and change the problem statement without saving."""
    at = switch_tab(editor_page(data_access).run(), "Business Problem")
    at.text_area(key="edit_problem").input("Changed").run()
    return at


@pytest.mark.unit
def test__untouched_working_copy__has_unsaved_changes__false(
    saved: OnePagerDocument,
) -> None:
    """A fresh working copy is clean."""
    # When
    dirty = has_unsaved_changes(saved, working_copy(saved))

    # Then
    assert not dirty


@pytest.mark.unit
def test__only_whitespace_and_blank_rows__has_unsaved_changes__false(
    saved: OnePagerDocument,
) -> None:
    """Changes that normalization removes do not count."""
    # Given
    working = working_copy(saved)
    working.description = "  Desc  "
    working.smes.append({"name": "", "initials": ""})

    # When
    dirty = has_unsaved_changes(saved, working)

    # Then
    assert not dirty


@pytest.mark.unit
def test__new_assumption__has_unsaved_changes__true(saved: OnePagerDocument) -> None:
    """Real content changes count."""
    # Given
    working = working_copy(saved)
    working.assumptions.append("New assumption")

    # When
    dirty = has_unsaved_changes(saved, working)

    # Then
    assert dirty


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
def test__navigation_state__guard_action__none_confirm_or_release(
    edit_mode: ModuleType,
    page: str,
    mode: str | None,
    editor_id: str | None,
    edit_id: str | None,
    dirty: bool,
    expected: str,
) -> None:
    """Leaving an edit session asks first if dirty, else releases the lock."""
    # When
    action = edit_mode.guard_action(
        on_editor_page=page == "Editor",
        editor_mode=mode,
        editor_one_pager_id=editor_id,
        edit_one_pager_id=edit_id,
        dirty=dirty,
    )

    # Then
    assert action == expected


@pytest.mark.unit
def test__unsaved_changes__click_close_editor__confirmation_and_lock_kept(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Close asks for confirmation instead of discarding changes."""
    # Given
    at = _edited_problem(alices_draft)
    assert any("Unsaved changes" in c.value for c in at.caption)

    # When
    button_labelled(at, "Close editor").click().run()

    # Then
    assert not at.exception
    assert switched == []
    assert alices_draft.get_lock(NEW_ID) is not None


@pytest.mark.unit
def test__clean_edit_session__navigate_away__closed_and_lock_released(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Leaving a clean session closes it and releases the lock."""
    # Given
    editor = editor_page(alices_draft).run()
    assert alices_draft.get_lock(NEW_ID) is not None

    # When
    at = _registry_after(editor)

    # Then
    assert not at.exception
    assert alices_draft.get_lock(NEW_ID) is None
    assert "edit_document" not in at.session_state


@pytest.mark.unit
def test__dirty_edit_session__navigate_away__warned_and_kept(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Leaving a dirty session keeps the lock and the changes, with a warning."""
    # Given
    editor = _edited_problem(alices_draft)

    # When
    at = _registry_after(editor)

    # Then
    assert not at.exception
    assert "Unsaved changes in the Editor" in at.sidebar.warning[0].value
    assert alices_draft.get_lock(NEW_ID) is not None
    assert at.session_state["edit_document"].business_problem_statement == "Changed"


@pytest.mark.unit
def test__dirty_session_warning__click_return__back_in_the_editor(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The warning links back to the Editor."""
    # Given
    at = _registry_after(_edited_problem(alices_draft))

    # When
    at.sidebar.button(key="guard_return").click().run()

    # Then
    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "edit"
