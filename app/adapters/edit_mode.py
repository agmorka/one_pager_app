"""Editor page, edit mode — edit an existing One Pager (UI_Design.md §4.2).

The editor works on a copy of the stored document kept in session state
(``edit_document``). Each tab renders its widgets from that copy and writes the
widget values back into it on every run, so switching tabs never loses input.
Only one tab is rendered at a time (a radio "tab bar"), which lets other parts
of the page switch tabs programmatically.

The edit lock is acquired when the editor opens and re-acquired on re-runs,
which is the lock heartbeat (Backend_Design.md §6). The heartbeat is written
at most every ``ttl / HEARTBEAT_TTL_FRACTION``; Save and Submit check the lock
fresh either way.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from adapters.cache import writes_data
from adapters.edit_tabs import (
    bound,
    render_classification_tab,
    render_data_product_tab,
    render_data_sources_tab,
    render_governance_tab,
    render_requirements_tab,
    render_scope_tab,
    render_use_cases_tab,
)
from adapters.navigation import (
    EDITOR_FLASH_KEY,
    EDITOR_ID_KEY,
    EDITOR_MODE_KEY,
    open_in_editor,
    open_preview,
)
from adapters.page import (
    ALERT_ICON,
    current_user,
    page_header,
    render_error_state,
    show_flash,
    timestamp_label,
)
from adapters.session import current_session_id
from adapters.workflow_actions import resolve_comment_and_report
from onepagerapp.compare import suggested_summary
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import (
    MAX_SUMMARY_LENGTH,
    SAVE_FAILED_MESSAGE,
    SaveError,
    has_unsaved_changes,
    open_for_edit,
    save_draft,
    submission_issues,
    working_copy,
)
from onepagerapp.locking import (
    DEFAULT_LOCK_TTL,
    acquire_lock,
    heartbeat_due,
    release_lock,
    utc_now,
)
from onepagerapp.models import (
    CurrentUser,
    LockInfo,
    OnePagerDocument,
    ValidationError,
)
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import section_label
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.timeutils import as_utc
from onepagerapp.validation import MAX_NAME_LENGTH, MAX_TEXT_LENGTH
from onepagerapp.workflow import (
    TRANSITION_FAILED_MESSAGE,
    TransitionError,
    active_reference_values,
    submit_for_review,
)

logger = logging.getLogger(__name__)

PREFIX = "edit_"
ONE_PAGER_KEY = "edit_one_pager_id"
STATUS_ROW_KEY = "edit_status_row"
SAVED_KEY = "edit_saved_document"
DOCUMENT_KEY = "edit_document"
ACTIVE_TAB_KEY = "edit_active_tab"
BANNER_KEY = "edit_banner"
FLASH_KEY = "edit_flash"
ERRORS_KEY = "edit_errors"
SUMMARY_KEY = "edit_change_summary"
CLEAR_SUMMARY_KEY = "edit_clear_summary"

SME_COLUMNS = ["name", "initials", "email", "team"]

LOAD_ERROR_MESSAGE = "Couldn't open this One Pager for editing. Please retry."


# ============================================================================
# Session state helpers
# ============================================================================


def clear_edit_state() -> None:
    """Forget the working copy and every edit-mode widget value."""
    for key in [k for k in st.session_state if str(k).startswith(PREFIX)]:
        del st.session_state[key]


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
        doc.business_domain = (
            st.selectbox(
                "Business Domain *",
                options=_with_current(
                    _get_domain_options(data_access), doc.business_domain
                ),
                key=bound("edit_business_domain", doc.business_domain or None),
                placeholder="Select…",
            )
            or ""
        )
    with col_type:
        doc.data_product_type = (
            st.selectbox(
                "Product Type *",
                options=_with_current(
                    _get_type_options(data_access), doc.data_product_type
                ),
                key=bound("edit_data_product_type", doc.data_product_type or None),
                placeholder="Select…",
            )
            or ""
        )
    doc.description = st.text_area(
        "Description *",
        key=bound("edit_description", doc.description),
        max_chars=MAX_TEXT_LENGTH,
    )

    st.subheader("Data Product Owner")
    me = current_user()
    if me is not None and doc.owner_initials != me.initials:
        st.button(
            ":material/person: Use my details",
            key="edit_owner_me",
            on_click=_fill_owner_with_me,
            args=(me,),
            help="Make yourself the Owner (name, initials and email).",
        )
    col_name, col_initials = st.columns([3, 1])
    with col_name:
        doc.owner_name = st.text_input(
            "Name *",
            key=bound("edit_owner_name", doc.owner_name),
            max_chars=MAX_NAME_LENGTH,
        )
    with col_initials:
        doc.owner_initials = st.text_input(
            "Initials *",
            key=bound("edit_owner_initials", doc.owner_initials),
            max_chars=5,
            help="Corporate initials, e.g. X0W (3 letters or digits) — used to "
            "grant edit access.",
        )
    col_email, col_team = st.columns(2)
    with col_email:
        doc.owner_email = st.text_input(
            "Email *", key=bound("edit_owner_email", doc.owner_email), max_chars=254
        )
    with col_team:
        doc.owner_team = (
            st.text_input(
                "Team",
                key=bound("edit_owner_team", doc.owner_team or ""),
                max_chars=MAX_NAME_LENGTH,
            )
            or None
        )

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
    known = {p.get("initials") for p in doc.smes} | {doc.owner_initials}
    if (
        me is not None
        and me.initials not in known
        and st.button(":material/person_add: Add me as SME", key="edit_sme_me")
    ):
        doc.smes.append(person_of(me))
        # Rebuild the grid from the working copy, which holds every edit.
        st.session_state.pop("edit_smes_grid", None)
        st.rerun()


def person_of(user: CurrentUser) -> dict[str, str]:
    """Return the user as an SME entry (name, initials, email if known)."""
    person = {"name": user.display_name, "initials": user.initials}
    if user.email:
        person["email"] = user.email
    return person


def _fill_owner_with_me(user: CurrentUser) -> None:
    """**Use my details**: the Owner fields become the signed-in user's."""
    st.session_state["edit_owner_name"] = user.display_name
    st.session_state["edit_owner_initials"] = user.initials
    if user.email:
        st.session_state["edit_owner_email"] = user.email


