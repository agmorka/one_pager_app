"""Preview page — read-only view of a single One Pager.

Displays the complete One Pager document with header, status timeline, content sections,
change log, review comments, and lock indicator. The action bar shows the actions
that apply to the user's role and the One Pager's statuses
(``permissions.get_action_states``, derived from the workflow state machine);
actions not implemented yet are disabled.

The view:
1. Resolves one_pager_id from session state or the query param; without one it
   points the user to the Registry instead of guessing an ID
2. Fetches PreviewData from DataAccess layer
3. Renders regions in order: header, timeline, content, change log, comments, lock
4. Handles all page states: Loading, Populated, Not found, Error (friendly
   banner with Retry, no internals), No selection
5. Does not cache dynamic data (document, lock, comments) — fresh on every re-run

Per Backend_Design.md §2, read permission is universal (authenticated users only).
Per UI_Design.md §4.4, actions depend on role and status.
"""

import html
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from adapters import cache
from adapters.navigation import (
    PREVIEW_FLASH_KEY,
    PREVIEW_ID_KEY,
    REVIEW_MODE_KEY,
    back_to_review_queue,
    go_to_registry,
    open_in_editor,
    open_use_case,
)
from adapters.page import (
    ALERT_ICON,
    current_roles,
    page_header,
    render_error_state,
    require_data_access,
    set_flash,
    show_flash,
    signed_in_user,
    timestamp_label,
)
from adapters.theme import DEFAULT_BADGE_COLOR, section_gap
from adapters.workflow_actions import (
    add_comment_and_report,
    approve_and_report,
    cancel_and_report,
    change_dp_status_and_report,
    export_pdf_and_report,
    reject_and_report,
    resolve_comment_and_report,
    update_and_report,
)
from onepagerapp.compare import (
    ComparisonBase,
    SectionChange,
    comparison_base,
    diff_documents,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.export import (
    BUSINESS_CONCEPT_COLUMNS,
    CDE_LINEAGE_COLUMNS,
    CDE_QUALITY_COLUMNS,
    DATA_ELEMENT_COLUMNS,
    DATA_SOURCE_COLUMNS,
    OPEN_QUESTION_COLUMNS,
    REQUIREMENT_COLUMNS,
    RETENTION_COLUMNS,
    USE_CASE_COLUMNS,
    cell_text,
    resolve_use_cases,
)
from onepagerapp.help_content import TOPIC_PREVIEW
from onepagerapp.locking import active_lock, release_lock
from onepagerapp.models import (
    ChangeLogEntry,
    CurrentUser,
    LockInfo,
    PreviewData,
    ReviewComment,
)
from onepagerapp.permissions import (
    AUTHORIZED_ROLES,
    ActionState,
    PermissionDeniedError,
    get_action_states,
    get_status_path,
)
from onepagerapp.review import MAX_COMMENT_LENGTH, SECTION_LABELS, section_label
from onepagerapp.state_machine import IN_REVIEW, Actor, TransitionRule
from onepagerapp.workflow import data_product_options, plan_approval

logger = logging.getLogger(__name__)

PREVIEW_SUBTITLE = "Read-only view of a One Pager, its change log and review comments."


LOAD_ERROR_MESSAGE = "Couldn't load this One Pager. Please retry."


# ============================================================================
# Render Components
# ============================================================================


def render_status_badge(status: str, color: str) -> None:
    """Render a colored status badge inline.

    Args:
        status: Status text to display.
        color: Hex color code for the dot.

    """
    html = f"""
    <span style="display: inline-flex; align-items: center; gap: 6px;">
        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: {color}; flex-shrink: 0;"></span>
        <span style="font-weight: 500; font-size: 0.95em;">{status}</span>
    </span>
    """  # noqa: E501 - one HTML element per line
    st.markdown(html, unsafe_allow_html=True)


_STEP_STYLE = {
    "done": ("#65B676", "normal", "✓"),
    "current": ("#0c1c49", "bold", "●"),
    "next": ("#9E9E9E", "normal", "○"),
    "cancelled": ("#F34421", "bold", "✕"),
}


def last_rejection(change_log: list[ChangeLogEntry]) -> ChangeLogEntry | None:
    """Return the latest status change if it sent the One Pager back from review."""
    for entry in change_log:  # newest first
        if entry.event_type != "status_transition" or entry.status_field not in (
            None,
            "one_pager_status",
        ):
            continue
        return (
            entry
            if entry.from_status == IN_REVIEW
            and entry.to_status
            in (
                "Draft",
                "Draft Update",
            )
            else None
        )
    return None


def render_status_timeline(
    current_status: str, version: str, change_log: list[ChangeLogEntry] = ()
) -> None:
    """Render where the One Pager is on its path (``get_status_path``).

    Done steps are ticked, the current one is bold, the next ones are gray;
    every step is text, so color is never the only indicator. A rejection
    that sent it back for rework is noted underneath.
    """
    parts = []
    for i, step in enumerate(get_status_path(current_status, version)):
        color, weight, mark = _STEP_STYLE[step.state]
        if i:
            parts.append('<span style="color:#9E9E9E;margin:0 6px;">→</span>')
        parts.append(
            f'<span style="color:{color};font-weight:{weight};">{mark} '
            f"{html.escape(step.label)}</span>"
        )
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;align-items:center;'
        f'font-size:0.95em;margin:0.25rem 0;">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )
    rejection = last_rejection(list(change_log))
    if rejection and current_status == rejection.to_status:
        st.caption(
            f"Sent back for rework by {rejection.author_name} on "
            f"{timestamp_label(rejection.created_at)}. See the review comments."
        )


def render_header(preview_data: PreviewData, op_colors: dict, dp_colors: dict) -> None:
    """Render the One Pager header with metadata and status badges.

    Args:
        preview_data: Complete preview data.
        op_colors: Map of One Pager status → color.
        dp_colors: Map of Data Product status → color.

    """
    header = preview_data.header

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        page_header(header.product_name, PREVIEW_SUBTITLE, help_topic=TOPIC_PREVIEW)
        st.markdown(f"**ID:** {header.one_pager_id} | **Version:** {header.version}")
        st.markdown(
            f"**Owner:** {header.owner_name} ({header.owner_email}) | "
            f"**Last updated:** {timestamp_label(header.last_updated_at)} "
            f"by {header.last_updated_by}"
        )

    with col2:
        st.markdown("**One Pager status**")
        color = op_colors.get(header.one_pager_status, DEFAULT_BADGE_COLOR)
        render_status_badge(header.one_pager_status, color)

    with col3:
        st.markdown("**Data Product status**")
        color = dp_colors.get(header.data_product_status, DEFAULT_BADGE_COLOR)
        render_status_badge(header.data_product_status, color)


def _table(rows: list[dict], columns: dict[str, str]) -> pd.DataFrame:
    """Build a display table from document list items.

    Args:
        rows: List items as stored in the document.
        columns: Document key → column label, in display order (the column
            definitions of ``onepagerapp.export``, shared with the PDF).

    """
    # Cells are always strings: a column mixing e.g. booleans with "" for
    # missing values cannot be serialized to Arrow by st.dataframe.
    return pd.DataFrame(
        [
            {label: cell_text(row, keys) for keys, label in columns.items()}
            for row in rows
        ],
        columns=list(columns.values()),
    )


def _show_table(rows: list[dict], columns: dict[str, str], empty: str) -> None:
    if rows:
        st.dataframe(_table(rows, columns), hide_index=True, use_container_width=True)
    else:
        st.write(f"*{empty}*")


def _show_list(items: list[str], empty: str) -> None:
    if items:
        st.markdown("\n".join(f"- {item}" for item in items))
    else:
        st.write(f"*{empty}*")


@dataclass
class CommentTarget:
    """What an Approver needs to comment on a section from the content view."""

    data_access: DataAccess
    one_pager_id: str
    user: CurrentUser
    roles: frozenset[Actor]


EXPAND_ALL_KEY = "preview_expand_all"


@contextmanager
def section_block(
    title: str,
    section: str,
    *,
    empty: bool,
    comment: CommentTarget | None,
    expanded: bool = False,
) -> Iterator[None]:
    """Render the expander of one content section, with "Comment on this section"."""
    label = f"{title} — empty" if empty else title
    with st.expander(label, expanded=expanded or st.session_state.get(EXPAND_ALL_KEY)):
        yield
        if comment is not None and st.button(
            ":material/add_comment: Comment on this section",
            key=f"preview_comment_on_{section}",
        ):
            add_comment_dialog(
                comment.data_access,
                comment.one_pager_id,
                comment.user,
                comment.roles,
                section=section,
            )


def _render_use_case_links(use_case_rows: list[dict]) -> None:
    """Render a button per linked Use Case that opens it on the Use Cases page."""
    ids = [str(row.get("useCaseId")) for row in use_case_rows if row.get("useCaseId")]
    if not ids:
        return
    st.caption("Open a Use Case:")
    for column, use_case_id in zip(st.columns(min(len(ids), 6)), ids, strict=False):
        if column.button(use_case_id, key=f"preview_open_uc_{use_case_id}"):
            open_use_case(use_case_id)


def render_content_sections(
    preview_data: PreviewData,
    use_case_rows: list[dict],
    comment: CommentTarget | None = None,
) -> None:
    """Render the One Pager document content as collapsible sections.

    Sections follow the editor-tab order (UI_Design.md §4.4). Empty sections
    say so in their title; **Expand all** opens every section. With a
    ``comment`` target (an Approver in review), each section has
    **Comment on this section**.

    Args:
        preview_data: Complete preview data.
        use_case_rows: Use Case rows from ``resolve_use_cases``.
        comment: Where section comments go, or None when commenting is off.

    """
    doc = preview_data.document
    dc = doc.data_classification
    governance = doc.data_governance_artifacts

    st.toggle("Expand all sections", key=EXPAND_ALL_KEY)

    with section_block(
        "Description",
        "description",
        empty=not doc.description,
        comment=comment,
        expanded=True,
    ):
        st.write(doc.description or "*No description provided.*")

    with section_block(
        "Business Problem Statement",
        "businessProblemStatement",
        empty=not doc.business_problem_statement,
        comment=comment,
    ):
        st.write(
            doc.business_problem_statement
            or "*No business problem statement provided.*"
        )

    with section_block(
        "Use Cases", "useCases", empty=not use_case_rows, comment=comment
    ):
        _show_table(use_case_rows, USE_CASE_COLUMNS, "No use cases provided.")
        _render_use_case_links(use_case_rows)

    with section_block(
        "Business Requirements",
        "businessRequirements",
        empty=not doc.business_requirements,
        comment=comment,
    ):
        _show_table(
            doc.business_requirements,
            REQUIREMENT_COLUMNS,
            "No business requirements provided.",
        )

    with section_block(
        "Data Sources", "dataSources", empty=not doc.data_sources, comment=comment
    ):
        _show_table(doc.data_sources, DATA_SOURCE_COLUMNS, "No data sources provided.")

    with section_block(
        "Data Product Preview",
        "dataProductPreview",
        empty=not doc.data_product_preview,
        comment=comment,
    ):
        _show_table(
            doc.data_product_preview,
            DATA_ELEMENT_COLUMNS,
            "No data product preview provided.",
        )

    with section_block(
        "Classification", "dataClassification", empty=not dc, comment=comment
    ):
        if dc:
            st.write(
                f"**Classification Level:** {dc.get('classificationLevel') or 'N/A'}"
            )
            st.write(f"**Contains PII:** {'Yes' if dc.get('containsPII') else 'No'}")
            st.write(
                "**Contains Sensitive Data:** "
                f"{'Yes' if dc.get('containsSensitiveData') else 'No'}"
            )
        else:
            st.write("*No classification provided.*")
        st.markdown("**Retention Requirements**")
        if dc.get("retentionRequirements"):  # v1: a string inside the classification
            st.write(dc["retentionRequirements"])
        else:
            _show_table(
                doc.retention_requirements,
                RETENTION_COLUMNS,
                "No retention requirements provided.",
            )

    with section_block(
        "Governance",
        "dataGovernanceArtifacts",
        empty=not any(governance.values()),
        comment=comment,
    ):
        st.markdown("**Business Concepts**")
        _show_table(
            governance.get("businessConcepts", []),
            BUSINESS_CONCEPT_COLUMNS,
            "No business concepts provided.",
        )
        st.markdown("**CDE Quality**")
        _show_table(
            governance.get("cdeQuality", []),
            CDE_QUALITY_COLUMNS,
            "No CDE quality rules provided.",
        )
        st.markdown("**CDE Lineage**")
        _show_table(
            governance.get("cdeLineage", []),
            CDE_LINEAGE_COLUMNS,
            "No CDE lineage provided.",
        )

    with section_block(
        "Scope & Questions",
        "outOfScope",
        empty=not (doc.out_of_scope or doc.open_questions or doc.assumptions),
        comment=comment,
    ):
        st.markdown("**Out of Scope**")
        _show_list(doc.out_of_scope, "Nothing listed as out of scope.")
        st.markdown("**Open Questions**")
        _show_table(doc.open_questions, OPEN_QUESTION_COLUMNS, "No open questions.")
        st.markdown("**Assumptions**")
        _show_list(doc.assumptions, "No assumptions listed.")


EVENT_LABELS = {
    "creation": "Created",
    "content_save": "Saved",
    "status_transition": "Status changed",
    "cancellation": "Cancelled",
}

STATUS_FIELD_LABELS = {
    "one_pager_status": "One Pager status",
    "data_product_status": "Data Product status",
}


def event_label(event_type: str) -> str:
    """Readable name of a change-log event type."""
    return EVENT_LABELS.get(event_type, event_type.replace("_", " ").capitalize())


def render_change_log(preview_data: PreviewData) -> None:
    """Render the change log (newest-first), times in UTC."""
    if not preview_data.change_log:
        st.info("No changes recorded yet.")
        return

    for entry in preview_data.change_log:
        with st.container(border=True):
            col1, col2, col3 = st.columns([1, 2, 2])
            with col1:
                st.markdown(f"**v{entry.version}**")
                st.caption(timestamp_label(entry.created_at))
            with col2:
                st.markdown(f"**{event_label(entry.event_type)}**")
                st.caption(f"By {entry.author_name} ({entry.author_initials})")
            with col3:
                st.write(entry.summary)
                if entry.event_type == "status_transition":
                    field = STATUS_FIELD_LABELS.get(
                        entry.status_field or "", entry.status_field or ""
                    )
                    st.caption(f"{field}: {entry.from_status} → {entry.to_status}")


def _resolve(
    data_access: DataAccess, one_pager_id: str, comment_id: int, user: CurrentUser
) -> None:
    """Handle **Mark resolved** on a review comment."""
    error = resolve_comment_and_report(data_access, one_pager_id, comment_id, user)
    if error:
        st.session_state["preview_comment_error"] = error
    else:
        set_flash(PREVIEW_FLASH_KEY, "The comment was marked as resolved.")


def render_review_comments(  # noqa: C901 - one branch per comment state
    preview_data: PreviewData,
    data_access: DataAccess | None = None,
    user: CurrentUser | None = None,
    resolve: ActionState | None = None,
) -> None:
    """Render review comments grouped by section, unresolved first.

    Owner/SMEs get **Mark resolved** on each unresolved comment when
    ``resolve`` is enabled (Backend_Design.md §13).

    Args:
        preview_data: Complete preview data.
        data_access: Data access used to resolve comments.
        user: The current user.
        resolve: State of the resolve action for this user and One Pager.

    """
    error = st.session_state.pop("preview_comment_error", None)
    if error:
        st.error(error, icon=ALERT_ICON)

    if not preview_data.review_comments:
        st.info("No review comments yet.")
        return

    unresolved_total = sum(1 for c in preview_data.review_comments if not c.resolved)
    if unresolved_total:
        st.caption(f"{unresolved_total} unresolved comment(s).")

    by_section: dict[str, list[ReviewComment]] = {}
    for comment in preview_data.review_comments:
        by_section.setdefault(section_label(comment.section), []).append(comment)

    can_resolve = bool(resolve and resolve.visible and data_access and user)
    for section, comments in by_section.items():
        unresolved = [c for c in comments if not c.resolved]
        resolved = [c for c in comments if c.resolved]
        with st.expander(
            f"{section} ({len(unresolved)} open)", expanded=bool(unresolved)
        ):
            if unresolved:
                st.markdown("**Unresolved:**")
                for comment in unresolved:
                    with st.container(border=True):
                        st.markdown(
                            f"**{comment.reviewer_name}** ({comment.reviewer_initials})"
                        )
                        st.caption(
                            f"v{comment.version} • "
                            f"{timestamp_label(comment.created_at)}"
                        )
                        st.write(comment.comment)
                        if can_resolve:
                            st.button(
                                "Mark resolved",
                                key=f"preview_resolve_{comment.id}",
                                disabled=not resolve.enabled,
                                help=resolve.tooltip
                                or "Mark this comment as addressed",
                                on_click=_resolve,
                                args=(
                                    data_access,
                                    comment.one_pager_id,
                                    comment.id,
                                    user,
                                ),
                            )
                        else:
                            st.info("Unresolved — awaiting action")

            if resolved:
                st.markdown("**Resolved:**")
                for comment in resolved:
                    with st.container(border=True):
                        st.markdown(
                            f"**{comment.reviewer_name}** ({comment.reviewer_initials})"
                        )
                        st.caption(
                            f"v{comment.version} • "
                            f"{timestamp_label(comment.created_at)} "
                            f"• Resolved by {comment.resolved_by}"
                        )
                        st.write(comment.comment)
                        st.success("Resolved")


def _release_my_lock(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    """Handle **Release my lock**: release, then re-run with a confirmation."""
    try:
        released = release_lock(data_access, one_pager_id, user)
    except PermissionDeniedError:
        st.error("Only the lock holder can release this lock.")
        return
    except Exception:
        logger.exception(f"Failed to release lock on {one_pager_id}")
        st.error("Couldn't release the lock. Please retry.", icon=ALERT_ICON)
        return
    set_flash(
        PREVIEW_FLASH_KEY,
        "Your lock was released."
        if released
        else "This One Pager is no longer locked.",
    )
    st.rerun()


def render_lock_indicator(
    data_access: DataAccess,
    preview_data: PreviewData,
    lock: LockInfo | None,
    user: CurrentUser,
) -> None:
    """Render the lock status indicator (UI_Design.md §4.4).

    Locked by the current user: a notice with **Release my lock**. Locked by
    someone else: a read-only notice. Not locked (or the lock expired): nothing.

    Args:
        data_access: Data access used to release the lock.
        preview_data: Complete preview data.
        lock: The active lock, or None.
        user: The current user.

    """
    if lock is None:
        return

    acquired = timestamp_label(lock.acquired_at, "%Y-%m-%d %H:%M")
    expires = timestamp_label(lock.expires_at, "%H:%M")
    header = preview_data.header
    action = get_action_states(
        current_user_initials=user.initials,
        owner_initials=header.owner_initials,
        one_pager_status=header.one_pager_status,
        is_locked=True,
        lock_holder_initials=lock.locked_by_initials,
    )["release_lock"]

    if not action.enabled:
        st.warning(
            f"**Locked by {lock.locked_by_name}** ({lock.locked_by_initials}) "
            f"since {acquired} (expires {expires}). Read-only mode.",
            icon=":material/lock:",
        )
        return

    st.info(
        f"**Locked by you** since {acquired} (expires {expires}).",
        icon=":material/lock:",
    )
    if st.button(
        "Release my lock",
        key="preview_release_lock",
        help="Release your edit lock so others can edit this One Pager",
    ):
        _release_my_lock(data_access, header.one_pager_id, user)


# Preview actions in display order → button label (UI_Design.md §4.4).
ACTION_BUTTONS = {
    "edit": "Edit",
    "update": "Update",
    "change_dp_status": "Change Data Product status",
    "approve": "Approve",
    "reject": "Reject",
    "add_comment": "Add Comment",
    "cancel": "Cancel One Pager",
    "export_pdf": "Export PDF",
}


def load_authorized_initials(data_access: DataAccess, one_pager_id: str) -> set[str]:
    """Return the Owner/SMEs' initials; empty (edit disabled) if unreadable."""
    try:
        users = data_access.get_authorized_users(one_pager_id)
    except Exception:
        logger.exception(f"Failed to load authorized users of {one_pager_id}")
        return set()
    return {u.user_initials for u in users if u.role in AUTHORIZED_ROLES}


@st.dialog("Cancel this One Pager?")
def confirm_cancel(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    """Confirm **Cancel One Pager** (UI_Design.md §5): it is permanent."""
    st.write(
        "Cancelling is permanent: the One Pager and its Data Product both become "
        "**Cancelled** and can no longer be edited."
    )
    reason = st.text_area(
        "Reason (optional)", key="preview_cancel_reason", max_chars=500
    )
    col_confirm, col_keep = st.columns(2)
    if col_confirm.button("Cancel One Pager", type="primary", use_container_width=True):
        error = cancel_and_report(data_access, one_pager_id, user, reason)
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        st.rerun()
    if col_keep.button("Keep it", use_container_width=True):
        st.rerun()


@st.dialog("Update this One Pager?")
def confirm_update(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    """[Update] confirmation dialog (UI_Design.md §4.4)."""
    st.write(
        "This will create a working copy for editing. The current approved "
        "version remains in Git until you complete the review cycle. Proceed?"
    )
    col_confirm, col_back = st.columns(2)
    if col_confirm.button("Confirm", type="primary", use_container_width=True):
        error = update_and_report(data_access, one_pager_id, user)
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        st.rerun()
    if col_back.button("Cancel", key="preview_update_back", use_container_width=True):
        st.rerun()


def _after_decision(*, from_queue: bool) -> None:
    """After Approve/Reject: back to the Review queue if it was opened there."""
    if from_queue:
        back_to_review_queue(st.session_state.pop(PREVIEW_FLASH_KEY, None))
    st.rerun()


@st.dialog("Reject this One Pager?")
def confirm_reject(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    roles: frozenset[Actor],
) -> None:
    """Reject dialog (UI_Design.md §4.4): the reason is mandatory."""
    st.write(
        "The One Pager goes back to **Draft** for its Owner. Your reason is "
        "stored as a review comment and in the change log."
    )
    reason = st.text_area(
        "Reason *",
        key="preview_reject_reason",
        max_chars=MAX_COMMENT_LENGTH,
        placeholder="Why is this being rejected?",
    )
    col_confirm, col_back = st.columns(2)
    if col_confirm.button("Confirm Reject", type="primary", use_container_width=True):
        from_queue = st.session_state.get(REVIEW_MODE_KEY) == one_pager_id
        error = reject_and_report(data_access, one_pager_id, user, reason, roles)
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        _after_decision(from_queue=from_queue)
    if col_back.button("Cancel", key="preview_reject_back", use_container_width=True):
        st.rerun()


@st.dialog("Approve this One Pager?")
def confirm_approve(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
    roles: frozenset[Actor],
) -> None:
    """Show what the approval does (version, DP status) before it is made."""
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        st.error(f"One Pager {one_pager_id} not found.")
        return
    plan = plan_approval(row)
    st.write(f"The One Pager becomes **Approved** as version **v{plan.version}**.")
    if plan.data_product_status:
        st.write(
            f"The Data Product status changes from **{row.data_product_status}** "
            f"to **{plan.data_product_status}**."
        )
    col_confirm, col_back = st.columns(2)
    if col_confirm.button("Approve", type="primary", use_container_width=True):
        from_queue = st.session_state.get(REVIEW_MODE_KEY) == one_pager_id
        error = approve_and_report(
            data_access, document_store, one_pager_id, user, roles
        )
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        _after_decision(from_queue=from_queue)
    if col_back.button("Cancel", key="preview_approve_back", use_container_width=True):
        st.rerun()


@st.dialog("Add a review comment")
def add_comment_dialog(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    roles: frozenset[Actor],
    section: str | None = None,
) -> None:
    """Section-level review comment (UI_Design.md §4.4, Approver in review).

    ``section`` preselects the section (from "Comment on this section").
    """
    options = list(SECTION_LABELS)
    preset = section
    section = st.selectbox(
        "Section",
        options=options,
        index=options.index(preset) if preset in options else 0,
        format_func=section_label,
        key=f"preview_comment_section_{preset}"
        if preset
        else "preview_comment_section",
    )
    text = st.text_area(
        "Comment *",
        key="preview_comment_text",
        max_chars=MAX_COMMENT_LENGTH,
        placeholder="What should the Owner change or clarify?",
    )
    col_post, col_back = st.columns(2)
    if col_post.button("Post", type="primary", use_container_width=True):
        error = add_comment_and_report(
            data_access, one_pager_id, user, section, text, roles
        )
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        st.rerun()
    if col_back.button("Cancel", key="preview_comment_back", use_container_width=True):
        st.rerun()


def in_review_mode(one_pager_id: str, status: str, roles: frozenset[Actor]) -> bool:
    """Tell whether an Approver opened it from the Review queue while In Review."""
    return (
        st.session_state.get(REVIEW_MODE_KEY) == one_pager_id
        and status == IN_REVIEW
        and Actor.APPROVER in roles
    )


def render_review_banner(one_pager_id: str) -> None:
    """Review-mode notice with the way back to the queue (UI_Design.md §4.3)."""
    col_text, col_back = st.columns([4, 1])
    col_text.info(
        f"**Review mode** — you are reviewing {one_pager_id}. See **What "
        "changed**, comment on sections, then Approve or Reject.",
    )
    if col_back.button(
        "Back to Review queue", key="preview_back_to_queue", use_container_width=True
    ):
        back_to_review_queue()


def comparison_for(
    data_access: DataAccess, preview_data: PreviewData
) -> tuple[ComparisonBase, list[SectionChange]] | None:
    """Return the base version and the changes since it; None for a first version.

    Read fresh; a version that cannot be read hides the tab (logged).
    """
    header = preview_data.header
    base = comparison_base(preview_data.change_log, header.version)
    if base is None:
        return None
    try:
        old = data_access.read_document(header.one_pager_id, base.version)
    except Exception:
        logger.exception(f"Failed to read v{base.version} of {header.one_pager_id}")
        return None
    if old is None:
        return None
    return base, diff_documents(old, preview_data.document)


_CHANGE_KIND_LABELS = {"added": "Added", "removed": "Removed", "changed": "Changed"}


def render_what_changed(base: ComparisonBase, changes: list[SectionChange]) -> None:
    """Sections that differ from the base version, before and after."""
    st.caption(f"Compared with {base.label}.")
    if not changes:
        st.info("No content changes since that version.")
        return
    st.markdown(
        f"**{len(changes)} section(s) changed:** "
        + ", ".join(change.label for change in changes)
    )
    for change in changes:
        with st.expander(
            f"{change.label} — {_CHANGE_KIND_LABELS[change.kind]}", expanded=True
        ):
            before_col, after_col = st.columns(2)
            before_col.markdown(f"**Before (v{base.version})**")
            before_col.code(change.before or "(empty)", language=None, wrap_lines=True)
            after_col.markdown("**Now**")
            after_col.code(change.after or "(empty)", language=None, wrap_lines=True)


@st.dialog("Export PDF")
def export_pdf_dialog(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    status_colors: dict[str, str],
) -> None:
    """Render the PDF (Backend_Design.md §10) and offer it for download."""
    with st.spinner("Creating the PDF..."):
        export, error = export_pdf_and_report(
            data_access, one_pager_id, user, status_colors
        )
    if error or export is None:
        st.error(
            error or "The PDF could not be created. Please retry.", icon=ALERT_ICON
        )
        return
    st.write(
        "The PDF contains every section of the current version, with its Use "
        "Cases and change log."
    )
    st.download_button(
        f"Download {export.filename}",
        data=export.content,
        file_name=export.filename,
        mime="application/pdf",
        key="preview_download_pdf",
        type="primary",
        use_container_width=True,
    )


@st.dialog("Change Data Product status")
def change_dp_status_dialog(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    options: list[TransitionRule],
) -> None:
    """Offer the valid DP transitions (UI_Design.md §4.4).

    Asks before destructive ones such as Deprecate.
    """
    by_target = {rule.to_status: rule for rule in options}
    target = st.selectbox(
        "New status",
        options=list(by_target),
        format_func=lambda s: f"{by_target[s].label} → {s}",
        key="preview_dp_target",
    )
    rule = by_target[target]
    confirmed = True
    if rule.requires_confirmation:
        st.warning(f"**{rule.label}** cannot be undone.", icon=ALERT_ICON)
        confirmed = st.checkbox(
            f"Yes, {rule.label.lower()} this Data Product",
            key="preview_dp_confirm",
        )
    col_apply, col_back = st.columns(2)
    if col_apply.button(
        "Change status",
        type="primary",
        disabled=not confirmed,
        use_container_width=True,
    ):
        error = change_dp_status_and_report(
            data_access, one_pager_id, target, user, confirmed=confirmed
        )
        if error:
            st.error(error, icon=ALERT_ICON)
            return
        st.rerun()
    if col_back.button("Back", use_container_width=True):
        st.rerun()


# Actions in the "More" menu: rarely used or destructive.
MORE_ACTIONS = ("change_dp_status", "export_pdf", "cancel")

# The first of these the user can do is the page's primary action.
PRIMARY_ACTIONS = ("approve", "edit", "update")


def primary_action(actions: dict[str, ActionState]) -> str | None:
    """Return the action to highlight: Approve, Edit or Update, whichever applies."""
    return next(
        (
            name
            for name in PRIMARY_ACTIONS
            if actions[name].visible and actions[name].enabled
        ),
        None,
    )


def unavailable_reasons(actions: dict[str, ActionState]) -> list[str]:
    """List "**Edit**: <reason>" for the shown actions that are disabled."""
    return [
        f"**{ACTION_BUTTONS[name]}**: {actions[name].tooltip}"
        for name in ACTION_BUTTONS
        if actions[name].visible and not actions[name].enabled and actions[name].tooltip
    ]


def _action_button(name: str, state: ActionState, *, primary: bool) -> bool:
    return st.button(
        ACTION_BUTTONS[name],
        key=f"preview_{name}",
        disabled=not state.enabled,
        help=state.tooltip or None,
        type="primary" if primary else "secondary",
        use_container_width=True,
    )


def render_action_bar(  # noqa: C901, PLR0912, PLR0913 - one branch per action
    data_access: DataAccess,
    preview_data: PreviewData,
    lock: LockInfo | None,
    user: CurrentUser,
    authorized_initials: set[str],
    roles: frozenset[Actor] = frozenset(),
    status_colors: dict[str, str] | None = None,
) -> None:
    """Render the action buttons (UI_Design.md §4.4).

    The main actions are buttons, the one the user most likely wants
    (Approve, Edit or Update) highlighted; Change Data Product status, Export PDF and
    the destructive Cancel One Pager sit in the **More** menu. Why a shown
    action is unavailable is written under the buttons, not only in a tooltip.

    Args:
        data_access: Data access for the actions.
        preview_data: Complete preview data.
        lock: The active lock, or None.
        user: The current user.
        authorized_initials: Initials of the Owner/SMEs of this One Pager.
        roles: Group roles of the user (Approver, Admin).
        status_colors: Badge colors of the OP and DP statuses, for the PDF.

    """
    header = preview_data.header
    actions = get_action_states(
        current_user_initials=user.initials,
        owner_initials=header.owner_initials,
        one_pager_status=header.one_pager_status,
        is_locked=lock is not None,
        lock_holder_initials=lock.locked_by_initials if lock else None,
        authorized_initials=authorized_initials,
        data_product_status=header.data_product_status,
        roles=roles,
    )
    primary = primary_action(actions)
    main = [n for n in ACTION_BUTTONS if n not in MORE_ACTIONS and actions[n].visible]
    more = [n for n in MORE_ACTIONS if actions[n].visible]

    clicked = None
    columns = st.columns([1.2] * len(main) + [1] + [max(1, 6 - len(main))])
    for column, name in zip(columns, main, strict=False):
        with column:
            if _action_button(name, actions[name], primary=name == primary):
                clicked = name
    if more:
        with columns[len(main)].popover("More", use_container_width=True):
            for name in more:
                if _action_button(name, actions[name], primary=False):
                    clicked = name

    for reason in unavailable_reasons(actions):
        st.caption(reason)
    if header.one_pager_status == "Cancelled":
        st.caption("This One Pager is cancelled (read-only).")
    elif header.one_pager_status == "In Review" and not actions["approve"].visible:
        st.caption("Waiting for an Approver's review.")

    if clicked == "edit":
        open_in_editor(header.one_pager_id)
    elif clicked == "cancel":
        confirm_cancel(data_access, header.one_pager_id, user)
    elif clicked == "update":
        confirm_update(data_access, header.one_pager_id, user)
    elif clicked == "reject":
        confirm_reject(data_access, header.one_pager_id, user, roles)
    elif clicked == "add_comment":
        add_comment_dialog(data_access, header.one_pager_id, user, roles)
    elif clicked == "approve":
        confirm_approve(
            data_access,
            st.session_state.document_store,
            header.one_pager_id,
            user,
            roles,
        )
    elif clicked == "export_pdf":
        export_pdf_dialog(data_access, header.one_pager_id, user, status_colors or {})
    elif clicked == "change_dp_status":
        row = data_access.get_one_pager_status_row(header.one_pager_id)
        options = (
            data_product_options(row, owner_or_sme=user.initials in authorized_initials)
            if row
            else []
        )
        if options:
            change_dp_status_dialog(data_access, header.one_pager_id, user, options)


# ============================================================================
# Preview Page
# ============================================================================

# Initialize services from session state
data_access = require_data_access()
user = signed_in_user()
roles = current_roles()

# Resolve one_pager_id: internal navigation (session_state) takes priority since
# st.switch_page clears query params; fall back to a query param (deep link).
selected_id = st.session_state.get(PREVIEW_ID_KEY)
query_one_pager_id = st.query_params.get("one_pager_id")
if selected_id:
    one_pager_id = selected_id
elif query_one_pager_id:
    one_pager_id = (
        query_one_pager_id[0]
        if isinstance(query_one_pager_id, list)
        else query_one_pager_id
    )
else:
    # No silent default (UI_Design.md §4.4): ask the user to pick a One Pager.
    page_header("Preview", PREVIEW_SUBTITLE)
    st.info(
        "No One Pager selected. Open one from the Registry with the **View** button."
    )
    if st.button("Go to Registry", key="preview_go_to_registry"):
        go_to_registry()
    st.stop()

# Keep session_state and the URL in sync, so a refresh or bookmark resolves the
# same One Pager.
st.session_state[PREVIEW_ID_KEY] = one_pager_id
st.query_params["one_pager_id"] = one_pager_id


# Fetch preview data. Errors show a friendly banner with Retry; details go to the
# log only, never to the user (UI_Design.md §5, Architecture.md §7).
try:
    with st.spinner("Loading One Pager..."):
        preview_data = data_access.get_one_pager(one_pager_id)
except Exception as e:
    logger.exception(f"Failed to load One Pager {one_pager_id}")
    render_error_state(
        user_error_message(e, LOAD_ERROR_MESSAGE), key="preview_retry_load"
    )

# Not found state
if not preview_data:
    st.error(f"One Pager **{one_pager_id}** not found.")
    st.info("Use the **Registry** tab in the sidebar to browse available One Pagers.")
    st.stop()

# Load status colors
try:
    op_colors = cache.op_status_colors(data_access)
    dp_colors = cache.dp_status_colors(data_access)
except Exception as e:
    logger.exception("Failed to load status colors")
    render_error_state(
        user_error_message(e, LOAD_ERROR_MESSAGE), key="preview_retry_colors"
    )

# One-time confirmation after a redirect (e.g. "One Pager OP-0003 created").
show_flash(PREVIEW_FLASH_KEY)

# Populated state: render all regions
header = preview_data.header
review_mode = in_review_mode(one_pager_id, header.one_pager_status, roles)
if review_mode:
    render_review_banner(one_pager_id)
elif st.button("← Back to the Registry", key="preview_back"):
    go_to_registry()

render_header(preview_data, op_colors, dp_colors)
render_status_timeline(header.one_pager_status, header.version, preview_data.change_log)

# Expired locks count as "not locked" (Backend_Design.md §6). The lock notice
# sits next to the actions, since it explains why Edit may be unavailable.
lock = active_lock(preview_data.lock)
authorized_initials = load_authorized_initials(data_access, one_pager_id)
action_states = get_action_states(
    current_user_initials=user.initials,
    owner_initials=header.owner_initials,
    one_pager_status=header.one_pager_status,
    is_locked=lock is not None,
    lock_holder_initials=lock.locked_by_initials if lock else None,
    authorized_initials=authorized_initials,
    data_product_status=header.data_product_status,
    roles=roles,
)

st.divider()
render_lock_indicator(data_access, preview_data, lock, user)
render_action_bar(
    data_access,
    preview_data,
    lock,
    user,
    authorized_initials,
    roles,
    {**dp_colors, **op_colors},
)
section_gap()

comparison = comparison_for(data_access, preview_data)
unresolved = sum(1 for c in preview_data.review_comments if not c.resolved)
tab_names = ["Content"]
if comparison is not None:
    tab_names.insert(1 if not review_mode else 0, "What changed")
tab_names.append(f"Change log ({len(preview_data.change_log)})")
tab_names.append(
    f"Review comments ({unresolved} open)" if unresolved else "Review comments"
)
tabs = dict(zip(tab_names, st.tabs(tab_names), strict=True))

with tabs["Content"]:
    comment_target = (
        CommentTarget(data_access, one_pager_id, user, roles)
        if action_states["add_comment"].visible and action_states["add_comment"].enabled
        else None
    )
    render_content_sections(
        preview_data,
        resolve_use_cases(data_access, preview_data.document),
        comment_target,
    )

if comparison is not None:
    with tabs["What changed"]:
        render_what_changed(*comparison)

with tabs[tab_names[-2]]:
    render_change_log(preview_data)

with tabs[tab_names[-1]]:
    render_review_comments(
        preview_data, data_access, user, action_states["resolve_comment"]
    )
