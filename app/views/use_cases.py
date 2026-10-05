"""Use Cases page — browse and manage the shared Use Case registry.

Use Cases are shared across One Pagers (Requirements_and_Scope.md §9). The view:
1. Provides a filter bar (persona/goal search, priority, show deprecated)
2. Renders a paginated table with the number of One Pagers using each Use Case;
   clicking a row selects it
3. Shows a detail panel beside the table for the selected Use Case, including
   the One Pagers that reference it (each opens in Preview), so editors can
   assess the impact of a change before saving
4. Lets Owners/SMEs create, edit, deprecate (with confirmation) and restore
5. Handles all page states: Loading, Populated, Empty, Empty (after filter), Error

Per UI_Design.md §2 every user can browse; write actions are gated by
permissions.can_manage_use_cases. Deprecation is a soft delete — Use Cases are
never removed because One Pagers may still reference them.
Filtering, pagination, validation and ID generation live in onepagerapp, not here.
"""

import logging
import re

import pandas as pd
import streamlit as st

from adapters.cache import get_use_cases, writes_data
from adapters.navigation import USE_CASE_SELECTED_KEY, open_preview
from adapters.page import (
    current_initials,
    current_roles,
    current_user,
    page_header,
    render_error_state,
    require_data_access,
    set_flash,
    show_flash,
    timestamp_label,
)
from adapters.tables import selected_value
from adapters.theme import section_gap
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import TOPIC_USE_CASES
from onepagerapp.models import (
    PRIORITY_OPTIONS,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)
from onepagerapp.permissions import can_manage_use_cases
from onepagerapp.use_cases import (
    USE_CASE_FIELDS,
    clean_use_case_input,
    create_use_case,
    set_use_case_deprecated,
    update_use_case,
    validate_use_case_input,
)

logger = logging.getLogger(__name__)

LOAD_ERROR_MESSAGE = (
    "**Unable to load Use Cases.** Please try again. If the problem persists, "
    "check that the `use_cases` and `use_case_references` tables are deployed."
)

# Pagination settings
ROWS_PER_PAGE = 20

# Filter state defaults
FILTER_DEFAULTS: dict[str, object] = {
    "uc_filter_search": "",
    "uc_filter_priority": "All",
    "uc_filter_show_deprecated": False,
}

# Table columns to display
TABLE_COLUMNS = ["ID", "Persona", "Goal", "Priority", "Used by", "Status"]

_PAGE_KEY = "use_cases_page"
_SELECTED_KEY = USE_CASE_SELECTED_KEY
# Bumped to give the table a fresh selection (after Close).
_TABLE_VERSION_KEY = "uc_table_version"
FLASH_KEY = "uc_flash"

# Button labels are markdown: escape "+" so it isn't read as a list marker
_NEW_BUTTON_LABEL = "\\+ New Use Case"

# Characters with meaning in Streamlit markdown (incl. ":" for colors/emoji
# shortcodes and "$" for LaTeX). Escaped so stored text renders literally.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")


# ============================================================================
# Helpers
# ============================================================================


