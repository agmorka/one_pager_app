"""Repeating-items pattern for array sections (UI_Design.md §4.2, §5).

1. A summary table shows the existing items.
2. **+ Add** opens an inline form below the table.
3. **Edit** on an item opens the form pre-filled with that item.
4. **Remove** on an item asks for confirmation, then removes it.
5. Changes only touch the editor's working copy; nothing is stored until
   **Save Draft**.

Items are the dicts stored in the document (camelCase keys). A list of plain
strings (e.g. ``outOfScope``) is edited through ``render_string_items``.

The item-level helpers (``clean_item``, ``item_errors``, ``summary_rows``) are
pure so they can be unit tested without Streamlit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from onepagerapp.validation import sanitize_text

TEXT = "text"
TEXTAREA = "textarea"
SELECT = "select"
BOOL = "bool"
MULTISELECT = "multiselect"
DATE = "date"


@dataclass(frozen=True)
class FieldSpec:
    """One field of a repeating item.

    Attributes:
        name: Key in the stored item (camelCase, as in the schema).
        label: Form and table label.
        kind: Widget kind: text, textarea, select, bool, multiselect or date.
        required: The inline form refuses to add the item without it.
        options: Choices for select / multiselect.
        help: Widget help text.
        only_if: Name of a bool field; this field is cleared unless that field
            is checked (e.g. CDE tiering only for Critical Data Elements).
        editable: False for values the application assigns (e.g. BR IDs);
            they are shown but never entered.
        in_table: Whether the summary table shows the field.
        max_chars: Maximum length of a text value.

    """

    name: str
    label: str
    kind: str = TEXT
    required: bool = False
    options: tuple[str, ...] = ()
    help: str | None = None
    only_if: str | None = None
    editable: bool = True
    in_table: bool = True
    max_chars: int | None = None


@dataclass
class RepeatingSection:
    """Configuration of one repeating list in the editor.

    Attributes:
        key: Unique widget-key prefix (e.g. ``edit_br``).
        fields: The item fields, in form order.
        item_label: Singular noun for buttons and messages ("Requirement").
        title_field: Field used to name an item in the list and dialogs.
        on_add: Called with a new item before it is appended (e.g. to assign
            an ID). May raise to refuse the add; the message is shown.
        empty: Text shown when there are no items.
        extra_validation: Extra checks on a cleaned item; returns messages.
        on_change: Called after the items changed, before the page re-runs.

    """

    key: str
    fields: list[FieldSpec]
    item_label: str
    title_field: str
    on_add: Callable[[dict[str, Any]], None] | None = None
    empty: str = "Nothing added yet."
    extra_validation: Callable[[dict[str, Any]], list[str]] | None = None
    on_change: Callable[[], None] | None = None


# ============================================================================
# Pure helpers
# ============================================================================


def _clean_value(spec: FieldSpec, value: object) -> object:
    if spec.kind == BOOL:
        return bool(value)
    if spec.kind == MULTISELECT:
        return [str(v) for v in value or [] if str(v).strip()]  # type: ignore[attr-defined]
    if spec.kind == DATE:
        if isinstance(value, date):
            return value.isoformat()
        return sanitize_text(str(value)) if value else ""
    return sanitize_text(str(value)) if value is not None else ""


def clean_item(fields: list[FieldSpec], values: dict[str, Any]) -> dict[str, Any]:
    """Build a stored item from form values.

    Text is trimmed and stripped of HTML, empty optional values are dropped
    and ``only_if`` fields are cleared when their condition is not met.
    Values of fields that are not editable are kept as given.
    """
    item: dict[str, Any] = {}
    for spec in fields:
        if spec.name not in values:
            continue
        value = values[spec.name]
        if spec.editable:
            value = _clean_value(spec, value)
        if spec.only_if and not values.get(spec.only_if):
            continue
        if value in ("", None, []):
            continue
        item[spec.name] = value
    return item


def item_errors(fields: list[FieldSpec], item: dict[str, Any]) -> list[str]:
    """Messages for required fields that are missing from a cleaned item."""
    return [
        f"{spec.label} is required."
        for spec in fields
        if spec.required
        and spec.editable
        and spec.kind != BOOL
        and item.get(spec.name) in (None, "", [])
    ]


def _display(value: object) -> object:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else value


def summary_rows(fields: list[FieldSpec], items: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the summary table: one row per item, one column per table field."""
    shown = [f for f in fields if f.in_table]
    return pd.DataFrame(
        [{f.label: _display(item.get(f.name)) for f in shown} for item in items],
        columns=[f.label for f in shown],
    )


