"""Review page — the Approver's work queue (UI_Design.md §4.3).

Lists every One Pager ``In Review``, oldest submission first, with the pending
count in the header. **Review** on a row opens the One Pager in Preview in
review mode, where the Approver approves, rejects or comments.

The queue is read fresh on every run (never cached). Only Approvers see the
page in the navigation; the service re-checks the role (Backend_Design.md §5).
"""

import logging

import streamlit as st

from adapters.navigation import REVIEW_FLASH_KEY, open_in_review_mode
from adapters.page import (
    current_roles,
    page_header,
    render_error_state,
    require_data_access,
    show_flash,
    signed_in_user,
    timestamp_label,
)
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import TOPIC_REVIEW
from onepagerapp.models import OnePagerStatusRow
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import get_review_queue
from onepagerapp.timeutils import age_label

logger = logging.getLogger(__name__)

LOAD_ERROR_MESSAGE = "Couldn't load the review queue. Please retry."
REVIEW_SUBTITLE = "One Pagers waiting for review, oldest submission first."
EMPTY_MESSAGE = "Nothing waiting for your review."

QUEUE_COLUMNS = [
    "ID",
    "Product",
    "Domain",
    "Owner",
    "Submitted",
    "Waiting",
    "Version",
    "",
]
_COLUMN_RATIOS = [1.0, 2.3, 1.4, 1.8, 1.5, 0.9, 0.9, 1.0]


# ============================================================================
# Render Components
# ============================================================================


def render_queue(rows: list[OnePagerStatusRow]) -> None:
    """Table of pending One Pagers with a **Review** button per row."""
    st.caption(
        "Click **Review** to open a One Pager: see what changed, comment on "
        "sections, then approve or reject. You come back here afterwards."
    )
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
            timestamp_label(row.last_updated_at),
            age_label(row.last_updated_at),
            row.version,
        ]
        for column, value in zip(cells[:-1], values, strict=True):
            column.markdown(value or "—")
        with cells[-1]:
            if st.button(
                "Review",
                key=f"review_open_{row.one_pager_id}",
                type="primary",
                use_container_width=True,
                help=f"Open {row.one_pager_id} in Preview to review it",
            ):
                open_in_review_mode(row.one_pager_id)


# ============================================================================
# Review Page
# ============================================================================

data_access = require_data_access()
user = signed_in_user()
roles = current_roles()

try:
    with st.spinner("Loading the review queue..."):
        queue = get_review_queue(data_access, user, roles)
except PermissionDeniedError as e:
    page_header("Review Queue", REVIEW_SUBTITLE)
    st.info(str(e))
    st.stop()
except Exception as e:
    logger.exception("Failed to load the review queue")
    page_header("Review Queue", REVIEW_SUBTITLE)
    render_error_state(user_error_message(e, LOAD_ERROR_MESSAGE), key="review_retry")

title_col, count_col = st.columns([4, 1])
with title_col:
    page_header("Review Queue", REVIEW_SUBTITLE, help_topic=TOPIC_REVIEW)
count_col.metric("Pending", len(queue))
show_flash(REVIEW_FLASH_KEY)

if not queue:
    st.info(EMPTY_MESSAGE)
    st.stop()

render_queue(queue)
