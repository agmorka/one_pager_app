"""Content of the Help page (Requirements_and_Scope.md §11, UI_Design.md §4.6).

The lifecycle diagrams and the transition and combination tables are built
from ``workflow.get_workflow_reference()`` (the serialized ``TRANSITIONS`` and
``VALID_COMBINATIONS``), so the Help page always describes the rules the
application enforces. Badge colors and labels come from ``ref_op_status`` /
``ref_dp_status``. Roles and the quick reference are static text.

Pure Python — no Streamlit.
"""

from dataclasses import dataclass

import pandas as pd

from onepagerapp.state_machine import DP_STATUSES, OP_STATUSES

DEFAULT_NODE_COLOR = "#808080"
_DARK_TEXT = "#1B1B1B"
_LIGHT_TEXT = "#FFFFFF"
# Relative luminance above which a badge color gets dark text (UI_Design §3:
# gold #F9BD00 carries dark text, gray #808080 white text).
_LIGHT_BACKGROUND = 0.35

LIFECYCLE_INTRO = (
    "Every One Pager has **two statuses**. The **One Pager status** follows "
    "the document: it is written as a Draft, reviewed by an Approver and "
    "approved. The **Data Product status** follows the product the One Pager "
    "describes: from its definition through development to being active and, "
    "eventually, deprecated. The two move together only where the rules below "
    "say so: approving or cancelling a One Pager changes the Data Product "
    "status automatically, and the Owner moves the Data Product on once its "
    "One Pager is approved."
)

VERSIONING_NOTE = (
    "Versions follow MAJOR.MINOR.PATCH. Creating a One Pager gives 0.1.0, each "
    "Save Draft raises MINOR (0.2.0, 0.3.0, ...), and an approval raises MAJOR "
    "(1.0.0, 2.0.0, ...). Submitting, rejecting and cancelling keep the version."
)


@dataclass(frozen=True)
class RoleInfo:
    role: str
    who: str
    can_do: str


# Requirements_and_Scope.md §2.
ROLES: tuple[RoleInfo, ...] = (
    RoleInfo(
        "Owner / SME",
        "The Data Product Owner, or a Subject Matter Expert assigned to that "
        "Data Product. Edit rights are per Data Product.",
        "Creates and edits the One Pager, manages the shared Use Cases, "
        "submits for review, resolves review comments, starts updates of "
        "approved One Pagers, moves the Data Product status on, cancels.",
    ),
    RoleInfo(
        "Approver",
        "A reviewer from the partner institution.",
        "Approves or rejects One Pagers in review and adds section-level "
        "review comments. Never on a One Pager where they are Owner or SME.",
    ),
    RoleInfo(
        "Admin",
        "A Platform team representative.",
        "Maintains reference data (business domains, product types, source "
        "systems, status definitions), watches pending Git PRs, may cancel a "
        "One Pager.",
    ),
    RoleInfo(
        "Viewer",
        "Any authenticated employee.",
        "Browses, views and exports every One Pager and Use Case (read-only).",
    ),
)


@dataclass(frozen=True)
class QuickReference:
    title: str
    steps: tuple[str, ...]


QUICK_REFERENCE: tuple[QuickReference, ...] = (
    QuickReference(
        "Create a One Pager",
        (
            "Open the **Registry** and choose **New**.",
            "Fill in the Basics (Data Product name, domain, type, Owner, SMEs) "
            "and the Business Problem Statement, then create it.",
            "It starts as **Draft** (version 0.1.0) with the Data Product "
            "**In Definition**.",
        ),
    ),
    QuickReference(
        "Edit and submit for review",
        (
            "In **Preview**, choose **Edit**: the Editor opens and locks the "
            "One Pager for you (the lock expires after 30 minutes without "
            "activity).",
            "Work through the tabs and **Save Draft** with a short change "
            "summary; each save is a new version.",
            "On the **Review** tab, fix everything the checklist lists, then "
            "**Submit for Review**. The One Pager moves to **In Review** and "
            "your lock is released.",
        ),
    ),
    QuickReference(
        "Review (Approvers)",
        (
            "Open the **Review** page: it lists everything In Review, oldest "
            "first. Choose **Review** on a row.",
            "Add section-level comments with **Add Comment** where something "
            "must change.",
            "**Approve** it (version becomes the next MAJOR, the Data Product "
            "status changes automatically) or **Reject** it with a reason "
            "(back to Draft for the Owner).",
        ),
    ),
    QuickReference(
        "Rework after a rejection",
        (
            "Read the review comments in **Preview** or on the Editor's "
            "**Review** tab.",
            "Change the One Pager, **Save Draft**, mark each comment as "
            "resolved and submit again.",
        ),
    ),
    QuickReference(
        "Update an approved One Pager",
        (
            "In **Preview**, choose **Update** and confirm: the One Pager goes "
            "to **Draft Update**; the Data Product keeps its status.",
            "Edit and submit as for a new One Pager. When it is approved "
            "again, the Data Product moves to **In Enhancement**.",
        ),
    ),
    QuickReference(
        "Move the Data Product on",
        (
            "While the One Pager is **Approved**, choose **Change DP Status** "
            "in Preview: Start development, Activate, or Deprecate (asks for "
            "confirmation; Deprecated is final).",
        ),
    ),
    QuickReference(
        "Cancel a One Pager",
        (
            "Only before the first approval (Data Product **In Definition**): "
            "choose **Cancel One Pager** in Preview and confirm. Both statuses "
            "become **Cancelled** for good.",
        ),
    ),
    QuickReference(
        "Export to PDF",
        (
            "Choose **Export PDF** in Preview and download the file. Anyone "
            "can export any One Pager.",
        ),
    ),
)


