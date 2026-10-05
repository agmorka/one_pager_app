"""My work — the landing page: what needs the signed-in user's attention.

Shows the user's drafts (with unresolved review comments and their own edit
locks), their One Pagers waiting for review, the Approver's review queue and
their approved One Pagers. Each row opens the One Pager; the Registry stays
one click away for browsing everything.

Read fresh on every run (``my_work.get_my_work``), never cached.
"""

import logging

import streamlit as st

from adapters import cache
from adapters.navigation import (
    REVIEW_PAGE,
    go_to_registry,
    open_editor,
    open_in_editor,
    open_in_review_mode,
    open_preview,
)
from adapters.page import (
    current_roles,
    page_header,
    render_error_state,
    require_data_access,
    signed_in_user,
    timestamp_label,
)
from adapters.theme import DEFAULT_BADGE_COLOR, section_gap, status_badge
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import TOPIC_MY_WORK
from onepagerapp.models import OnePagerStatusRow, RegistryRow
from onepagerapp.my_work import MyWork, get_my_work
from onepagerapp.permissions import can_create_one_pager, can_review
from onepagerapp.timeutils import age_label

logger = logging.getLogger(__name__)

LOAD_ERROR_MESSAGE = "Couldn't load your work list. Please retry."
SUBTITLE = "What needs your attention: your drafts, reviews and One Pagers."
QUEUE_PREVIEW_ROWS = 5

_ROW_RATIOS = [1.1, 3.0, 1.6, 2.2, 1.0, 1.0]


# ============================================================================
# Helpers
# ============================================================================


def _registry_button(key: str) -> None:
    if st.button(":material/list_alt: Browse all One Pagers in the Registry", key=key):
        go_to_registry()


def _new_one_pager() -> None:
    for key in [k for k in st.session_state if str(k).startswith("create_")]:
        del st.session_state[key]
    open_editor("create")


def _render_counts(work: MyWork, *, reviewer: bool) -> None:
    counts = [
        ("Drafts to finish", len(work.drafts)),
        ("Comments to resolve", sum(work.open_comments.values())),
        ("Waiting for review", len(work.waiting_for_review)),
    ]
    if reviewer:
        counts.append(("Waiting for your review", len(work.review_queue)))
    for column, (label, value) in zip(st.columns(len(counts)), counts, strict=True):
        column.metric(label, value)


def _render_my_rows(
    rows: list[RegistryRow],
    work: MyWork,
    colors: dict[str, str],
    key: str,
    *,
    editable: bool,
) -> None:
    """One line per One Pager: ID, name, status, notes, Open (and Edit)."""
    for row in rows:
        cols = st.columns(_ROW_RATIOS, vertical_alignment="center")
        cols[0].markdown(f"**{row.one_pager_id}**")
        cols[1].markdown(row.product_name or "—")
        cols[2].markdown(
            status_badge(
                row.one_pager_status,
                colors.get(row.one_pager_status, DEFAULT_BADGE_COLOR),
            ),
            unsafe_allow_html=True,
        )
        notes = []
        comments = work.open_comments.get(row.one_pager_id)
        if comments:
            notes.append(
                f":material/comment: {comments} comment"
                f"{'s' if comments != 1 else ''} to resolve"
            )
        if row.one_pager_id in work.locked_by_me:
            notes.append(":material/lock: you are editing")
        cols[3].markdown(" · ".join(notes) or f"v{row.version}")
        if cols[4].button(
            "Open",
            key=f"mywork_{key}_open_{row.one_pager_id}",
            use_container_width=True,
            help=f"Open {row.one_pager_id} in Preview",
        ):
            open_preview(row.one_pager_id)
        if editable and cols[5].button(
            "Edit",
            key=f"mywork_{key}_edit_{row.one_pager_id}",
            type="primary",
            use_container_width=True,
            help=f"Continue editing {row.one_pager_id}",
        ):
            open_in_editor(row.one_pager_id)


def _render_review_rows(rows: list[OnePagerStatusRow]) -> None:
    for row in rows[:QUEUE_PREVIEW_ROWS]:
        cols = st.columns(_ROW_RATIOS, vertical_alignment="center")
        cols[0].markdown(f"**{row.one_pager_id}**")
        cols[1].markdown(row.product_name or "—")
        cols[2].markdown(f"{row.owner_name} ({row.owner_initials})")
        cols[3].markdown(
            f"Submitted {timestamp_label(row.last_updated_at)} · "
            f"waiting {age_label(row.last_updated_at)}"
        )
        if cols[5].button(
            "Review",
            key=f"mywork_review_{row.one_pager_id}",
            type="primary",
            use_container_width=True,
        ):
            open_in_review_mode(row.one_pager_id)
    if len(rows) > QUEUE_PREVIEW_ROWS and st.button(
        f"See all {len(rows)} in the Review queue :material/arrow_forward:",
        key="mywork_all_reviews",
    ):
        st.switch_page(REVIEW_PAGE)


# ============================================================================
# My work Page
# ============================================================================

data_access = require_data_access()
user = signed_in_user()
roles = current_roles()
reviewer = can_review(roles)

title_col, new_col = st.columns([5, 1], vertical_alignment="bottom")
with title_col:
    page_header(
        f"My work — {user.display_name or user.initials}",
        SUBTITLE,
        help_topic=TOPIC_MY_WORK,
    )
with new_col:
    if can_create_one_pager(user, roles) and st.button(
        ":material/add: New One Pager",
        type="primary",
        use_container_width=True,
        key="mywork_new",
    ):
        _new_one_pager()

try:
    with st.spinner("Loading your work..."):
        work = get_my_work(data_access, user, roles)
    op_colors = cache.op_status_colors(data_access)
except Exception as e:
    logger.exception("Failed to load the work list")
    render_error_state(user_error_message(e, LOAD_ERROR_MESSAGE), key="mywork_retry")

_render_counts(work, reviewer=reviewer)
section_gap()

nothing = not (
    work.drafts or work.waiting_for_review or work.approved or work.review_queue
)
if nothing:
    st.info(
        "Nothing needs your attention right now. You are not Owner or SME of "
        "any One Pager yet."
        + (" Waiting reviews will appear here too." if reviewer else "")
    )
    _registry_button("mywork_registry_empty")
    st.stop()

if reviewer:
    st.subheader("Waiting for your review")
    if work.review_queue:
        _render_review_rows(work.review_queue)
    else:
        st.caption("Nothing waiting for your review.")
    section_gap()

st.subheader("Continue editing")
if work.drafts:
    _render_my_rows(work.drafts, work, op_colors, "drafts", editable=True)
else:
    st.caption(
        "You have no drafts. Use **Update** on an approved One Pager to change it."
    )
section_gap()

st.subheader("Waiting for review")
if work.waiting_for_review:
    _render_my_rows(work.waiting_for_review, work, op_colors, "waiting", editable=False)
else:
    st.caption("None of your One Pagers is waiting for review.")
section_gap()

with st.expander(f"Approved ({len(work.approved)})"):
    if work.approved:
        _render_my_rows(work.approved, work, op_colors, "approved", editable=False)
    else:
        st.caption("None of your One Pagers is approved yet.")

_registry_button("mywork_registry")
