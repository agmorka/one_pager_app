"""Admin page — reference data and status definitions (UI_Design.md §4.7).

A section menu at the top, the selected section below it:

- **Business Domains**, **Data Product Types**, **Source Systems**: the values
  with their order, whether they are active and how many One Pagers use them;
  add a value, change its order, deactivate or reactivate it, delete it when
  no One Pager uses it.
- **Status Definitions**: display label, order and badge color of every One
  Pager and Data Product status.
- **Pending PRs**: approved One Pagers whose Git PR could not be created
  (``pending_pr``), with **Retry PR** per row. Retry needs the Git
  integration (Phase 8), so it is shown disabled until then.

Only Admins see the page in the navigation; everyone else who opens it gets
"You don't have access to this page." Every change is checked again by the
service (``onepagerapp.admin``). After a change the cached reference data is
cleared, so the Editor and the Registry show it at once.
"""

import logging
from collections.abc import Callable

import pandas as pd
import streamlit as st

from adapters.page import (
    ALERT_ICON,
    current_roles,
    render_retry_banner,
    require_data_access,
    set_flash,
    show_flash,
    signed_in_user,
    timestamp_label,
)
from adapters.theme import DEFAULT_BADGE_COLOR, status_badge
from onepagerapp.admin import (
    REFERENCE_KINDS,
    RETRY_PR_UNAVAILABLE,
    SAVE_FAILED_MESSAGE,
    STATUS_KINDS,
    AdminError,
    ReferenceKind,
    add_reference_value,
    check_can_administer,
    delete_reference_value,
    get_pending_prs,
    get_reference_values,
    get_status_definitions,
    update_reference_value,
    update_status_definition,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor

logger = logging.getLogger(__name__)

LOAD_ERROR_MESSAGE = "Couldn't load this section. Please retry."
FLASH_KEY = "admin_flash"
STATUS_SECTION = "Status Definitions"
PENDING_PRS_SECTION = "Pending PRs"
SECTIONS = [kind.title for kind in REFERENCE_KINDS] + [
    STATUS_SECTION,
    PENDING_PRS_SECTION,
]
PENDING_PR_COLUMNS = ["ID", "Product", "Version", "Approved", "Approved by", ""]
_PENDING_PR_RATIOS = [1.0, 2.4, 1.0, 1.8, 1.2, 1.2]


# ============================================================================
# Helpers
# ============================================================================


def apply_change(change: Callable[[], None], success: str) -> str | None:
    """Run an Admin change; on success clear cached reference data and re-run.

    Returns:
        A user-facing error, or None (then the page re-runs with ``success``).

    """
    try:
        change()
    except (PermissionDeniedError, AdminError) as e:
        return str(e)
    except Exception:
        logger.exception("Admin change failed")
        return SAVE_FAILED_MESSAGE
    # Reference data is cached by the Registry and the Editor (UI_Design §6).
    st.cache_data.clear()
    set_flash(FLASH_KEY, success)
    st.rerun()
    return None


def render_load_error(key: str, error: Exception) -> None:
    """Show the load error of a section with Retry; the rest of the page stays."""
    if render_retry_banner(user_error_message(error, LOAD_ERROR_MESSAGE), key):
        st.rerun()


# ============================================================================
# Render Components
# ============================================================================


def render_reference_section(
    data_access: DataAccess,
    kind: ReferenceKind,
    user: CurrentUser,
    roles: frozenset[Actor],
) -> None:
    """Values of one reference table with the add / edit / delete forms."""
    st.subheader(kind.title)
    try:
        values = get_reference_values(data_access, kind.table, user, roles)
    except Exception as e:
        logger.exception(f"Failed to load {kind.table}")
        render_load_error(f"admin_retry_{kind.table}", e)
        return

    if values:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Value": v.value,
                        "Order": v.sort_order,
                        "Active": "Yes" if v.active else "No",
                        "Used by": (
                            "-" if v.in_use is None else f"{v.in_use} One Pager(s)"
                        ),
                    }
                    for v in values
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info(f"No {kind.noun} values yet.")
    if kind.usage_filter is None:
        st.caption(
            f"One Pagers store the {kind.noun} as free text, so usage is not tracked."
        )

    col_edit, col_add = st.columns(2)
    with col_edit, st.container(border=True):
        st.markdown(f"**Change a {kind.noun}**")
        if values:
            _edit_reference_form(data_access, kind, values, user, roles)
        else:
            st.caption("Nothing to change yet.")
    with col_add, st.container(border=True):
        st.markdown(f"**Add a {kind.noun}**")
        _add_reference_form(data_access, kind, values, user, roles)


