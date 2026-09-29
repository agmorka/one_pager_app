"""Repeating-items pattern: pure helpers and the Streamlit component."""

from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def repeating(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    from adapters import repeating  # noqa: PLC0415

    return repeating


def _fields(r: ModuleType) -> list:
    return [
        r.FieldSpec("id", "ID", editable=False),
        r.FieldSpec("requirement", "Requirement", kind=r.TEXTAREA, required=True),
        r.FieldSpec("priority", "Priority", kind=r.SELECT, options=("High", "Low")),
        r.FieldSpec("isCde", "CDE", kind=r.BOOL),
        r.FieldSpec("tier", "Tier", only_if="isCde"),
        r.FieldSpec("links", "Links", kind=r.MULTISELECT, options=("UC-001",)),
        r.FieldSpec("due", "Due", kind=r.DATE),
    ]


@pytest.mark.unit
def test__clean_item__sanitizes_and_drops_empty(repeating: ModuleType) -> None:
    item = repeating.clean_item(
        _fields(repeating),
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
    assert item == {
        "id": "BR-001",
        "requirement": "Fast loads",
        "isCde": False,
        "due": "2026-10-01",
    }


@pytest.mark.unit
def test__clean_item__only_if_kept_when_condition_met(repeating: ModuleType) -> None:
    item = repeating.clean_item(
        _fields(repeating), {"requirement": "x", "isCde": True, "tier": "Tier 1"}
    )
    assert item["tier"] == "Tier 1"


@pytest.mark.unit
def test__item_errors__required_fields(repeating: ModuleType) -> None:
    fields = _fields(repeating)
    assert repeating.item_errors(fields, {}) == ["Requirement is required."]
    assert repeating.item_errors(fields, {"requirement": "x"}) == []


@pytest.mark.unit
def test__summary_rows__formats_values(repeating: ModuleType) -> None:
    df = repeating.summary_rows(
        _fields(repeating),
        [{"requirement": "x", "isCde": True, "links": ["UC-001", "UC-002"]}],
    )
    row = df.iloc[0].to_dict()
    assert row["CDE"] == "Yes"
    assert row["Links"] == "UC-001, UC-002"
    assert row["Priority"] == ""


def _script() -> None:
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


def _app() -> AppTest:
    at = AppTest.from_function(_script, default_timeout=30)
    at.session_state["items"] = []
    at.session_state["strings"] = []
    return at


def _click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()


@pytest.mark.unit
def test__component__add_edit_remove(repeating: ModuleType) -> None:
    at = _app().run()
    assert not at.exception

    _click(at, "t_add")
    _click(at, "t_form_ok")  # empty → refused
    assert "Requirement is required." in at.error[0].value
    assert at.session_state["items"] == []

    at.text_input(key="t_f_requirement").input("Fast loads")
    _click(at, "t_form_ok")
    assert at.session_state["items"] == [{"id": "BR-001", "requirement": "Fast loads"}]

    _click(at, "t_edit_0")
    assert at.text_input(key="t_f_requirement").value == "Fast loads"
    at.text_input(key="t_f_requirement").input("Faster loads")
    _click(at, "t_form_ok")
    assert at.session_state["items"][0]["requirement"] == "Faster loads"
    assert at.session_state["items"][0]["id"] == "BR-001"

    _click(at, "t_remove_0")
    _click(at, "t_remove_no")
    assert len(at.session_state["items"]) == 1
    _click(at, "t_remove_0")
    _click(at, "t_remove_yes")
    assert at.session_state["items"] == []
    assert not at.exception


@pytest.mark.unit
def test__component__cancel_discards_form(repeating: ModuleType) -> None:
    at = _app().run()
    _click(at, "t_add")
    at.text_input(key="t_f_requirement").input("Draft")
    _click(at, "t_form_cancel")
    assert at.session_state["items"] == []
    assert "t_form" not in at.session_state


@pytest.mark.unit
def test__string_items__add_and_remove(repeating: ModuleType) -> None:
    at = _app().run()
    _click(at, "s_add")
    at.text_area(key="s_f_text").input("Data is refreshed daily")
    _click(at, "s_form_ok")
    assert at.session_state["strings"] == ["Data is refreshed daily"]

    _click(at, "s_remove_0")
    _click(at, "s_remove_yes")
    assert at.session_state["strings"] == []
