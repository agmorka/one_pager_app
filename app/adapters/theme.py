"""BEC theming constants and status badge colors."""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from onepagerapp.data_access.base import DataAccess

logger = logging.getLogger(__name__)

TOTAL_CARD_COLOR = "#0c1c49"
DEFAULT_BADGE_COLOR = "#808080"

# Environment badge colors (background, text), UI_Design.md §2-3. PRD uses the
# BEC red accent so production is never mistaken for a test environment.
ENVIRONMENT_BADGE_COLORS: dict[str, tuple[str, str]] = {
    "DEV": ("#808080", "#FFFFFF"),
    "INT": ("#3599B8", "#FFFFFF"),
    "TST": ("#7E57C2", "#FFFFFF"),
    "UAT": ("#F9BD00", "#343333"),
    "PRD": ("#F34421", "#FFFFFF"),
}


def get_op_status_colors(data_access: DataAccess) -> dict[str, str]:
    """Build One Pager status color map from ref_op_status table.

    Args:
        data_access: DataAccess instance to fetch reference data

    Returns:
        Dictionary mapping status strings to hex color codes

    Raises:
        RuntimeError: If the returned data is invalid or missing required columns

    """
    statuses = data_access.get_ref_op_status()
    logger.debug(
        f"ref_op_status returned {len(statuses)} rows "
        f"with columns: {list(statuses.columns)}"
    )

    # Validate required columns exist
    required_columns = {"status", "badge_color"}
    missing_columns = required_columns - set(statuses.columns)
    if missing_columns:
        msg = (
            f"ref_op_status table is missing required columns: {missing_columns}. "
            f"Got columns: {list(statuses.columns)}"
        )
        raise RuntimeError(msg)

    if statuses.empty:
        msg = "ref_op_status table is empty — no statuses available"
        raise RuntimeError(msg)

    return dict(zip(statuses["status"], statuses["badge_color"], strict=False))


def get_dp_status_colors(data_access: DataAccess) -> dict[str, str]:
    """Build Data Product status color map from ref_dp_status table.

    Args:
        data_access: DataAccess instance to fetch reference data

    Returns:
        Dictionary mapping status strings to hex color codes

    Raises:
        RuntimeError: If the returned data is invalid or missing required columns

    """
    statuses = data_access.get_ref_dp_status()
    logger.debug(
        f"ref_dp_status returned {len(statuses)} rows "
        f"with columns: {list(statuses.columns)}"
    )

    # Validate required columns exist
    required_columns = {"status", "badge_color"}
    missing_columns = required_columns - set(statuses.columns)
    if missing_columns:
        msg = (
            f"ref_dp_status table is missing required columns: {missing_columns}. "
            f"Got columns: {list(statuses.columns)}"
        )
        raise RuntimeError(msg)

    if statuses.empty:
        msg = "ref_dp_status table is empty — no statuses available"
        raise RuntimeError(msg)

    return dict(zip(statuses["status"], statuses["badge_color"], strict=False))


def apply_theme() -> None:
    """Apply BEC theming to the Streamlit app."""
    st.markdown(
        """
        <style>
        /* Sidebar in BEC dark blue (UI_Design.md §3); the logo is white. */
        [data-testid="stSidebar"] {
            background-color: #0c1c49;
        }
        [data-testid="stSidebar"] * {
            color: #FFFFFF;
        }
        [data-testid="stSidebar"] a {
            color: #FFFFFF !important;
        }
        [data-testid="stSidebarNavLink"][aria-current="page"],
        [data-testid="stSidebarNavLink"]:hover {
            background-color: rgba(255, 255, 255, 0.15);
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 500;
        }
        .role-badge {
            display: inline-block;
            padding: 1px 8px;
            margin: 0 4px 4px 0;
            border: 1px solid currentColor;
            border-radius: 10px;
            font-size: 0.75em;
            font-weight: 600;
        }
        .environment-badge {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: 700;
            letter-spacing: 0.05em;
        }
        .status-badge-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_badge(status: str, color: str) -> str:
    """Generate HTML for a status badge with colored dot and text label.

    Renders both a colored dot and text label for accessibility—never color alone,
    to support users with color blindness.

    Args:
        status: The status text to display (e.g. "Draft", "Approved")
        color: Hex color code for the dot (e.g. '#65B676')

    Returns:
        HTML string for the badge with colored dot + text

    """
    return (
        f'<span class="status-badge">'
        f'<span class="status-badge-dot" style="background-color: {color};"></span>'
        f"{status}"
        f"</span>"
    )


def environment_badge(environment: str) -> str:
    """Generate HTML for the sidebar environment badge (DEV / INT / UAT / PRD).

    The label is always shown as text, so color is never the only indicator.

    Args:
        environment: Environment name, e.g. "DEV".

    Returns:
        HTML string for the badge.

    """
    background, text = ENVIRONMENT_BADGE_COLORS.get(
        environment, (DEFAULT_BADGE_COLOR, "#FFFFFF")
    )
    return (
        f'<span class="environment-badge" aria-label="Environment: {environment}" '
        f'style="background-color: {background}; color: {text};">'
        f"{environment}"
        f"</span>"
    )


def role_badges(roles: list[str]) -> str:
    """HTML for the user's role badges in the sidebar (UI_Design.md §2).

    Plain text in an outlined pill, so the role never depends on color.
    """
    return "".join(
        f'<span class="role-badge" aria-label="Role: {html.escape(role)}">'
        f"{html.escape(role)}</span>"
        for role in roles
    )