def _edit_reference_form(
    data_access: DataAccess,
    kind: ReferenceKind,
    values: list,
    user: CurrentUser,
    roles: frozenset[Actor],
) -> None:
    by_value = {v.value: v for v in values}
    selected = st.selectbox(
        kind.noun.capitalize(), options=list(by_value), key=f"admin_edit_{kind.table}"
    )
    current = by_value[selected]
    order = st.number_input(
        "Order",
        min_value=1,
        max_value=9999,
        step=1,
        value=max(current.sort_order, 1),
        key=f"admin_edit_order_{kind.table}_{selected}",
    )
    active = st.checkbox(
        "Active (can be chosen for One Pagers)",
        value=current.active,
        key=f"admin_edit_active_{kind.table}_{selected}",
    )
    col_save, col_delete = st.columns(2)
    error = None
    if col_save.button(
        "Save", key=f"admin_save_{kind.table}", type="primary", use_container_width=True
    ):
        error = apply_change(
            lambda: update_reference_value(
                data_access,
                kind.table,
                selected,
                sort_order=int(order),
                active=active,
                user=user,
                roles=roles,
            ),
            f"Saved {selected}.",
        )
    if col_delete.button(
        "Delete",
        key=f"admin_delete_{kind.table}",
        use_container_width=True,
        disabled=bool(current.in_use),
        help=(
            "Used by One Pagers: deactivate it instead"
            if current.in_use
            else f"Delete this {kind.noun}"
        ),
    ):
        error = apply_change(
            lambda: delete_reference_value(
                data_access, kind.table, selected, user, roles
            ),
            f"Deleted {selected}.",
        )
    if error:
        st.error(error, icon=ALERT_ICON)


def _add_reference_form(
    data_access: DataAccess,
    kind: ReferenceKind,
    values: list,
    user: CurrentUser,
    roles: frozenset[Actor],
) -> None:
    with st.form(f"admin_add_form_{kind.table}", clear_on_submit=False):
        name = st.text_input(kind.noun.capitalize(), key=f"admin_add_{kind.table}")
        order = st.number_input(
            "Order",
            min_value=1,
            max_value=9999,
            step=1,
            value=max((v.sort_order for v in values), default=0) + 1,
            key=f"admin_add_order_{kind.table}",
        )
        submitted = st.form_submit_button("Add", type="primary")
    if submitted:
        error = apply_change(
            lambda: add_reference_value(
                data_access, kind.table, name, int(order), user, roles
            ),
            f"Added {' '.join(name.split())}.",
        )
        if error:
            st.error(error, icon=ALERT_ICON)