def item_title(section: RepeatingSection, item: dict[str, Any], index: int) -> str:
    """Short name of an item for the list and the remove confirmation."""
    title = str(item.get(section.title_field) or "").strip()
    if len(title) > 60:  # noqa: PLR2004
        title = title[:57] + "..."
    return title or f"{section.item_label} {index + 1}"


# ============================================================================
# Streamlit component
# ============================================================================


def _rerun(section: RepeatingSection) -> None:
    if section.on_change:
        section.on_change()
    st.rerun()


def _form_key(section: RepeatingSection) -> str:
    return f"{section.key}_form"


def _widget_key(section: RepeatingSection, spec: FieldSpec) -> str:
    return f"{section.key}_f_{spec.name}"


def _open_form(section: RepeatingSection, target: int | str, item: dict) -> None:
    """Open the inline form for a new item ("new") or item ``target``."""
    for spec in section.fields:
        value = item.get(spec.name)
        if spec.kind == BOOL:
            value = bool(value)
        elif spec.kind == MULTISELECT:
            value = [v for v in value or [] if v in spec.options]
        elif spec.kind == SELECT:
            value = value if value in spec.options else None
        elif spec.kind == DATE:
            try:
                value = date.fromisoformat(str(value)) if value else None
            except ValueError:
                value = None
        else:
            value = "" if value is None else str(value)
        st.session_state[_widget_key(section, spec)] = value
    st.session_state[_form_key(section)] = target
    st.session_state.pop(f"{section.key}_form_errors", None)


def _close_form(section: RepeatingSection) -> None:
    st.session_state.pop(_form_key(section), None)
    st.session_state.pop(f"{section.key}_form_errors", None)


def _render_widget(section: RepeatingSection, spec: FieldSpec) -> None:
    key = _widget_key(section, spec)
    label = f"{spec.label} *" if spec.required and spec.kind != BOOL else spec.label
    if spec.only_if:
        label += " (only when checked above)"
    if not spec.editable:
        st.text_input(label, key=key, disabled=True, help=spec.help)
    elif spec.kind == TEXTAREA:
        st.text_area(label, key=key, help=spec.help, max_chars=spec.max_chars)
    elif spec.kind == SELECT:
        st.selectbox(
            label,
            options=spec.options,
            key=key,
            index=None,
            placeholder="Select…",
            help=spec.help,
        )
    elif spec.kind == BOOL:
        st.checkbox(label, key=key, help=spec.help)
    elif spec.kind == MULTISELECT:
        st.multiselect(label, options=spec.options, key=key, help=spec.help)
    elif spec.kind == DATE:
        st.date_input(label, key=key, help=spec.help, value=None)
    else:
        st.text_input(label, key=key, help=spec.help, max_chars=spec.max_chars)


def _submit_form(section: RepeatingSection, items: list[dict[str, Any]]) -> bool:
    """Apply the inline form to ``items``. Returns True if the form can close."""
    target = st.session_state.get(_form_key(section))
    values = {
        spec.name: st.session_state.get(_widget_key(section, spec))
        for spec in section.fields
    }
    item = clean_item(section.fields, values)
    errors = item_errors(section.fields, item)
    if section.extra_validation:
        errors += section.extra_validation(item)
    if errors:
        st.session_state[f"{section.key}_form_errors"] = errors
        return False
    if target == "new":
        if section.on_add:
            try:
                section.on_add(item)
            except Exception as e:  # noqa: BLE001 - shown to the user
                st.session_state[f"{section.key}_form_errors"] = [str(e)]
                return False
        items.append(item)
    elif isinstance(target, int) and 0 <= target < len(items):
        items[target] = item
    return True


