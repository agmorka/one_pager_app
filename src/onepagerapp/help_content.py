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
        "A member of the Owner/SME group may create One Pagers and manage "
        "Use Cases. Editing is per Data Product: only its Owner and SMEs.",
        "Creates and edits the One Pager, manages the shared Use Cases, "
        "submits for review, resolves review comments, starts updates of "
        "approved One Pagers, moves the Data Product status on, cancels.",
    ),
    RoleInfo(
        "Approver",
        "A member of the Approver group (reviewers from the partner institution).",
        "Approves or rejects One Pagers in review and adds section-level "
        "review comments. Never on a One Pager where they are Owner or SME.",
    ),
    RoleInfo(
        "Admin",
        "A member of the Admin group (Platform team).",
        "Maintains reference data (business domains, product types, source "
        "systems, status definitions), watches pending Git PRs, may cancel a "
        "One Pager.",
    ),
    RoleInfo(
        "Viewer",
        "Every employee who can sign in; no group needed.",
        "Browses, views and exports every One Pager and Use Case (read-only).",
    ),
)


# Who a guide is for: Owner/SME, Approver, Admin, Viewer (everyone).
OWNER_SME = "Owner / SME"
APPROVER = "Approver"
ADMIN = "Admin"
EVERYONE = "Everyone"


@dataclass(frozen=True)
class QuickReference:
    title: str
    steps: tuple[str, ...]
    roles: tuple[str, ...] = (EVERYONE,)
    topic: str = ""


# Roles come from Entra ID groups; the app has no role administration
# (User_Identity_And_Access_Plan.md §4.1 A3, Architecture.md §4).
ROLE_REQUEST = QuickReference(
    "How do I get a role, e.g. become an Approver?",
    (
        "Roles come from Entra ID groups, one per role (Owner/SME, Approver, "
        "Admin). There is nothing to request inside the One Pager App, and "
        "nobody can grant a role in the app.",
        "Ask the team that manages the group to add you, using the usual "
        "group access request. Until the dedicated role groups exist, the "
        "roles are held by the Data Platform Engineering group of the "
        "environment.",
        "Your role badges in the sidebar show what you have. A change applies "
        "from your next session: close the app and open it again.",
    ),
)


# Help topics the pages link to ("? Help" next to a page title).
TOPIC_MY_WORK = "my-work"
TOPIC_REGISTRY = "registry"
TOPIC_PREVIEW = "preview"
TOPIC_EDITOR = "editor"
TOPIC_REVIEW = "review"
TOPIC_USE_CASES = "use-cases"
TOPIC_CREATE = "create"


QUICK_REFERENCE: tuple[QuickReference, ...] = (
    QuickReference(
        "Use My work",
        (
            "**My work** is the first page you see. It lists your drafts, the "
            "review comments you still have to resolve, your One Pagers "
            "waiting for review and, for Approvers, the One Pagers waiting "
            "for your review.",
            "**Edit** continues a draft; **Open** shows a One Pager in Preview; "
            "**Review** opens it with the review actions.",
            "You are on a One Pager's list when you are its Owner or an SME.",
        ),
        topic=TOPIC_MY_WORK,
    ),
    QuickReference(
        "Find a One Pager",
        (
            "Open the **Registry**. Click a status card to see only that "
            "status; click it again to see all.",
            "Type an ID, product name or owner in **Search**, or pick **My "
            "One Pagers** / **My drafts**. Domain, type and Use Case are under "
            "**More filters**.",
            "Click a row to open the One Pager in Preview. **Being edited by** "
            "shows who holds the edit lock, and since when.",
        ),
        topic=TOPIC_REGISTRY,
    ),
    QuickReference(
        "Create a One Pager",
        (
            "Choose **New One Pager** on My work or in the Registry.",
            "Fill in the Basics (Data Product name, domain, type, Owner, SMEs; "
            "**Add me as SME** adds you) and the Business Problem Statement.",
            "**Create and continue editing** opens the new Draft in the Editor "
            "for the other sections; **Create Draft** shows it in Preview. It "
            "starts as **Draft** (version 0.1.0) with the Data Product **In "
            "Definition**.",
        ),
        roles=(OWNER_SME,),
        topic=TOPIC_CREATE,
    ),
    QuickReference(
        "Edit and submit for review",
        (
            "In **Preview**, choose **Edit**: the Editor opens and locks the "
            "One Pager for you. Activity keeps the lock; after a long pause "
            "the Editor warns you and **Keep editing** renews it.",
            "The section list on the left shows ✓ for complete sections and "
            "how many issues each other section has. Click a section to work "
            "on it.",
            "**Save Draft** saves a new version. The change summary is "
            "suggested from the sections you changed; edit it as you like.",
            "When nothing is left to fix, **Submit for Review** (or **Save & "
            "submit for review** with unsaved changes). The One Pager moves "
            "to **In Review** and your lock is released.",
        ),
        roles=(OWNER_SME,),
        topic=TOPIC_EDITOR,
    ),
    QuickReference(
        "Review (Approvers)",
        (
            "Open the **Review** page (or My work): it lists everything In "
            "Review, oldest first, with how long it has waited. Choose "
            "**Review** on a row.",
            "**What changed** shows each section that differs from the last "
            "approved version (or the version you rejected).",
            "Open a section under **Content** and use **Comment on this "
            "section** where something must change (or **Add Comment** for "
            "the whole document).",
            "**Approve** it (the version becomes the next MAJOR, the Data "
            "Product status changes automatically) or **Reject** it with a "
            "reason (back to Draft for the Owner). You return to the queue.",
        ),
        roles=(APPROVER,),
        topic=TOPIC_REVIEW,
    ),
    QuickReference(
        "Rework after a rejection",
        (
            "My work counts the comments you have to resolve. Read them in "
            "**Preview** (Review comments tab) or on the Editor's **Review** "
            "section.",
            "Change the One Pager, mark each comment as resolved and submit again.",
        ),
        roles=(OWNER_SME,),
    ),
    QuickReference(
        "Update an approved One Pager",
        (
            "In **Preview**, choose **Update** and confirm: the One Pager goes "
            "to **Draft Update**; the Data Product keeps its status.",
            "Edit and submit as for a new One Pager. When it is approved "
            "again, the Data Product moves to **In Enhancement**.",
        ),
        roles=(OWNER_SME,),
    ),
    QuickReference(
        "Move the Data Product on",
        (
            "While the One Pager is **Approved**, open **More** in Preview "
            "and choose **Change Data Product status**: Start development, "
            "Activate, or Deprecate (asks for confirmation; Deprecated is "
            "final).",
        ),
        roles=(OWNER_SME,),
    ),
    QuickReference(
        "Cancel a One Pager",
        (
            "Only before the first approval (Data Product **In Definition**): "
            "open **More** in Preview, choose **Cancel One Pager** and "
            "confirm. Both statuses become **Cancelled** for good.",
        ),
        roles=(OWNER_SME, ADMIN),
    ),
    QuickReference(
        "Read a One Pager",
        (
            "Preview shows where the One Pager is on its path, the actions "
            "you can take (the main one highlighted; more under **More**) and, "
            "when an action is unavailable, why.",
            "The tabs hold the content (**Expand all sections** opens every "
            "section; empty ones say so), what changed since the last "
            "approval, the change log and the review comments.",
        ),
        topic=TOPIC_PREVIEW,
    ),
    QuickReference(
        "Work with Use Cases",
        (
            "The **Use Cases** page lists the shared Use Cases. Click a row to "
            "see its details beside the table, with the One Pagers that use "
            "it (each opens in Preview).",
            "Owners/SMEs create, edit, deprecate and restore Use Cases; in the "
            "Editor's **Use Cases** section they link them to a One Pager.",
        ),
        topic=TOPIC_USE_CASES,
    ),
    QuickReference(
        "Export to PDF",
        (
            "Open **More** in Preview, choose **Export PDF** and download the "
            "file. Anyone can export any One Pager.",
        ),
    ),
)