def render_problem_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Business Problem tab (the problem statement)."""
    doc.business_problem_statement = st.text_area(
        "Business Problem Statement",
        key=bound("edit_problem", doc.business_problem_statement),
        max_chars=MAX_TEXT_LENGTH,
        height=250,
        placeholder="Current state, desired state and impact of inaction.",
    )


def _resolve_in_editor(
    data_access: DataAccess, one_pager_id: str, comment_id: int, user: CurrentUser
) -> None:
    """Handle **Mark resolved** in the Review tab."""
    error = resolve_comment_and_report(data_access, one_pager_id, comment_id, user)
    st.session_state[BANNER_KEY] = error
    if not error:
        st.session_state[FLASH_KEY] = "The comment was marked as resolved."


def _render_checklist(doc: OnePagerDocument) -> int:
    """Render the validation checklist per tab (strict tier); return the count."""
    issues = issues_by_tab(submission_issues(doc, st.session_state[STATUS_ROW_KEY]))
    st.markdown("**Validation checklist**")
    for tab in [t for t in TABS if t != REVIEW_TAB]:
        count = len(issues.get(tab, []))
        if count:
            st.button(
                f"{tab}: {count} issue(s) — open the tab",
                key=f"edit_review_check_{tab}",
                on_click=_go_to_tab,
                args=(tab,),
            )
        else:
            st.markdown(f"{tab}: no issues")
    for error in issues.get("Form", []):
        st.markdown(error.message)
    return sum(len(v) for v in issues.values())


def _render_open_comments(data_access: DataAccess, one_pager_id: str) -> None:
    """Review comments from earlier cycles, with **Mark resolved** (§13)."""
    st.markdown("**Review comments**")
    try:
        comments = data_access.get_review_comments(one_pager_id)
    except Exception:
        logger.exception(f"Failed to load the review comments of {one_pager_id}")
        st.warning("Review comments are unavailable right now.", icon=ALERT_ICON)
        return
    if not comments:
        st.caption("No review comments.")
        return
    user = current_user()
    unresolved = [c for c in comments if not c.resolved]
    resolved = [c for c in comments if c.resolved]
    if not unresolved:
        st.caption("Every review comment is resolved.")
    for comment in unresolved:
        with st.container(border=True):
            st.markdown(
                f"**{section_label(comment.section)}** · {comment.reviewer_name} "
                f"({comment.reviewer_initials}) · v{comment.version}"
            )
            st.write(comment.comment)
            if user is not None:
                st.button(
                    "Mark resolved",
                    key=f"edit_resolve_{comment.id}",
                    on_click=_resolve_in_editor,
                    args=(data_access, one_pager_id, comment.id, user),
                )
    if resolved:
        with st.expander(f"Resolved comments ({len(resolved)})"):
            for comment in resolved:
                st.markdown(
                    f"**{section_label(comment.section)}** · "
                    f"{comment.reviewer_name}: {comment.comment} "
                    f"(resolved by {comment.resolved_by})"
                )


def render_review_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:
    """Review tab (UI_Design.md §4.2): checklist, review comments, Submit.

    Read-only summary. Its **Submit for Review** is the same action as the
    bottom bar's; it is enabled only when strict validation passes and there
    are no unsaved changes.
    """
    one_pager_id = st.session_state[ONE_PAGER_KEY]
    issue_count = _render_checklist(doc)
    st.divider()
    _render_open_comments(data_access, one_pager_id)
    st.divider()
    dirty = is_dirty()
    label, can_submit, reason = submit_state(dirty, issue_count)
    user = current_user()
    if st.button(
        label,
        key="edit_review_submit",
        type="primary",
        disabled=not can_submit or user is None,
        help=reason,
    ):
        if dirty:
            _save_and_submit(
                data_access, st.session_state.document_store, one_pager_id, user
            )
        else:
            _submit(data_access, one_pager_id, user)
        st.rerun()
    st.caption(reason)


TabRenderer = Callable[[OnePagerDocument, DataAccess], None]

REVIEW_TAB = "Review"

# Tab label → renderer, in the order of UI_Design.md §4.2.
TABS: dict[str, TabRenderer] = {
    "Basics": render_basics_tab,
    "Business Problem": render_problem_tab,
    "Use Cases": render_use_cases_tab,
    "Business Requirements": render_requirements_tab,
    "Data Sources": render_data_sources_tab,
    "Data Product Preview": render_data_product_tab,
    "Classification": render_classification_tab,
    "Governance": render_governance_tab,
    "Scope & Questions": render_scope_tab,
    REVIEW_TAB: render_review_tab,
}


# ============================================================================
# Page
# ============================================================================


def _lock_expiry(lock: LockInfo | None) -> str:
    if lock is None:
        return ""
    return f" until {timestamp_label(lock.expires_at, '%H:%M')}"


# The lock warning shows when this little time is left (the heartbeat renews
# the lock on activity, so it only appears after a long idle time).
LOCK_WARNING_BEFORE = timedelta(minutes=5)


def lock_minutes_left(lock: LockInfo | None, now: datetime | None = None) -> int | None:
    """Whole minutes until the lock expires (0 when expired); None without one."""
    if lock is None:
        return None
    left = as_utc(lock.expires_at) - (now or utc_now())
    return max(0, int(left.total_seconds() // 60))


def _keep_editing() -> None:
    """**Keep editing**: renew the lock now."""
    one_pager_id = st.session_state.get(ONE_PAGER_KEY)
    user = current_user()
    if one_pager_id is None or user is None:
        return
    try:
        result = acquire_lock(
            st.session_state.data_access,
            one_pager_id,
            user,
            current_session_id(),
            ttl=_lock_ttl(),
        )
    except Exception:
        logger.exception(f"Failed to renew the lock on {one_pager_id}")
        st.session_state[BANNER_KEY] = "Couldn't renew your edit lock. Please retry."
        return
    if result.acquired:
        st.session_state["edit_lock"] = result.lock
    else:
        st.session_state[BANNER_KEY] = (
            f"You no longer hold the edit lock. {result.message}"
        )


@st.fragment(run_every=60)
def render_lock_status() -> None:
    """Lock line, refreshed every minute; warns before the lock expires.

    The lock is renewed by activity (the heartbeat), so the warning appears
    only after a long idle time; **Keep editing** renews it at once.
    """
    lock = st.session_state.get("edit_lock")
    left = lock_minutes_left(lock)
    if left is None:
        return
    if left <= 0:
        st.error(
            "Your edit lock has expired. Save now to check whether you can "
            "still save, or copy your changes.",
            icon=":material/lock_open:",
        )
    elif timedelta(minutes=left) <= LOCK_WARNING_BEFORE:
        col_text, col_button = st.columns([4, 1], vertical_alignment="center")
        col_text.warning(
            f"Your edit lock expires in {left} minute(s){_lock_expiry(lock)}. "
            "Keep editing, or save your changes.",
            icon=":material/timer:",
        )
        col_button.button(
            "Keep editing",
            key="edit_keep_lock",
            on_click=_keep_editing,
            type="primary",
            use_container_width=True,
        )
    else:
        st.caption(f":material/lock: Locked by you{_lock_expiry(lock)}")


def _leave_editor(one_pager_id: str, flash: str | None = None) -> None:
    """Clear the editor's state and switch to Preview of the One Pager."""
    clear_edit_state()
    st.session_state.pop(EDITOR_MODE_KEY, None)
    st.session_state.pop(EDITOR_ID_KEY, None)
    open_preview(one_pager_id, flash)


