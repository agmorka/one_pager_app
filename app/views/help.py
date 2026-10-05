"""Help page — the lifecycle, roles and workflow in the app (UI_Design.md §4.6).

Explains the two-status model with a diagram per status machine, lists every
transition and the valid status combinations, the roles and a quick reference
of the common workflows. The diagrams show the status badge colors.

Diagrams and tables are built from the workflow service's serialized
``TRANSITIONS`` (``workflow.get_workflow_reference``), so they always match
the rules the app enforces. Badge colors and labels come from the reference
tables; if those cannot be read the page still shows everything, in gray,
under a warning with Retry. Every user sees this page.
"""

import logging

import pandas as pd
import streamlit as st

from adapters import cache
from adapters.navigation import HELP_TOPIC_KEY
from adapters.page import ALERT_ICON, current_roles, page_header, require_data_access
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import (
    ADMIN,
    APPROVER,
    GLOSSARY,
    LIFECYCLE_INTRO,
    OWNER_SME,
    QUICK_REFERENCE,
    ROLE_REQUEST,
    ROLES,
    VERSIONING_NOTE,
    BadgeInfo,
    QuickReference,
    combinations_table,
    dp_legend,
    guide_for_topic,
    guides_for,
    op_legend,
    state_diagram_dot,
    transition_table,
)
from onepagerapp.state_machine import Actor
from onepagerapp.workflow import get_workflow_reference

logger = logging.getLogger(__name__)

COLORS_ERROR_MESSAGE = (
    "Couldn't load the status colors. The page is shown without them. Please retry."
)


# ============================================================================
# Helpers
# ============================================================================


def load_legends(data_access: DataAccess) -> tuple[list[BadgeInfo], list[BadgeInfo]]:
    """One Pager and Data Product badges; gray defaults if they cannot be read."""
    try:
        return (
            op_legend(cache.get_ref_op_status(data_access)),
            dp_legend(cache.get_ref_dp_status(data_access)),
        )
    except Exception as e:
        logger.exception("Failed to load the status reference tables")
        st.warning(user_error_message(e, COLORS_ERROR_MESSAGE), icon=ALERT_ICON)
        if st.button("Retry", key="help_retry"):
            st.rerun()
        return op_legend(pd.DataFrame()), dp_legend(pd.DataFrame())


# ============================================================================
# Render Components
# ============================================================================


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


# Help guide roles of the session's roles.
GUIDE_ROLES = {
    Actor.OWNER_SME_GROUP: OWNER_SME,
    Actor.APPROVER: APPROVER,
    Actor.ADMIN: ADMIN,
}


def render_guide(guide: QuickReference, *, expanded: bool = False) -> None:
    """One "How do I…" guide as numbered steps in an expander."""
    with st.expander(guide.title, expanded=expanded):
        st.markdown(
            "\n".join(f"{number}. {step}" for number, step in enumerate(guide.steps, 1))
        )


# ============================================================================
# Help Page
# ============================================================================

data_access = require_data_access()

page_header(
    "Help",
    "How the app works: guides for your role, the lifecycle, roles and terms.",
)

topic_guide = guide_for_topic(st.session_state.pop(HELP_TOPIC_KEY, "") or "")
if topic_guide is not None:
    st.info("Help for the page you came from:", icon=":material/help:")
    render_guide(topic_guide, expanded=True)

op_badges, dp_badges = load_legends(data_access)
reference = get_workflow_reference()

st.header("How do I…")
my_roles = [GUIDE_ROLES[r] for r in GUIDE_ROLES if r in current_roles()]
mine = guides_for(my_roles)
st.caption(
    "Guides for your role" + (f" ({', '.join(my_roles)})" if my_roles else "") + "."
)
for guide in mine:
    if guide != topic_guide:
        render_guide(guide)
others = [q for q in QUICK_REFERENCE if q not in mine]
if others:
    with st.expander(f"Guides for other roles ({len(others)})"):
        for guide in others:
            st.markdown(f"**{guide.title}**")
            st.markdown(
                "\n".join(f"{n}. {step}" for n, step in enumerate(guide.steps, 1))
            )

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

st.header("Glossary")
st.dataframe(
    pd.DataFrame(GLOSSARY, columns=["Term", "Meaning"]),
    hide_index=True,
    use_container_width=True,
    column_config={"Meaning": st.column_config.TextColumn(width="large")},
)

st.caption(
    "Status colors: the lifecycle diagrams above show every status in its badge "
    "color. Being edited by (Registry): who holds the edit lock, and since when."
)