@dataclass(frozen=True)
class BadgeInfo:
    status: str
    label: str
    color: str
    is_terminal: bool


def status_legend(
    ref_statuses: pd.DataFrame, statuses: tuple[str, ...]
) -> list[BadgeInfo]:
    """Badges of ``statuses`` with the label and color from a ref_*_status table.

    Ordered by the table's ``sort_order``; a status missing from the table is
    listed last with the default color, so the legend is always complete.
    """
    rows = {
        str(r["status"]): r
        for r in ref_statuses.to_dict("records")
        if "status" in r
    }

    def order(status: str) -> tuple[int, int]:
        row = rows.get(status)
        if row is None or row.get("sort_order") is None:
            return (1, statuses.index(status))
        return (0, int(row["sort_order"]))

    legend = []
    for status in sorted(statuses, key=order):
        row = rows.get(status, {})
        legend.append(
            BadgeInfo(
                status=status,
                label=str(row.get("display_label") or status),
                color=str(row.get("badge_color") or DEFAULT_NODE_COLOR),
                is_terminal=_truthy(row.get("is_terminal")),
            )
        )
    return legend


def _truthy(value: object) -> bool:
    return value is True or str(value).lower() == "true"


def text_color(background: str) -> str:
    """Dark or white text, whichever reads better on ``background``."""
    value = background.lstrip("#")
    try:
        r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return _LIGHT_TEXT

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: PLR2004

    luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
    return _DARK_TEXT if luminance > _LIGHT_BACKGROUND else _LIGHT_TEXT


def _dot_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def state_diagram_dot(
    transitions: list[dict[str, object]],
    legend: list[BadgeInfo],
) -> str:
    """Graphviz DOT of one status machine (rendered by ``st.graphviz_chart``).

    Nodes are the statuses in legend order, filled with their badge color and
    labelled with their text (terminal ones double-bordered). Edges are the
    transitions labelled with their action; automatic (system) transitions are
    dashed. Several transitions with the same ends and label share one edge.
    """
    lines = [
        "digraph {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", '
        "fontsize=11];",
        '  edge [fontname="Helvetica", fontsize=9, color="#555555", '
        'fontcolor="#343333"];',
    ]
    for badge in legend:
        peripheries = 2 if badge.is_terminal else 1
        lines.append(
            f"  {_dot_string(badge.status)} [label={_dot_string(badge.label)}, "
            f"fillcolor={_dot_string(badge.color)}, "
            f"fontcolor={_dot_string(text_color(badge.color))}, "
            f"peripheries={peripheries}];"
        )
    seen: set[tuple[str, str, str]] = set()
    for transition in transitions:
        edge = (
            str(transition["from_status"]),
            str(transition["to_status"]),
            str(transition["label"]),
        )
        if edge in seen:
            continue
        seen.add(edge)
        style = ', style="dashed"' if transition["system"] else ""
        lines.append(
            f"  {_dot_string(edge[0])} -> {_dot_string(edge[1])} "
            f"[label={_dot_string(edge[2])}{style}];"
        )
    lines.append("}")
    return "\n".join(lines)


def transition_table(transitions: list[dict[str, object]]) -> pd.DataFrame:
    """Return the transitions as a table: From, To, Action, Who, Conditions."""
    return pd.DataFrame(
        [
            {
                "From": t["from_status"],
                "To": t["to_status"],
                "Action": t["label"],
                "Who": "Automatic" if t["system"] else ", ".join(t["who"]),  # type: ignore[arg-type]
                "Conditions": "; ".join(t["conditions"]) or "-",  # type: ignore[arg-type]
            }
            for t in transitions
        ],
        columns=["From", "To", "Action", "Who", "Conditions"],
    )


def combinations_table(combinations: list[dict[str, object]]) -> pd.DataFrame:
    """Return the valid status combinations: OP status, allowed DP statuses."""
    return pd.DataFrame(
        [
            {
                "One Pager status": c["one_pager_status"],
                "Valid Data Product statuses": ", ".join(
                    c["data_product_statuses"]  # type: ignore[arg-type]
                ),
            }
            for c in combinations
        ],
        columns=["One Pager status", "Valid Data Product statuses"],
    )


def op_legend(ref_op_status: pd.DataFrame) -> list[BadgeInfo]:
    """Badges of every One Pager status."""
    return status_legend(ref_op_status, OP_STATUSES)


def dp_legend(ref_dp_status: pd.DataFrame) -> list[BadgeInfo]:
    """Badges of every Data Product status."""
    return status_legend(ref_dp_status, DP_STATUSES)
