"""Help page — the lifecycle, roles and workflow in the app (UI_Design.md §4.6).

Explains the two-status model with a diagram per status machine, lists every
transition and the valid status combinations, the roles, a quick reference of
the common workflows and the status badge legend.

Diagrams and tables are built from the workflow service's serialized
``TRANSITIONS`` (``workflow.get_workflow_reference``), so they always match
the rules the app enforces. Badge colors and labels come from the reference
tables; if those cannot be read the page still shows everything, in gray,
under a warning with Retry. Every user sees this page.
"""

import logging

import pandas as pd
import streamlit as st

from adapters.theme import status_badge
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import (
    LIFECYCLE_INTRO,
    QUICK_REFERENCE,
    ROLE_REQUEST,
    ROLES,
    VERSIONING_NOTE,
    BadgeInfo,
    combinations_table,
    dp_legend,
    op_legend,
    state_diagram_dot,
    transition_table,
)
from onepagerapp.workflow import get_workflow_reference

logger = logging.getLogger(__name__)

COLORS_ERROR_MESSAGE = (
    "Couldn't load the status colors. The page is shown without them. Please retry."
)


def load_legends(data_access: DataAccess) -> tuple[list[BadgeInfo], list[BadgeInfo]]:
    """One Pager and Data Product badges; gray defaults if they cannot be read."""
    try:
        return (
            op_legend(data_access.get_ref_op_status()),
            dp_legend(data_access.get_ref_dp_status()),
        )
    except Exception as e:
        logger.exception("Failed to load the status reference tables")
        st.warning(user_error_message(e, COLORS_ERROR_MESSAGE), icon="⚠️")
        if st.button("Retry", key="help_retry"):
            st.rerun()
        return op_legend(pd.DataFrame()), dp_legend(pd.DataFrame())


def render_legend(title: str, legend: list[BadgeInfo]) -> None:
    """Status badges (dot + text, never color alone) with a terminal marker."""
    st.markdown(f"**{title}**")
    for badge in legend:
        suffix = " — final status" if badge.is_terminal else ""
        st.markdown(
            f"{status_badge(badge.label, badge.color)}{suffix}",
            unsafe_allow_html=True,
        )


def render_machine(
    title: str,
    transitions: list[dict[str, object]],
    legend: list[BadgeInfo],
    note: str,
) -> None:
    """Diagram and transition table of one status machine."""
    st.subheader(title)
    st.graphviz_chart(state_diagram_dot(transitions, legend), use_container_width=True)
    st.caption(note)
    with st.expander("All transitions and their conditions"):
        st.dataframe(
            transition_table(transitions), hide_index=True, use_container_width=True
        )


# ============================================================================
# Help Page
# ============================================================================

if not st.session_state.get("services_initialized"):
    st.error("Services not initialized. Please refresh the page.")
    st.stop()

data_access: DataAccess = st.session_state.data_access

st.title("Help")
st.caption("How One Pagers move through their lifecycle, and who does what.")

op_badges, dp_badges = load_legends(data_access)
reference = get_workflow_reference()

st.header("The two-status lifecycle")
st.markdown(LIFECYCLE_INTRO)
st.markdown(VERSIONING_NOTE)

render_machine(
    "One Pager status",
    reference["one_pager_transitions"],
    op_badges,
    "Submit for Review passes through Ready for Review in one step: you never "
    "wait in it. Double borders mark final statuses.",
)
render_machine(
    "Data Product status",
    reference["data_product_transitions"],
    dp_badges,
    "Dashed arrows happen automatically when the One Pager is approved or "
    "cancelled. Double borders mark final statuses.",
)

st.subheader("Valid status combinations")
st.caption(
    "Every change is checked against this table, so the two statuses never "
    "contradict each other."
)
st.dataframe(
    combinations_table(reference["valid_combinations"]),
    hide_index=True,
    use_container_width=True,
)

st.header("Roles and responsibilities")
st.dataframe(
    pd.DataFrame(
        [{"Role": r.role, "Who": r.who, "What they do": r.can_do} for r in ROLES]
    ),
    hide_index=True,
    use_container_width=True,
)
st.caption(
    "Owner/SME rights apply per Data Product: only the people listed as Owner "
    "or SME of a One Pager may edit it, matched on their corporate initials "
    "(e.g. X0W for the username x0wadm@…; 3 letters or digits, which may differ "
    "from the initials of your name)."
)

with st.expander(ROLE_REQUEST.title):
    st.markdown(
        "\n".join(f"{n}. {step}" for n, step in enumerate(ROLE_REQUEST.steps, 1))
    )

st.header("Workflow quick reference")
for item in QUICK_REFERENCE:
    with st.expander(item.title):
        st.markdown(
            "\n".join(f"{number}. {step}" for number, step in enumerate(item.steps, 1))
        )

st.header("Status badges")
col_op, col_dp = st.columns(2)
with col_op:
    render_legend("One Pager status", op_badges)
with col_dp:
    render_legend("Data Product status", dp_badges)
st.caption("🔒 Locked by … — someone is editing the One Pager; it is read-only for others.")