def render_status_section(
    data_access: DataAccess, user: CurrentUser, roles: frozenset[Actor]
) -> None:
    """Display label, order and badge color of the OP and DP statuses."""
    st.subheader(STATUS_SECTION)
    st.caption(
        "The statuses and which of them are final are fixed by the workflow; "
        "their labels, order and badge colors can be changed here."
    )
    table = st.radio(
        "Status type",
        options=list(STATUS_KINDS),
        format_func=lambda t: STATUS_KINDS[t][0],
        horizontal=True,
        key="admin_status_table",
    )
    try:
        rows = get_status_definitions(data_access, table, user, roles)
    except Exception as e:
        logger.exception(f"Failed to load {table}")
        render_load_error(f"admin_retry_{table}", e)
        return

    for row in rows:
        final = " — final" if row.is_terminal else ""
        st.markdown(
            f"{status_badge(row.display_label, row.badge_color or DEFAULT_BADGE_COLOR)}"
            f" <code>{row.status}</code> · order {row.sort_order}{final}",
            unsafe_allow_html=True,
        )

    by_status = {r.status: r for r in rows}
    if not by_status:
        return
    with st.container(border=True):
        st.markdown("**Change a status**")
        status = st.selectbox(
            "Status", options=list(by_status), key=f"admin_status_{table}"
        )
        current = by_status[status]
        label = st.text_input(
            "Display label",
            value=current.display_label,
            max_chars=50,
            key=f"admin_status_label_{table}_{status}",
        )
        order = st.number_input(
            "Order",
            min_value=1,
            max_value=9999,
            step=1,
            value=max(current.sort_order, 1),
            key=f"admin_status_order_{table}_{status}",
        )
        color = st.color_picker(
            "Badge color",
            value=current.badge_color or DEFAULT_BADGE_COLOR,
            key=f"admin_status_color_{table}_{status}",
        )
        if st.button("Save", key=f"admin_status_save_{table}", type="primary"):
            error = apply_change(
                lambda: update_status_definition(
                    data_access,
                    table,
                    status,
                    display_label=label,
                    sort_order=int(order),
                    badge_color=color,
                    user=user,
                    roles=roles,
                ),
                f"Saved the status {status}.",
            )
            if error:
                st.error(error, icon=ALERT_ICON)


def render_pending_prs_section(
    data_access: DataAccess, user: CurrentUser, roles: frozenset[Actor]
) -> None:
    """Approved One Pagers whose Git PR is still missing, with Retry PR."""
    st.subheader(PENDING_PRS_SECTION)
    st.caption(
        "Approved One Pagers whose pull request to the One Pager registry "
        "could not be created. The approval itself is complete."
    )
    try:
        rows = get_pending_prs(data_access, user, roles)
    except Exception as e:
        logger.exception("Failed to load the pending PRs")
        render_load_error("admin_retry_pending_prs", e)
        return
    st.info(RETRY_PR_UNAVAILABLE, icon=":material/info:")
    if not rows:
        st.success("No pending PRs: every approved One Pager has its PR.")
        return

    header = st.columns(_PENDING_PR_RATIOS)
    for column, title in zip(header, PENDING_PR_COLUMNS, strict=True):
        column.markdown(f"**{title}**")
    for row in rows:
        cells = st.columns(_PENDING_PR_RATIOS)
        values = [
            row.one_pager_id,
            row.product_name,
            row.version,
            timestamp_label(row.reviewed_at),
            row.reviewed_by or "-",
        ]
        for column, value in zip(cells[:-1], values, strict=True):
            column.markdown(value)
        cells[-1].button(
            "Retry PR",
            key=f"admin_retry_pr_{row.one_pager_id}",
            disabled=True,
            help=RETRY_PR_UNAVAILABLE,
            use_container_width=True,
        )


# ============================================================================
# Admin Page
# ============================================================================

data_access = require_data_access()
user = signed_in_user()
roles = current_roles()

st.title("Administration")
try:
    check_can_administer(user, roles)
except PermissionDeniedError as e:
    st.info(str(e))
    st.stop()

show_flash(FLASH_KEY)

# A horizontal section menu: the sections use columns themselves, and
# Streamlit allows only one level of nested columns.
section = st.radio("Section", options=SECTIONS, horizontal=True, key="admin_section")
st.divider()
if section == STATUS_SECTION:
    render_status_section(data_access, user, roles)
elif section == PENDING_PRS_SECTION:
    render_pending_prs_section(data_access, user, roles)
else:
    kind = next(k for k in REFERENCE_KINDS if k.title == section)
    render_reference_section(data_access, kind, user, roles)
