"""Review page — the Approver's work queue (UI_Design.md §4.3).

Lists every One Pager ``In Review``, oldest submission first, with the pending
count in the header. **Review** on a row opens the One Pager in Preview in
review mode, where the Approver approves, rejects or comments.

The queue is read fresh on every run (never cached). Only Approvers see the
page in the navigation; the service re-checks the role (Backend_Design.md §5).
"""

import logging
from datetime import UTC, datetime

import streamlit as st

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import CurrentUser, OnePagerStatusRow
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import get_review_queue
from onepagerapp.state_machine import Actor

logger = logging.getLogger(__name__)

LOAD_ERROR_MESSAGE = "Couldn't load the review queue. Please retry."
EMPTY_MESSAGE = "Nothing waiting for your review."

QUEUE_COLUMNS = ["ID", "Product", "Domain", "Owner", "Submitted", "Version", ""]
_COLUMN_RATIOS = [1.0, 2.4, 1.5, 1.8, 1.4, 1.0, 1.0]

# Session key Preview reads to show the review actions (UI_Design.md §4.3).
REVIEW_MODE_KEY = "preview_review_mode"


def submitted_label(value: datetime) -> str:
    """Submission date for the queue, in UTC (e.g. "2026-09-19 10:15 UTC")."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%d %H:%M") + " UTC"


def open_in_review_mode(one_pager_id: str) -> None:
    """Open the One Pager in Preview with the review actions."""
    st.session_state["preview_one_pager_id"] = one_pager_id
    st.session_state[REVIEW_MODE_KEY] = one_pager_id
    st.switch_page("views/preview.py")


def render_queue(rows: list[OnePagerStatusRow]) -> None:
    """Table of pending One Pagers with a **Review** button per row."""
    st.caption("Click **Review** to open a One Pager in Preview with review actions.")
    header = st.columns(_COLUMN_RATIOS)
    for column, title in zip(header, QUEUE_COLUMNS, strict=True):
        column.markdown(f"**{title}**")
    st.divider()
    for row in rows:
        cells = st.columns(_COLUMN_RATIOS)
        values = [
            row.one_pager_id,
            row.product_name,
            row.business_domain,
            f"{row.owner_name} ({row.owner_initials})",
            submitted_label(row.last_updated_at),
            row.version,
        ]
        for column, value in zip(cells[:-1], values, strict=True):
            column.markdown(value or "—")
        with cells[-1]:
            if st.button(
                "Review",
                key=f"review_open_{row.one_pager_id}",
                use_container_width=True,
                help=f"Open {row.one_pager_id} in Preview to review it",
            ):
                open_in_review_mode(row.one_pager_id)


# ============================================================================
# Review Page
# ============================================================================

if not st.session_state.get("services_initialized"):
    st.error("Services not initialized. Please refresh the page.")
    st.stop()

data_access: DataAccess = st.session_state.data_access
current_user_info: CurrentUser = st.session_state.get(
    "current_user_info"
) or resolve_current_user(
    st.session_state.get("current_user", "unknown"), st.session_state.config
)
roles: frozenset[Actor] = st.session_state.get("current_user_roles", frozenset())

try:
    with st.spinner("Loading the review queue..."):
        queue = get_review_queue(data_access, current_user_info, roles)
except PermissionDeniedError as e:
    st.title("Review Queue")
    st.info(str(e))
    st.stop()
except Exception:
    logger.exception("Failed to load the review queue")
    st.title("Review Queue")
    st.error(LOAD_ERROR_MESSAGE, icon="⚠️")
    if st.button("Retry", key="review_retry"):
        st.rerun()
    st.stop()

title_col, count_col = st.columns([4, 1])
title_col.title("Review Queue")
count_col.metric("Pending", len(queue))

if not queue:
    st.info(EMPTY_MESSAGE)
    st.stop()

render_queue(queue)