def guides_for(roles: tuple[str, ...] | list[str]) -> list[QuickReference]:
    """Return the guides for these roles (plus those for everyone), in order."""
    wanted = {*roles, EVERYONE}
    return [q for q in QUICK_REFERENCE if wanted & set(q.roles)]


def guide_for_topic(topic: str) -> QuickReference | None:
    """Return the guide a page's "? Help" link points to, if any."""
    if not topic:
        return None
    return next((q for q in QUICK_REFERENCE if q.topic == topic), None)


# Terms and abbreviations used in the app.
GLOSSARY: tuple[tuple[str, str], ...] = (
    (
        "One Pager (OP)",
        "The one-page description of a Data Product: what it is, why it is "
        "needed, its sources, data elements, classification and governance.",
    ),
    (
        "Data Product (DP)",
        "The data asset a One Pager describes. Its status (In Definition, In "
        "Development, Active, …) follows the product, not the document.",
    ),
    (
        "One Pager status",
        "Where the document is: Draft, In Review, Approved, Draft Update "
        "(an approved One Pager being changed) or Cancelled.",
    ),
    (
        "Draft Update",
        "An approved One Pager that is being updated. The approved version "
        "stays valid until the update is approved.",
    ),
    ("Owner", "The Data Product Owner, accountable for the Data Product."),
    (
        "SME",
        "Subject Matter Expert. Owners and SMEs of a One Pager may edit it.",
    ),
    (
        "Approver",
        "A reviewer who approves or rejects One Pagers in review, never their own.",
    ),
    (
        "Corporate initials",
        "The 3-character code from your username (x0wadm@… → X0W). Edit "
        "rights are matched on it.",
    ),
    (
        "Use Case (UC)",
        "A shared description of who uses data for which decision; One "
        "Pagers link to them.",
    ),
    ("Business Requirement (BR)", "A numbered requirement (BR-001, …)."),
    (
        "CDE",
        "Critical Data Element: a data element whose quality and lineage are "
        "governed closely.",
    ),
    ("PII", "Personally Identifiable Information."),
    (
        "Edit lock",
        "Only one person edits a One Pager at a time. Opening the Editor "
        "takes the lock; it is released when you close the Editor or submit, "
        "and expires after 30 minutes without activity.",
    ),
    (
        "Version",
        "MAJOR.MINOR.PATCH: each Save Draft raises MINOR, each approval MAJOR.",
    ),
    (
        "Change summary",
        "A short note saved with each version; it becomes the change-log entry.",
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
        str(r["status"]): r for r in ref_statuses.to_dict("records") if "status" in r
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
