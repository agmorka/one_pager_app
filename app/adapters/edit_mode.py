"""Editor page, edit mode — edit an existing One Pager (UI_Design.md §4.2).

The editor works on a copy of the stored document kept in session state
(``edit_document``). Each tab renders its widgets from that copy and writes the
widget values back into it on every run, so switching tabs never loses input.
Only one tab is rendered at a time (a radio "tab bar"), which lets other parts
of the page switch tabs programmatically.

The edit lock is acquired when the editor opens and re-acquired on every
re-run, which is the lock heartbeat (Backend_Design.md §6).
"""

import logging
from collections.abc import Callable
from datetime import UTC, timedelta

import pandas as pd
import streamlit as st

from adapters.session import current_session_id
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.editing import open_for_edit, working_copy
from onepagerapp.locking import DEFAULT_LOCK_TTL, acquire_lock, release_lock
from onepagerapp.models import CurrentUser, LockInfo, OnePagerDocument
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.validation import MAX_NAME_LENGTH, MAX_TEXT_LENGTH
from onepagerapp.workflow import active_reference_values

logger = logging.getLogger(__name__)

PREFIX = "edit_"
ONE_PAGER_KEY = "edit_one_pager_id"
STATUS_ROW_KEY = "edit_status_row"
SAVED_KEY = "edit_saved_document"
DOCUMENT_KEY = "edit_document"
ACTIVE_TAB_KEY = "edit_active_tab"
BANNER_KEY = "edit_banner"

SME_COLUMNS = ["name", "initials", "email", "team"]

LOAD_ERROR_MESSAGE = "Couldn't open this One Pager for editing. Please retry."


# ============================================================================
# Session state helpers
# ============================================================================


def clear_edit_state() -> None:
    """Forget the working copy and every edit-mode widget value."""
    for key in [k for k in st.session_state if str(k).startswith(PREFIX)]:
        del st.session_state[key]


def bound(key: str, value: object) -> str:
    """Seed a widget's session-state value from the document once; return the key.

    Widgets of tabs that are not rendered lose their state, so a tab that is
    shown again is seeded from the working copy.
    """
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _people_from_grid(df: pd.DataFrame) -> list[dict]:
    """SME grid rows as document person dicts, skipping completely empty rows."""
    people = []
    for _, row in df.iterrows():
        person = {c: _cell(row.get(c)) for c in SME_COLUMNS}
        person = {k: v for k, v in person.items() if v}
        if person:
            people.append(person)
    return people


def _with_current(options: list[str], current: str) -> list[str]:
    """Keep a stored value selectable even if it is no longer active."""
    return options if not current or current in options else [*options, current]


# ============================================================================
# Cached reference data
# ============================================================================


@st.cache_data(ttl=3600)
def _get_domain_options(_data_access) -> list[str]:  # noqa: ANN001
    return active_reference_values(_data_access.get_ref_business_domains(), "domain")


@st.cache_data(ttl=3600)
def _get_type_options(_data_access) -> list[str]:  # noqa: ANN001
    return active_reference_values(_data_access.get_ref_data_product_types(), "type")


# ============================================================================
# Tabs
# ============================================================================