def _render_form(section: RepeatingSection, items: list[dict[str, Any]]) -> None:
    target = st.session_state.get(_form_key(section))
    title = (
        f"New {section.item_label.lower()}"
        if target == "new"
        else f"Edit {item_title(section, items[int(target)], int(target))}"
    )
    with st.container(border=True):
        st.markdown(f"**{title}**")
        for spec in section.fields:
            _render_widget(section, spec)
        for message in st.session_state.get(f"{section.key}_form_errors", []):
            st.error(message, icon="⚠️")
        col_ok, col_cancel, _ = st.columns([1, 1, 3])
        ok = col_ok.button(
            "Add" if target == "new" else "Apply",
            key=f"{section.key}_form_ok",
            type="primary",
            use_container_width=True,
        )
        cancel = col_cancel.button(
            "Cancel", key=f"{section.key}_form_cancel", use_container_width=True
        )
    if ok:
        if _submit_form(section, items):
            _close_form(section)
        _rerun(section)
    if cancel:
        _close_form(section)
        _rerun(section)


def _render_remove_confirmation(
    section: RepeatingSection, items: list[dict[str, Any]], index: int
) -> None:
    confirm_key = f"{section.key}_confirm_remove"
    st.warning(f"Remove **{item_title(section, items[index], index)}**?")
    col_yes, col_no, _ = st.columns([1, 1, 3])
    if col_yes.button("Remove", key=f"{section.key}_remove_yes", type="primary"):
        del items[index]
        st.session_state.pop(confirm_key, None)
        _close_form(section)
        _rerun(section)
    if col_no.button("Keep", key=f"{section.key}_remove_no"):
        st.session_state.pop(confirm_key, None)
        _rerun(section)


def render_repeating_items(
    section: RepeatingSection, items: list[dict[str, Any]]
) -> None:
    """Render the summary table, per-item actions and the inline form.

    ``items`` is the list inside the editor's working copy; it is changed in
    place.
    """
    if items:
        st.dataframe(
            summary_rows(section.fields, items),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption(section.empty)

    confirm_index = st.session_state.get(f"{section.key}_confirm_remove")
    for i, item in enumerate(items):
        col_title, col_edit, col_remove = st.columns([6, 1, 1])
        col_title.write(f"{i + 1}. {item_title(section, item, i)}")
        if col_edit.button("Edit", key=f"{section.key}_edit_{i}"):
            _open_form(section, i, item)
            _rerun(section)
        if col_remove.button("Remove", key=f"{section.key}_remove_{i}"):
            st.session_state[f"{section.key}_confirm_remove"] = i
            _rerun(section)
    if isinstance(confirm_index, int) and 0 <= confirm_index < len(items):
        _render_remove_confirmation(section, items, confirm_index)

    if _form_key(section) in st.session_state:
        target = st.session_state[_form_key(section)]
        if target == "new" or (isinstance(target, int) and target < len(items)):
            _render_form(section, items)
            return
        _close_form(section)
    if st.button(f"➕ Add {section.item_label}", key=f"{section.key}_add"):  # noqa: RUF001
        _open_form(section, "new", {})
        _rerun(section)


STRING_FIELD = "text"


def render_string_items(
    key: str, items: list[str], item_label: str, empty: str
) -> None:
    """Repeating pattern for a list of plain strings (changed in place)."""
    as_dicts = [{STRING_FIELD: value} for value in items]

    def sync() -> None:
        items[:] = [str(d[STRING_FIELD]) for d in as_dicts if d.get(STRING_FIELD)]

    section = RepeatingSection(
        key=key,
        fields=[FieldSpec(STRING_FIELD, item_label, kind=TEXTAREA, required=True)],
        item_label=item_label,
        title_field=STRING_FIELD,
        empty=empty,
        on_change=sync,
    )
    render_repeating_items(section, as_dicts)
