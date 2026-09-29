"""BEC theming constants and status badge colors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from onepagerapp.data_access.base import DataAccess

logger = logging.getLogger(__name__)

TOTAL_CARD_COLOR = "#0c1c49"
DEFAULT_BADGE_COLOR = "#808080"


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
    logger.debug(f"ref_op_status returned {len(statuses)} rows with columns: {list(statuses.columns)}")
    
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
        raise RuntimeError("ref_op_status table is empty — no statuses available")
    
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
    logger.debug(f"ref_dp_status returned {len(statuses)} rows with columns: {list(statuses.columns)}")
    
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
        raise RuntimeError("ref_dp_status table is empty — no statuses available")
    
    return dict(zip(statuses["status"], statuses["badge_color"], strict=False))


def apply_theme() -> None:
    """Apply BEC theming to the Streamlit app."""
    st.markdown(
        """
        <style>
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 500;
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
        f'{status}'
        f'</span>'
    )