def render_basics_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:
    """Render the Basics tab: names, domain, type, description, Owner, SMEs."""
    st.text_input(
        "Data Product",
        value=doc.data_product,
        disabled=True,
        help="The registered name of the Data Product cannot be changed.",
    )
    doc.product_name = st.text_input(
        "Product Name *",
        key=bound("edit_product_name", doc.product_name),
        max_chars=MAX_NAME_LENGTH,
    )
    col_domain, col_type = st.columns(2)
    with col_domain:
        doc.business_domain = st.selectbox(
            "Business Domain *",
            options=_with_current(
                _get_domain_options(data_access), doc.business_domain
            ),
            key=bound("edit_business_domain", doc.business_domain or None),
            placeholder="Select…",
        ) or ""
    with col_type:
        doc.data_product_type = st.selectbox(
            "Product Type *",
            options=_with_current(
                _get_type_options(data_access), doc.data_product_type
            ),
            key=bound("edit_data_product_type", doc.data_product_type or None),
            placeholder="Select…",
        ) or ""
    doc.description = st.text_area(
        "Description *",
        key=bound("edit_description", doc.description),
        max_chars=MAX_TEXT_LENGTH,
    )

    st.subheader("Data Product Owner")
    col_name, col_initials = st.columns([3, 1])
    with col_name:
        doc.owner_name = st.text_input(
            "Name *", key=bound("edit_owner_name", doc.owner_name),
            max_chars=MAX_NAME_LENGTH,
        )
    with col_initials:
        doc.owner_initials = st.text_input(
            "Initials *",
            key=bound("edit_owner_initials", doc.owner_initials),
            max_chars=5,
            help="Corporate initials — used to grant edit access.",
        )
    col_email, col_team = st.columns(2)
    with col_email:
        doc.owner_email = st.text_input(
            "Email *", key=bound("edit_owner_email", doc.owner_email), max_chars=254
        )
    with col_team:
        doc.owner_team = st.text_input(
            "Team", key=bound("edit_owner_team", doc.owner_team or ""),
            max_chars=MAX_NAME_LENGTH,
        ) or None

    st.subheader("Subject Matter Experts")
    st.caption("SMEs can edit this One Pager. Name, initials and email are required.")
    if "edit_smes_grid" not in st.session_state:
        # The grid's source must stay the same object while the tab is shown.
        st.session_state["edit_smes_initial"] = pd.DataFrame(
            [{c: s.get(c, "") for c in SME_COLUMNS} for s in doc.smes],
            columns=SME_COLUMNS,
        ).astype("str")
    smes_df = st.data_editor(
        st.session_state["edit_smes_initial"],
        key="edit_smes_grid",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": st.column_config.TextColumn("Name", max_chars=MAX_NAME_LENGTH),
            "initials": st.column_config.TextColumn("Initials", max_chars=5),
            "email": st.column_config.TextColumn("Email", max_chars=254),
            "team": st.column_config.TextColumn("Team", max_chars=MAX_NAME_LENGTH),
        },
    )
    doc.smes = _people_from_grid(smes_df)


def render_problem_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Business Problem tab (the problem statement)."""
    doc.business_problem_statement = st.text_area(
        "Business Problem Statement",
        key=bound("edit_problem", doc.business_problem_statement),
        max_chars=MAX_TEXT_LENGTH,
        height=250,
        placeholder="Current state, desired state and impact of inaction.",
    )


TabRenderer = Callable[[OnePagerDocument, DataAccess], None]

# Tab label → renderer, in the order of UI_Design.md §4.2.
TABS: dict[str, TabRenderer] = {
    "Basics": render_basics_tab,
    "Business Problem": render_problem_tab,
}


# ============================================================================
# Page
# ============================================================================


def _lock_expiry(lock: LockInfo | None) -> str:
    if lock is None:
        return ""
    expires = lock.expires_at
    if expires.tzinfo is not None:
        expires = expires.astimezone(UTC)
    return f" (expires {expires.strftime('%H:%M')} UTC)"


def _go_to_preview(one_pager_id: str) -> None:
    clear_edit_state()
    st.session_state.pop("editor_mode", None)
    st.session_state.pop("editor_one_pager_id", None)
    st.session_state["preview_one_pager_id"] = one_pager_id
    st.switch_page("views/preview.py")


def close_editor(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Leave the editor: release the lock and return to Preview."""
    try:
        release_lock(data_access, one_pager_id, user)
    except Exception:
        # The lock expires on its own; leaving the editor must not fail.
        logger.exception(f"Failed to release the lock on {one_pager_id}")
    _go_to_preview(one_pager_id)


def _stop_with_preview_link(one_pager_id: str) -> None:
    if st.button("Open in Preview", key="edit_open_preview"):
        _go_to_preview(one_pager_id)
    st.stop()


