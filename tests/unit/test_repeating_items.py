"""Repeating-items pattern: pure helpers and the Streamlit component."""

from collections.abc import Callable
from datetime import date
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def repeating(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return ``adapters.repeating``."""
    return import_app_module("adapters.repeating")


@pytest.fixture
def fields(repeating: ModuleType) -> list:
    """Return one field of every kind, with a required and a conditional one."""
    r = repeating
    return [
        r.FieldSpec("id", "ID", editable=False),
        r.FieldSpec("requirement", "Requirement", kind=r.TEXTAREA, required=True),
        r.FieldSpec("priority", "Priority", kind=r.SELECT, options=("High", "Low")),
        r.FieldSpec("isCde", "CDE", kind=r.BOOL),
        r.FieldSpec("tier", "Tier", only_if="isCde"),
        r.FieldSpec("links", "Links", kind=r.MULTISELECT, options=("UC-001",)),
        r.FieldSpec("due", "Due", kind=r.DATE),
    ]


def _script() -> None:
    """Render a requirement list and a string list from the session state."""
    import streamlit as st  # noqa: PLC0415

    from adapters.repeating import (  # noqa: PLC0415
        FieldSpec,
        RepeatingSection,
        render_repeating_items,
        render_string_items,
    )

    items = st.session_state.setdefault("items", [])
    strings = st.session_state.setdefault("strings", [])

    def assign_id(item: dict) -> None:
        item["id"] = f"BR-{len(items) + 1:03d}"

    render_repeating_items(
        RepeatingSection(
            key="t",
            fields=[
                FieldSpec("id", "ID", editable=False),
                FieldSpec("requirement", "Requirement", required=True),
            ],
            item_label="Requirement",
            title_field="requirement",
            on_add=assign_id,
        ),
        items,
    )
    render_string_items("s", strings, "Assumption", "None yet.")


@pytest.fixture
def at(repeating: ModuleType) -> AppTest:
    """Run the component script with empty lists."""
    app = AppTest.from_function(_script, default_timeout=30)
    app.session_state["items"] = []
    app.session_state["strings"] = []
    return app.run()


def _click(at: AppTest, key: str) -> None:
    """Click the button ``key`` and rerun."""
    at.button(key=key).click().run()


def _add_requirement(at: AppTest, text: str) -> None:
    """Add a requirement through the form."""
    _click(at, "t_add")
    at.text_input(key="t_f_requirement").input(text)
    _click(at, "t_form_ok")


# ============================================================================
# Pure helpers
# ============================================================================


@pytest.mark.unit
def test__raw_item__clean_item__sanitized_with_empty_and_hidden_dropped(
    repeating: ModuleType, fields: list
) -> None:
    """HTML is stripped, dates become ISO, empty and inapplicable values go."""
    # When
    item = repeating.clean_item(
        fields,
        {
            "id": "BR-001",
            "requirement": "  <b>Fast</b> loads ",
            "priority": None,
            "isCde": False,
            "tier": "Tier 1",
            "links": [],
            "due": date(2026, 10, 1),
        },
    )

    # Then
    assert item == {
        "id": "BR-001",
        "requirement": "Fast loads",
        "isCde": False,
        "due": "2026-10-01",
    }


@pytest.mark.unit
def test__condition_met__clean_item__conditional_field_kept(
    repeating: ModuleType, fields: list
) -> None:
    """An ``only_if`` field is kept when its condition is true."""
    # When
    item = repeating.clean_item(
        fields, {"requirement": "x", "isCde": True, "tier": "Tier 1"}
    )

    # Then
    assert item["tier"] == "Tier 1"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("item", "errors"),
    [({}, ["Requirement is required."]), ({"requirement": "x"}, [])],
    ids=["missing", "given"],
)
def test__item__item_errors__required_fields_checked(
    repeating: ModuleType, fields: list, item: dict, errors: list[str]
) -> None:
    """Required fields must be filled."""
    # When
    result = repeating.item_errors(fields, item)

    # Then
    assert result == errors


@pytest.mark.unit
def test__item__summary_rows__values_formatted(
    repeating: ModuleType, fields: list
) -> None:
    """Booleans read Yes/No, lists are joined and missing values are blank."""
    # When
    df = repeating.summary_rows(
        fields, [{"requirement": "x", "isCde": True, "links": ["UC-001", "UC-002"]}]
    )

    # Then
    row = df.iloc[0].to_dict()
    assert (row["CDE"], row["Links"], row["Priority"]) == (
        "Yes",
        "UC-001, UC-002",
        "",
    )


# ============================================================================
# Component
# ============================================================================


@pytest.mark.unit
def test__empty_form__click_ok__refused_with_error(at: AppTest) -> None:
    """An item with a missing required field is not added."""
    # Given
    _click(at, "t_add")

    # When
    _click(at, "t_form_ok")

    # Then
    assert "Requirement is required." in at.error[0].value
    assert at.session_state["items"] == []


@pytest.mark.unit
def test__filled_form__click_ok__item_added_with_id(at: AppTest) -> None:
    """A valid item is added and gets its ID from ``on_add``."""
    # When
    _add_requirement(at, "Fast loads")

    # Then
    assert not at.exception
    assert at.session_state["items"] == [{"id": "BR-001", "requirement": "Fast loads"}]


@pytest.mark.unit
def test__item__edit_and_ok__changed_and_id_kept(at: AppTest) -> None:
    """Editing prefills the form and keeps the ID."""
    # Given
    _add_requirement(at, "Fast loads")
    _click(at, "t_edit_0")
    assert at.text_input(key="t_f_requirement").value == "Fast loads"

    # When
    at.text_input(key="t_f_requirement").input("Faster loads")
    _click(at, "t_form_ok")

    # Then
    assert at.session_state["items"] == [
        {"id": "BR-001", "requirement": "Faster loads"}
    ]


@pytest.mark.unit
def test__item__remove_and_say_no__kept(at: AppTest) -> None:
    """Removing asks first; No keeps the item."""
    # Given
    _add_requirement(at, "Fast loads")
    _click(at, "t_remove_0")

    # When
    _click(at, "t_remove_no")

    # Then
    assert len(at.session_state["items"]) == 1


@pytest.mark.unit
def test__item__remove_and_say_yes__removed(at: AppTest) -> None:
    """Confirming removes the item."""
    # Given
    _add_requirement(at, "Fast loads")
    _click(at, "t_remove_0")

    # When
    _click(at, "t_remove_yes")

    # Then
    assert not at.exception
    assert at.session_state["items"] == []


@pytest.mark.unit
def test__form_with_input__click_cancel__discarded(at: AppTest) -> None:
    """Cancel closes the form without adding anything."""
    # Given
    _click(at, "t_add")
    at.text_input(key="t_f_requirement").input("Draft")

    # When
    _click(at, "t_form_cancel")

    # Then
    assert at.session_state["items"] == []
    assert "t_form" not in at.session_state


@pytest.mark.unit
def test__string_form_filled__click_ok__string_added(at: AppTest) -> None:
    """A text item is added to the string list."""
    # Given
    _click(at, "s_add")
    at.text_area(key="s_f_text").input("Data is refreshed daily")

    # When
    _click(at, "s_form_ok")

    # Then
    assert at.session_state["strings"] == ["Data is refreshed daily"]


@pytest.mark.unit
def test__string_item__remove_and_confirm__removed(at: AppTest) -> None:
    """A text item can be removed."""
    # Given
    _click(at, "s_add")
    at.text_area(key="s_f_text").input("Data is refreshed daily")
    _click(at, "s_form_ok")
    _click(at, "s_remove_0")

    # When
    _click(at, "s_remove_yes")

    # Then
    assert at.session_state["strings"] == []