def _md(text: str) -> str:
    """Escape user-entered text for safe display inside st.markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _used_by_label(count: int) -> str:
    return f"{count} One Pager" if count == 1 else f"{count} One Pagers"


def _reset_page() -> None:
    st.session_state[_PAGE_KEY] = 1


def _clear_filters() -> None:
    """Reset all filter inputs to default values (on_click callback)."""
    for key, default in FILTER_DEFAULTS.items():
        st.session_state[key] = default
    _reset_page()


def _select_use_case(use_case_id: str | None) -> None:
    st.session_state[_SELECTED_KEY] = use_case_id
    if use_case_id is None:
        # Forget the row selection, or the table would select it again.
        st.session_state[_TABLE_VERSION_KEY] = (
            st.session_state.get(_TABLE_VERSION_KEY, 0) + 1
        )


# ============================================================================
# Dialogs: Create, Edit and Deprecate
# ============================================================================


def _render_use_case_form(data_access: DataAccess, existing: UseCase | None) -> None:
    """Render the create/edit form and save it on submit.

    Args:
        data_access: DataAccess instance.
        existing: Use Case being edited, or None to create a new one.

    """
    if existing is not None and existing.reference_count:
        st.warning(
            f"This Use Case is referenced by "
            f"{_used_by_label(existing.reference_count)}. "
            "Changes apply to all of them."
        )

    key_prefix = f"uc_form_{existing.use_case_id if existing else 'new'}"
    with st.form(key_prefix):
        values = {}
        for name, (label, max_length) in USE_CASE_FIELDS.items():
            widget = st.text_input if name == "persona" else st.text_area
            values[name] = widget(
                label,
                value=getattr(existing, name) if existing else "",
                max_chars=max_length,
                key=f"{key_prefix}_{name}",
            )
        values["priority"] = st.selectbox(
            "Priority",
            options=PRIORITY_OPTIONS,
            index=PRIORITY_OPTIONS.index(existing.priority)
            if existing and existing.priority in PRIORITY_OPTIONS
            else 0,
            key=f"{key_prefix}_priority",
        )
        submitted = st.form_submit_button("Save", type="primary")

    if not submitted:
        return

    data = clean_use_case_input(UseCaseInput(**values))
    errors = validate_use_case_input(data)
    if errors:
        # The form keeps the entered values; list what must be fixed.
        st.error(
            "Please fix the following:\n\n"
            + "\n".join(f"- {message}" for message in errors.values())
        )
        return

    try:
        with writes_data():
            if existing is None:
                new_id = create_use_case(
                    data_access, data, current_user(), roles=current_roles()
                )
            else:
                update_use_case(
                    data_access,
                    existing.use_case_id,
                    data,
                    current_user(),
                    roles=current_roles(),
                )
        if existing is None:
            _select_use_case(new_id)
            set_flash(FLASH_KEY, f"Created Use Case {new_id}.")
        else:
            set_flash(FLASH_KEY, f"Saved changes to {existing.use_case_id}.")
    except NotFoundError:
        st.error("This Use Case no longer exists. Close the dialog and refresh.")
        return
    except Exception:
        logger.exception("Failed to save use case")
        st.error(
            "The Use Case could not be saved. Your input is kept — please try again."
        )
        return
    st.rerun()


@st.dialog("New Use Case", width="large")
def _create_dialog(data_access: DataAccess) -> None:
    st.caption("The Use Case ID (UC-###) is assigned automatically when you save.")
    _render_use_case_form(data_access, existing=None)


@st.dialog("Edit Use Case", width="large")
def _edit_dialog(data_access: DataAccess, use_case: UseCase) -> None:
    st.caption(f"Editing **{use_case.use_case_id}**")
    _render_use_case_form(data_access, existing=use_case)


@st.dialog("Deprecate Use Case")
def _deprecate_dialog(
    data_access: DataAccess, use_case: UseCase, references: list[str]
) -> None:
    st.markdown(f"Deprecate **{use_case.use_case_id}** — {_md(use_case.persona)}?")
    if references:
        st.warning(
            f"Referenced by {', '.join(references)}. These One Pagers keep the "
            "Use Case, but it can no longer be linked to new One Pagers."
        )
    else:
        st.info("No One Pager references this Use Case.")
    st.caption("A deprecated Use Case can be restored later.")

    confirm_col, cancel_col, _ = st.columns([1, 1, 2])
    if confirm_col.button("Confirm", type="primary", key="uc_deprecate_confirm"):
        try:
            with writes_data():
                set_use_case_deprecated(
                    data_access,
                    use_case.use_case_id,
                    deprecated=True,
                    user=current_user(),
                    roles=current_roles(),
                )
        except Exception:
            logger.exception("Failed to deprecate use case")
            st.error("The Use Case could not be deprecated. Please try again.")
            return
        set_flash(FLASH_KEY, f"Deprecated {use_case.use_case_id}.")
        st.rerun()
    if cancel_col.button("Cancel", key="uc_deprecate_cancel"):
        st.rerun()


def _restore(data_access: DataAccess, use_case: UseCase) -> None:
    try:
        with writes_data():
            set_use_case_deprecated(
                data_access,
                use_case.use_case_id,
                deprecated=False,
                user=current_user(),
                roles=current_roles(),
            )
    except Exception:
        logger.exception("Failed to restore use case")
        st.error("The Use Case could not be restored. Please try again.")
        return
    set_flash(FLASH_KEY, f"Restored {use_case.use_case_id}.")
    st.rerun()


# ============================================================================
# Render Components
# ============================================================================


def _render_filter_bar() -> None:
    """Render the filter bar; values are read back from session state."""
    for key, default in FILTER_DEFAULTS.items():
        st.session_state.setdefault(key, default)

    search_col, priority_col, deprecated_col, clear_col = st.columns(
        [3, 1.5, 1.5, 1], vertical_alignment="bottom"
    )
    search_col.text_input(
        "Search persona or goal", key="uc_filter_search", on_change=_reset_page
    )
    priority_col.selectbox(
        "Priority",
        options=["All", *PRIORITY_OPTIONS],
        key="uc_filter_priority",
        on_change=_reset_page,
    )
    deprecated_col.checkbox(
        "Show deprecated", key="uc_filter_show_deprecated", on_change=_reset_page
    )
    clear_col.button("Clear filters", on_click=_clear_filters)


def table_frame(use_case_page: UseCasePage) -> pd.DataFrame:
    """Return the rows of the page as the table shows them."""
    return pd.DataFrame(
        [
            [
                use_case.use_case_id,
                use_case.persona,
                use_case.goal,
                use_case.priority,
                _used_by_label(use_case.reference_count),
                "Deprecated" if use_case.deprecated else "Active",
            ]
            for use_case in use_case_page.rows
        ],
        columns=TABLE_COLUMNS,
    )


def _render_table(use_case_page: UseCasePage) -> None:
    """Render Use Cases as a table; clicking a row shows its details."""
    frame = table_frame(use_case_page)
    event = st.dataframe(
        frame,
        key=f"uc_table_{st.session_state.get(_TABLE_VERSION_KEY, 0)}",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Goal": st.column_config.TextColumn(width="large"),
            "Used by": st.column_config.TextColumn(
                help="Number of One Pagers linked to the Use Case"
            ),
        },
    )
    clicked = selected_value(event, frame, "ID")
    if clicked and clicked != st.session_state.get(_SELECTED_KEY):
        st.session_state[_SELECTED_KEY] = clicked


def page_range_label(use_case_page: UseCasePage) -> str:
    """Return e.g. "Showing 21-40 of 57 Use Cases"."""
    first = (use_case_page.page - 1) * use_case_page.page_size + 1
    last = first + len(use_case_page.rows) - 1
    return f"Showing {first}-{last} of {use_case_page.total_rows} Use Cases"


def _render_pagination(current_page: int, total_pages: int) -> None:
    """Render pagination controls."""
    prev_col, page_col, next_col, _ = st.columns(
        [1, 1.2, 1, 5], vertical_alignment="center"
    )
    if prev_col.button("← Prev", disabled=current_page <= 1, key="uc_prev"):
        st.session_state[_PAGE_KEY] = current_page - 1
        st.rerun()
    page_col.markdown(f"Page {current_page} of {total_pages}")
    if next_col.button("Next →", disabled=current_page >= total_pages, key="uc_next"):
        st.session_state[_PAGE_KEY] = current_page + 1
        st.rerun()


def _render_reference_links(references: list[str]) -> None:
    """Render a button per referencing One Pager that opens it in Preview."""
    for column, one_pager_id in zip(
        st.columns(min(max(len(references), 1), 4)), references, strict=False
    ):
        if column.button(
            f"Open {one_pager_id}",
            key=f"uc_open_{one_pager_id}",
            help=f"Open {one_pager_id} in Preview",
        ):
            open_preview(one_pager_id)


def _render_details(
    data_access: DataAccess, use_case_id: str, *, can_manage: bool
) -> None:
    """Render the detail panel (all fields, references, actions)."""
    try:
        use_case = data_access.get_use_case(use_case_id)
        references = (
            data_access.get_use_case_references(use_case_id) if use_case else []
        )
    except Exception as e:
        logger.exception("Failed to load use case details")
        st.error(user_error_message(e, "Unable to load the details of this Use Case."))
        return
    if use_case is None:
        _select_use_case(None)
        return

    title = f"{use_case.use_case_id} — {use_case.persona}"
    if use_case.deprecated:
        title += " (Deprecated)"
    with st.container(border=True):
        st.markdown(f"#### {title}")
        for name, (label, _) in USE_CASE_FIELDS.items():
            st.markdown(f"**{label}:** {_md(getattr(use_case, name))}")
        st.markdown(f"**Priority:** {use_case.priority}")
        st.markdown(
            f"**Referenced by:** {', '.join(references)}"
            if references
            else "**Referenced by:** no One Pagers yet"
        )
        _render_reference_links(references)
        st.caption(
            f"Created by {_md(use_case.created_by)} on "
            f"{timestamp_label(use_case.created_at)} · Last updated by "
            f"{_md(use_case.last_updated_by)} on "
            f"{timestamp_label(use_case.last_updated_at)}"
        )

        action_cols = st.columns([1, 1.2, 1])
        if can_manage:
            if action_cols[0].button("Edit", key="uc_edit"):
                _edit_dialog(data_access, use_case)
            if use_case.deprecated:
                if action_cols[1].button("Restore", key="uc_restore"):
                    _restore(data_access, use_case)
            elif action_cols[1].button("Deprecate", key="uc_deprecate"):
                _deprecate_dialog(data_access, use_case, references)
        action_cols[2].button(
            "Close", key="uc_close", on_click=_select_use_case, args=(None,)
        )


# ============================================================================
# Use Cases Page
# ============================================================================

data_access = require_data_access()
can_manage = can_manage_use_cases(current_initials(), current_roles())

title_col, new_col = st.columns([5, 1], vertical_alignment="bottom")
with title_col:
    page_header(
        "Use Case Registry",
        "Browse the shared Use Cases referenced by One Pagers.",
        help_topic=TOPIC_USE_CASES,
    )
with new_col:
    if can_manage and st.button(_NEW_BUTTON_LABEL, type="primary", key="uc_new"):
        _create_dialog(data_access)

show_flash(FLASH_KEY)

_render_filter_bar()
section_gap()

search = st.session_state["uc_filter_search"].strip()
priority = st.session_state["uc_filter_priority"]
current_filter = UseCaseFilter(
    search=search or None,
    priority=priority if priority != "All" else None,
    include_deprecated=st.session_state["uc_filter_show_deprecated"],
)
has_active_filter = bool(search) or priority != "All"

st.session_state.setdefault(_PAGE_KEY, 1)

try:
    with st.spinner("Loading Use Cases..."):
        use_case_page = get_use_cases(
            data_access, current_filter, st.session_state[_PAGE_KEY], ROWS_PER_PAGE
        )
        # Clamp to the last page if rows disappeared (e.g. after deprecating)
        if not use_case_page.rows and st.session_state[_PAGE_KEY] > 1:
            st.session_state[_PAGE_KEY] = max(use_case_page.total_pages, 1)
            use_case_page = get_use_cases(
                data_access, current_filter, st.session_state[_PAGE_KEY], ROWS_PER_PAGE
            )
except Exception as e:
    logger.exception("Failed to fetch use cases")
    render_error_state(user_error_message(e, LOAD_ERROR_MESSAGE), key="uc_retry")

selected_id = st.session_state.get(_SELECTED_KEY)
table_col, details_col = (
    st.columns([3, 2], gap="medium") if selected_id else (st.container(), None)
)
with table_col:
    if use_case_page.rows:
        st.markdown(
            f"**{page_range_label(use_case_page)}** · click a row to see its details"
        )
        _render_table(use_case_page)
        if use_case_page.total_pages > 1:
            _render_pagination(use_case_page.page, use_case_page.total_pages)
    elif has_active_filter:
        st.warning("**No Use Cases match your filters.** Try adjusting your criteria.")
        st.button("Clear all filters", on_click=_clear_filters, key="uc_clear_empty")
    else:
        st.info(
            "**No Use Cases yet.**"
            + (
                " Use **\\+ New Use Case** to create the first one."
                if can_manage
                else ""
            )
        )

selected_id = st.session_state.get(_SELECTED_KEY)
if selected_id:
    if details_col is None:
        st.rerun()  # a row was just clicked: lay the page out with the panel
    with details_col:
        _render_details(data_access, selected_id, can_manage=can_manage)