def close_editor(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Leave the editor: release the lock and return to Preview."""
    try:
        release_lock(data_access, one_pager_id, user)
    except Exception:
        # The lock expires on its own; leaving the editor must not fail.
        logger.exception(f"Failed to release the lock on {one_pager_id}")
    _leave_editor(one_pager_id)


def is_dirty() -> bool:
    """Whether the editor's working copy has unsaved changes."""
    saved = st.session_state.get(SAVED_KEY)
    working = st.session_state.get(DOCUMENT_KEY)
    if saved is None or working is None:
        return False
    return has_unsaved_changes(saved, working)


@st.dialog("Leave without saving?")
def _confirm_close(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    st.write("You have unsaved changes. They will be lost.")
    col_leave, col_stay = st.columns(2)
    if col_leave.button(
        "Leave without saving", type="primary", use_container_width=True
    ):
        close_editor(data_access, one_pager_id, user)
    if col_stay.button("Keep editing", use_container_width=True):
        st.rerun()


# What the navigation guard does when the user left the editor.
GUARD_NONE = "none"
GUARD_RELEASE = "release"
GUARD_CONFIRM = "confirm"


def guard_action(
    *,
    on_editor_page: bool,
    editor_mode: str | None,
    editor_one_pager_id: str | None,
    edit_one_pager_id: str | None,
    dirty: bool,
) -> str:
    """Decide what to do about an open edit session on this page run.

    Streamlit cannot stop sidebar navigation, so the guard runs on the page
    the user navigated to: an edit session with unsaved changes asks the user
    to return or discard; a clean one is closed and its lock released.
    """
    if edit_one_pager_id is None:
        return GUARD_NONE
    still_editing = (
        on_editor_page
        and editor_mode == "edit"
        and editor_one_pager_id == edit_one_pager_id
    )
    if still_editing:
        return GUARD_NONE
    return GUARD_CONFIRM if dirty else GUARD_RELEASE


def _release_quietly(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    try:
        release_lock(data_access, one_pager_id, user)
    except Exception:
        logger.exception(f"Failed to release the lock on {one_pager_id}")
    clear_edit_state()


@st.dialog("You have unsaved changes")
def _confirm_leave(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    st.write(
        f"You left the Editor with unsaved changes to **{one_pager_id}**. "
        "Leave without saving?"
    )
    col_back, col_discard = st.columns(2)
    if col_back.button(
        "Return to the Editor", type="primary", use_container_width=True
    ):
        open_in_editor(one_pager_id)
    if col_discard.button("Discard changes", use_container_width=True):
        _release_quietly(data_access, one_pager_id, user)
        st.rerun()


def navigation_guard(
    page_title: str, data_access: DataAccess, user: CurrentUser | None
) -> None:
    """Unsaved-changes guard for sidebar navigation (UI_Design.md §4.2).

    Called by the app shell before every page runs.
    """
    edit_id = st.session_state.get(ONE_PAGER_KEY)
    action = guard_action(
        on_editor_page=page_title == "Editor",
        editor_mode=st.session_state.get(EDITOR_MODE_KEY),
        editor_one_pager_id=st.session_state.get(EDITOR_ID_KEY),
        edit_one_pager_id=edit_id,
        dirty=is_dirty(),
    )
    if action == GUARD_NONE or user is None:
        if edit_id is not None:
            st.session_state.pop("guard_prompted", None)
        return
    if action == GUARD_RELEASE:
        _release_quietly(data_access, edit_id, user)
        return
    with st.sidebar:
        st.warning(
            f"Unsaved changes in the Editor ({edit_id}).", icon=":material/edit:"
        )
        if st.button("Return to the Editor", key="guard_return"):
            open_in_editor(edit_id)
    if st.session_state.get("guard_prompted") != edit_id:
        st.session_state["guard_prompted"] = edit_id
        _confirm_leave(data_access, edit_id, user)


def _stop_with_preview_link(one_pager_id: str) -> None:
    if st.button("Open in Preview", key="edit_open_preview"):
        _leave_editor(one_pager_id)
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
    except Exception as e:  # DocumentMissingError or storage failure
        logger.exception(f"Failed to open {one_pager_id} for editing")
        render_error_state(
            user_error_message(e, LOAD_ERROR_MESSAGE), key="edit_retry_open"
        )

    if not session.lock.acquired:
        st.warning(session.lock.message, icon=":material/lock:")
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
    """Re-acquire the lock when the heartbeat is due; stop editing if it was lost."""
    if not heartbeat_due(
        st.session_state.get("edit_lock"),
        one_pager_id,
        current_session_id(),
        ttl=_lock_ttl(),
    ):
        return
    try:
        result = acquire_lock(
            data_access, one_pager_id, user, current_session_id(), ttl=_lock_ttl()
        )
    except Exception:
        logger.exception(f"Lock heartbeat failed for {one_pager_id}")
        st.error(
            "Couldn't confirm your edit lock. Your changes are preserved — "
            "please retry.",
            icon=ALERT_ICON,
        )
        if st.button("Retry", key="edit_retry_lock"):
            st.rerun()
        st.stop()
    if not result.acquired:
        st.warning(
            f"You no longer hold the edit lock. {result.message} "
            "Your unsaved changes cannot be saved.",
            icon=":material/lock:",
        )
        _stop_with_preview_link(one_pager_id)
    st.session_state["edit_lock"] = result.lock


def render_header(doc: OnePagerDocument) -> None:
    row = st.session_state[STATUS_ROW_KEY]
    page_header(
        f"Editing: {doc.product_name or row.product_name} ({row.one_pager_id})",
        "Update the sections, then save a new draft version or submit for review.",
    )
    st.markdown(
        f"**Status:** ● {row.one_pager_status} &nbsp;·&nbsp; "
        f"**Data Product status:** ● {row.data_product_status} &nbsp;·&nbsp; "
        f"**Version:** v{row.version}"
    )
    render_lock_status()


# Top-level document key → editor tab (UI_Design.md §4.2).
SECTION_TABS: dict[str, str] = {
    "structureDefinition": "Basics",
    "dataProduct": "Basics",
    "productName": "Basics",
    "businessDomain": "Basics",
    "dataProductType": "Basics",
    "description": "Basics",
    "onePagerStatus": "Basics",
    "dataProductStatus": "Basics",
    "version": "Basics",
    "dataProductOwner": "Basics",
    "smes": "Basics",
    "businessProblemStatement": "Business Problem",
    "useCases": "Use Cases",
    "businessRequirements": "Business Requirements",
    "dataSources": "Data Sources",
    "dataProductPreview": "Data Product Preview",
    "dataElementPreview": "Data Product Preview",
    "dataClassification": "Classification",
    "retentionRequirements": "Classification",
    "dataGovernanceArtifacts": "Governance",
    "outOfScope": "Scope & Questions",
    "openQuestions": "Scope & Questions",
    "assumptions": "Scope & Questions",
}


def tab_for_path(field_path: str) -> str | None:
    """Return the editor tab holding a field (``smes[1].email`` → Basics)."""
    section = re.split(r"[.\[]", field_path, maxsplit=1)[0]
    return SECTION_TABS.get(section)


def describe_path(field_path: str) -> str:
    """Return a readable field location, e.g. "item 1", "name" joined by a chevron."""
    parts = re.split(r"\.", field_path)[1:] if "." in field_path else []
    first = re.search(r"\[(\d+)\]", field_path.split(".", 1)[0])
    labels = [f"item {int(first.group(1)) + 1}"] if first else []
    for part in parts:
        match = re.match(r"^([^\[]+)(?:\[(\d+)\])?$", part)
        if match:
            labels.append(match.group(1))
            if match.group(2) is not None:
                labels.append(f"item {int(match.group(2)) + 1}")
    return " \u203a ".join(labels)  # single right-pointing angle quotation mark


def issues_by_tab(errors: list[ValidationError]) -> dict[str, list[ValidationError]]:
    """Group validation errors by editor tab, in tab order; unknown → "Form"."""
    grouped: dict[str, list[ValidationError]] = {}
    for error in errors:
        grouped.setdefault(tab_for_path(error.field_path) or "Form", []).append(error)
    order = [*TABS, "Form"]
    return {tab: grouped[tab] for tab in order if tab in grouped}


def tab_label(name: str, issue_count: int) -> str:
    """Tab label with its issue count, e.g. "Data Sources (2)"."""
    return f"{name} ({issue_count})" if issue_count else name


# The selected tab. The section list's own state (ACTIVE_TAB_KEY) is reset
# by Streamlit whenever its labels (issue counts) change, so the selection is
# kept here and copied into the widget on every run.
SELECTED_TAB_KEY = "edit_selected_tab"


def _go_to_tab(tab: str) -> None:
    st.session_state[SELECTED_TAB_KEY] = tab
    st.session_state[ACTIVE_TAB_KEY] = tab


def _tab_chosen() -> None:
    st.session_state[SELECTED_TAB_KEY] = st.session_state[ACTIVE_TAB_KEY]


def active_tab() -> str:
    if st.session_state.get(SELECTED_TAB_KEY) not in TABS:
        st.session_state[SELECTED_TAB_KEY] = next(iter(TABS))
    return str(st.session_state[SELECTED_TAB_KEY])


NAV_COUNTS_KEY = "edit_nav_counts"


def nav_label(tab: str, counts: dict[str, int]) -> str:
    """Section list entry: "Data Sources · 2 to fix", or a tick when complete."""
    if tab == REVIEW_TAB:
        total = sum(counts.values())
        return f"{REVIEW_TAB} & submit" + (f" · {total} left" if total else " ✓")
    count = counts.get(tab, 0)
    return f"{tab} · {count} to fix" if count else f"{tab} ✓"


def issue_counts(issues: dict[str, list[ValidationError]]) -> dict[str, int]:
    """Count the issues per editor tab ("Form" issues count for Review)."""
    return {tab: len(errors) for tab, errors in issues.items()}


def render_tab_bar() -> str:
    """Vertical section list with issue counts; returns the active tab.

    The counts are in ``format_func`` labels: changing the options would reset
    the selection. They are those of the previous run; ``render_edit_mode``
    re-runs once when the input of this run changed them.
    """
    st.session_state[ACTIVE_TAB_KEY] = active_tab()
    counts = st.session_state.get(NAV_COUNTS_KEY, {})
    return str(
        st.radio(
            "Sections",
            options=list(TABS),
            key=ACTIVE_TAB_KEY,
            on_change=_tab_chosen,
            format_func=lambda tab: nav_label(tab, counts),
            help='✓ complete · "to fix": issues to fix before Submit for Review',
        )
    )


def render_issue_summary(
    title: str, issues: dict[str, list[ValidationError]], key: str
) -> None:
    """Render the validation summary; each issue is a button that opens its tab."""
    count = sum(len(v) for v in issues.values())
    if not count:
        return
    with st.expander(f"{count} {title}", expanded=key == "save"):
        for tab, errors in issues.items():
            st.markdown(f"**{tab}**")
            for i, error in enumerate(errors):
                where = describe_path(error.field_path)
                text = f"{where}: {error.message}" if where else error.message
                if tab in TABS:
                    st.button(
                        text,
                        key=f"edit_issue_{key}_{tab}_{i}",
                        on_click=_go_to_tab,
                        args=(tab,),
                    )
                else:
                    st.write(text)


def _save(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
) -> None:
    """Run **Save Draft** and record the outcome for the next run."""
    st.session_state[BANNER_KEY] = None
    try:
        with st.spinner("Saving..."), writes_data():
            result = save_draft(
                data_access,
                document_store,
                one_pager_id,
                st.session_state[DOCUMENT_KEY],
                st.session_state.get(SUMMARY_KEY, ""),
                user,
                current_session_id(),
                allowed_domains=_get_domain_options(data_access),
                allowed_types=_get_type_options(data_access),
            )
    except (PermissionDeniedError, SaveError) as e:
        st.session_state[BANNER_KEY] = str(e)
        return
    except Exception:
        logger.exception(f"Unexpected error saving {one_pager_id}")
        st.session_state[BANNER_KEY] = SAVE_FAILED_MESSAGE
        return

    if not result.ok:
        st.session_state[ERRORS_KEY] = result.errors
        return
    st.session_state[ERRORS_KEY] = []
    st.session_state[STATUS_ROW_KEY] = result.status_row
    st.session_state[SAVED_KEY] = result.document
    st.session_state[DOCUMENT_KEY] = working_copy(result.document)
    st.session_state[CLEAR_SUMMARY_KEY] = True
    st.session_state[FLASH_KEY] = f"Saved as v{result.version}."


def _submit(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Run **Submit for Review**; on success leave the editor for Preview."""
    st.session_state[BANNER_KEY] = None
    try:
        with st.spinner("Submitting for review..."), writes_data():
            result = submit_for_review(
                data_access, one_pager_id, user, current_session_id()
            )
    except (PermissionDeniedError, TransitionError, InvalidTransitionError) as e:
        st.session_state[BANNER_KEY] = str(e)
        return
    except Exception:
        logger.exception(f"Unexpected error submitting {one_pager_id}")
        st.session_state[BANNER_KEY] = TRANSITION_FAILED_MESSAGE
        return
    if not result.ok:
        st.session_state[BANNER_KEY] = (
            f"Submit for Review is blocked by {len(result.errors)} issue(s). "
            "Fix them (see the summary below), save, and submit again."
        )
        return
    _leave_editor(
        one_pager_id, f"{one_pager_id} was submitted for review and is now In Review."
    )


SUMMARY_AUTO_KEY = "edit_summary_auto"


def _suggest_summary() -> None:
    """Keep the change summary filled with a suggestion until the user types.

    The suggestion names the changed sections; a summary the user wrote is
    never replaced.
    """
    current = st.session_state.get(SUMMARY_KEY, "")
    previous = st.session_state.get(SUMMARY_AUTO_KEY, "")
    if current and current != previous:
        return  # the user's own words
    saved = st.session_state.get(SAVED_KEY)
    working = st.session_state.get(DOCUMENT_KEY)
    suggestion = (
        suggested_summary(saved, working) if saved and working and is_dirty() else ""
    )
    st.session_state[SUMMARY_KEY] = suggestion
    st.session_state[SUMMARY_AUTO_KEY] = suggestion


def _save_and_submit(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
) -> None:
    """**Save & submit for review**: save the changes, then submit."""
    _save(data_access, document_store, one_pager_id, user)
    if st.session_state.get(BANNER_KEY) or st.session_state.get(ERRORS_KEY):
        st.session_state.pop(FLASH_KEY, None)
        return
    _submit(data_access, one_pager_id, user)


def submit_state(dirty: bool, issue_count: int) -> tuple[str, bool, str]:  # noqa: FBT001
    """Label, enabled and reason of the bottom bar's submit button."""
    label = "Save & submit for review" if dirty else "Submit for Review"
    if issue_count:
        return (
            label,
            False,
            f"Fix the {issue_count} issue(s) marked in the section list first.",
        )
    if dirty:
        return label, True, "Saves your changes as a new version, then submits."
    return label, True, "Sends the One Pager to review. Releases your lock."


def render_bottom_bar(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
    submit_issue_count: int = 0,
) -> None:
    """Change summary, **Save Draft**, submit and **Close editor** (UI §4.2)."""
    st.divider()
    if st.session_state.pop(CLEAR_SUMMARY_KEY, False):
        st.session_state[SUMMARY_KEY] = ""
        st.session_state[SUMMARY_AUTO_KEY] = ""
    _suggest_summary()
    dirty = is_dirty()
    st.text_input(
        "Change summary *",
        key=SUMMARY_KEY,
        max_chars=MAX_SUMMARY_LENGTH,
        placeholder="What did you change?",
        help="Required to save. Suggested from the sections you changed; "
        "edit it as you like. Becomes the change log entry for this version.",
    )
    label, can_submit, reason = submit_state(dirty, submit_issue_count)
    col_save, col_submit, col_close, _ = st.columns([1, 1.6, 1, 2.4])
    with col_save:
        save_clicked = st.button(
            "Save Draft",
            key="edit_save",
            type="primary",
            disabled=not dirty,
            help="Saves your changes as a new version."
            if dirty
            else "No unsaved changes.",
            use_container_width=True,
        )
    with col_submit:
        submit_clicked = st.button(
            label,
            key="edit_submit",
            disabled=not can_submit,
            help=reason,
            use_container_width=True,
        )
    with col_close:
        close_clicked = st.button(
            "Close editor", key="edit_close", use_container_width=True
        )
    st.caption(("● Unsaved changes · " if dirty else "All changes saved · ") + reason)
    if save_clicked:
        _save(data_access, document_store, one_pager_id, user)
        st.rerun()
    if submit_clicked:
        if dirty:
            _save_and_submit(data_access, document_store, one_pager_id, user)
        else:
            _submit(data_access, one_pager_id, user)
        st.rerun()
    if close_clicked:
        if is_dirty():
            _confirm_close(data_access, one_pager_id, user)
        else:
            close_editor(data_access, one_pager_id, user)


def render_edit_mode(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    user: CurrentUser | None,
) -> None:
    """Render the Editor for the One Pager in ``editor_one_pager_id``.

    A section list on the left (with issue counts) and the active section on
    the right; save errors and the bottom bar underneath.
    """
    one_pager_id = st.session_state.get(EDITOR_ID_KEY)
    if not one_pager_id or user is None:
        page_header("Editor", "Create a new One Pager or edit an existing one.")
        st.info("Open a One Pager in Preview and choose **Edit**.")
        st.stop()

    if st.session_state.get(ONE_PAGER_KEY) != one_pager_id:
        _open(data_access, one_pager_id, user)
    else:
        _heartbeat(data_access, one_pager_id, user)

    doc: OnePagerDocument = st.session_state[DOCUMENT_KEY]
    if NAV_COUNTS_KEY not in st.session_state:
        st.session_state[NAV_COUNTS_KEY] = issue_counts(
            issues_by_tab(submission_issues(doc, st.session_state[STATUS_ROW_KEY]))
        )
    render_header(doc)

    banner = st.session_state.get(BANNER_KEY)
    if banner:
        st.error(banner, icon=ALERT_ICON)
    show_flash(FLASH_KEY)
    show_flash(EDITOR_FLASH_KEY)

    nav_col, body_col = st.columns([1, 3.4], gap="medium")
    with nav_col:
        active = render_tab_bar()
    with body_col:
        st.subheader(active if active != REVIEW_TAB else "Review & submit")
        TABS[active](doc, data_access)

    submit_issues = issues_by_tab(
        submission_issues(doc, st.session_state[STATUS_ROW_KEY])
    )
    counts = issue_counts(submit_issues)
    if counts != st.session_state.get(NAV_COUNTS_KEY):
        # The section list was drawn with the counts of the previous run.
        st.session_state[NAV_COUNTS_KEY] = counts
        st.rerun()

    save_errors = issues_by_tab(st.session_state.get(ERRORS_KEY, []))
    render_issue_summary("issue(s) must be fixed before saving", save_errors, "save")
    render_bottom_bar(
        data_access, document_store, one_pager_id, user, sum(counts.values())
    )