def _open(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """First run for this One Pager: permission check, lock, fresh document."""
    clear_edit_state()
    try:
        with st.spinner("Opening One Pager..."):
            session = open_for_edit(
                data_access,
                one_pager_id,
                user,
                current_session_id(),
                ttl=_lock_ttl(),
            )
    except NotFoundError:
        st.error(f"One Pager **{one_pager_id}** not found.")
        st.stop()
    except PermissionDeniedError as e:
        st.error(str(e))
        _stop_with_preview_link(one_pager_id)
    except Exception:  # DocumentMissingError or storage failure
        logger.exception(f"Failed to open {one_pager_id} for editing")
        st.error(LOAD_ERROR_MESSAGE, icon="⚠️")
        if st.button("Retry", key="edit_retry_open"):
            st.rerun()
        st.stop()

    if not session.lock.acquired:
        st.warning(session.lock.message, icon="🔒")
        st.info("You can view it in Preview mode.")
        _stop_with_preview_link(one_pager_id)

    st.session_state[ONE_PAGER_KEY] = one_pager_id
    st.session_state[STATUS_ROW_KEY] = session.status_row
    st.session_state[SAVED_KEY] = session.document
    st.session_state[DOCUMENT_KEY] = working_copy(session.document)
    st.session_state["edit_lock"] = session.lock.lock


def _lock_ttl() -> timedelta:
    config = st.session_state.get("config")
    return config.lock_ttl if config is not None else DEFAULT_LOCK_TTL


def _heartbeat(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Re-acquire the lock on every re-run; stop editing if it was lost."""
    try:
        result = acquire_lock(
            data_access, one_pager_id, user, current_session_id(), ttl=_lock_ttl()
        )
    except Exception:
        logger.exception(f"Lock heartbeat failed for {one_pager_id}")
        st.error(
            "Couldn't confirm your edit lock. Your changes are preserved — "
            "please retry.",
            icon="⚠️",
        )
        if st.button("Retry", key="edit_retry_lock"):
            st.rerun()
        st.stop()
    if not result.acquired:
        st.warning(
            f"You no longer hold the edit lock. {result.message} "
            "Your unsaved changes cannot be saved.",
            icon="🔒",
        )
        _stop_with_preview_link(one_pager_id)
    st.session_state["edit_lock"] = result.lock


def render_header(doc: OnePagerDocument) -> None:
    row = st.session_state[STATUS_ROW_KEY]
    st.title(f"Editing: {doc.product_name or row.product_name} ({row.one_pager_id})")
    st.markdown(
        f"**Status:** ● {row.one_pager_status} &nbsp;·&nbsp; "
        f"**Data Product status:** ● {row.data_product_status} &nbsp;·&nbsp; "
        f"**Version:** v{row.version}"
    )
    st.caption(f"🔒 Locked by you{_lock_expiry(st.session_state.get('edit_lock'))}")


def render_tab_bar(labels: dict[str, str]) -> str:
    """Horizontal tab bar; ``labels`` maps tab name → displayed label."""
    if st.session_state.get(ACTIVE_TAB_KEY) not in TABS:
        st.session_state[ACTIVE_TAB_KEY] = next(iter(TABS))
    return str(
        st.radio(
            "Section",
            options=list(TABS),
            format_func=lambda name: labels.get(name, name),
            key=ACTIVE_TAB_KEY,
            horizontal=True,
            label_visibility="collapsed",
        )
    )


def render_bottom_bar(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    st.divider()
    if st.button("Close editor", key="edit_close"):
        close_editor(data_access, one_pager_id, user)


def render_edit_mode(data_access: DataAccess, user: CurrentUser | None) -> None:
    """Render the Editor for the One Pager in ``editor_one_pager_id``."""
    one_pager_id = st.session_state.get("editor_one_pager_id")
    if not one_pager_id or user is None:
        st.title("Editor")
        st.info("Open a One Pager in Preview and choose **Edit**.")
        st.stop()

    if st.session_state.get(ONE_PAGER_KEY) != one_pager_id:
        _open(data_access, one_pager_id, user)
    else:
        _heartbeat(data_access, one_pager_id, user)

    doc: OnePagerDocument = st.session_state[DOCUMENT_KEY]
    render_header(doc)

    banner = st.session_state.get(BANNER_KEY)
    if banner:
        st.error(banner, icon="⚠️")

    active = render_tab_bar({})
    TABS[active](doc, data_access)

    render_bottom_bar(data_access, one_pager_id, user)
