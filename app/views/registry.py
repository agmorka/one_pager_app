"""Registry page — Browse/search/filter all One Pagers (paginated).

Displays a searchable, filterable list of all One Pagers with interactive navigation.

The view:
1. Loads and caches reference data (status colors, domains, types)
2. Renders status metrics showing counts by One Pager status
3. Provides multi-field filter bar (product name, statuses, owner, domain, type)
4. Renders paginated interactive table with clickable ID links
5. Each ID click navigates to the Preview page for that One Pager
6. Handles all page states: Loading, Populated, Empty (no filters), Empty (after filter), Error

Per Backend_Design.md §3, all authenticated users can read the registry (no role filtering in v1).
Per UI_Design.md §4.2, table rows are interactive and ID links navigate to preview on click.
"""

import logging

import streamlit as st

from adapters.theme import (
    TOTAL_CARD_COLOR,
    get_dp_status_colors,
    get_op_status_colors,
)
from onepagerapp.models import RegistryFilter
from onepagerapp.permissions import can_create_one_pager

logger = logging.getLogger(__name__)

# Pagination settings
ROWS_PER_PAGE = 20

# Filter state defaults
FILTER_DEFAULTS: dict[str, str] = {
    "filter_product_name": "",
    "filter_op_status": "All",
    "filter_dp_status": "All",
    "filter_owner": "",
    "filter_domain": "All",
    "filter_type": "All",
}

# Table columns to display
TABLE_COLUMNS = ["ID", "Product", "Domain", "DP Type", "Owner", "OP Status", "DP Status"]


# ============================================================================
# Cached Data Loaders
# ============================================================================

@st.cache_data
def _get_cached_op_status_colors(_data_access):
    """Load and cache One Pager status colors for the session.
    
    Args:
        _data_access: Data access instance (used for cache key).
        
    Returns:
        Dict mapping One Pager status → hex color code.
    """
    return get_op_status_colors(_data_access)


@st.cache_data
def _get_cached_dp_status_colors(_data_access):
    """Load and cache Data Product status colors for the session.
    
    Args:
        _data_access: Data access instance (used for cache key).
        
    Returns:
        Dict mapping Data Product status → hex color code.
    """
    return get_dp_status_colors(_data_access)


@st.cache_data
def _get_cached_business_domains(_data_access):
    """Load and cache business domain reference data for the session.
    
    Args:
        _data_access: Data access instance (used for cache key).
        
    Returns:
        DataFrame with domain reference data.
    """
    return _data_access.get_ref_business_domains()


@st.cache_data
def _get_cached_data_product_types(_data_access):
    """Load and cache data product type reference data for the session.
    
    Args:
        _data_access: Data access instance (used for cache key).
        
    Returns:
        DataFrame with data product type reference data.
    """
    return _data_access.get_ref_data_product_types()


# ============================================================================
# Render Helpers
# ============================================================================

def _render_metric_card(label: str, value: int, color: str) -> str:
    """Generate HTML for a colored metric card.
    
    Args:
        label: Text label for the metric.
        value: Numeric value to display.
        color: Hex color code for the card background.
        
    Returns:
        HTML string for the metric card.
    """
    return (
        f'<div style="background:{color};color:white;'
        f'height:110px;border-radius:8px;padding:12px;text-align:center;">'
        f'<div style="font-size:1.8rem;font-weight:bold;">{value}</div>'
        f'<div style="font-size:0.85rem;">{label}</div></div>'
    )


def _render_metrics(status_counts: dict, op_status_colors: dict) -> None:
    """Render the status metrics row showing counts per One Pager status.
    
    Args:
        status_counts: Dict mapping status → count.
        op_status_colors: Dict mapping status → hex color.
    """
    metric_cols = st.columns(len(op_status_colors) + 1)
    total = sum(status_counts.values())
    metric_cols[0].markdown(
        _render_metric_card("Total", total, TOTAL_CARD_COLOR), unsafe_allow_html=True
    )
    status_counts_enriched = {status: status_counts.get(status, 0) for status in op_status_colors.keys()}
    for col, (status, count) in zip(metric_cols[1:], status_counts_enriched.items(), strict=False):
        color = op_status_colors.get(status, "#808080")
        col.markdown(
            _render_metric_card(status, count, color), unsafe_allow_html=True
        )


def _clear_filters() -> None:
    """Reset all filter inputs to default values."""
    for key, default in FILTER_DEFAULTS.items():
        st.session_state[key] = default


def _render_filter_bar(op_status_colors: dict, dp_status_colors: dict, 
                       domain_options: list, type_options: list) -> tuple:
    """Render the filter bar and return current filter values.
    
    Args:
        op_status_colors: Dict mapping OP status → color.
        dp_status_colors: Dict mapping DP status → color.
        domain_options: List of available domains.
        type_options: List of available data product types.
        
    Returns:
        Tuple of (product_name, op_status, dp_status, owner, domain, data_type) filters.
    """
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    with filter_col1:
        filter_product_name = st.text_input("Product Name", key="filter_product_name")
    with filter_col2:
        filter_op_status = st.selectbox(
            "OP Status", options=["All", *op_status_colors], key="filter_op_status"
        )
    with filter_col3:
        filter_dp_status = st.selectbox(
            "DP Status", options=["All", *dp_status_colors], key="filter_dp_status"
        )
    with filter_col4:
        filter_owner = st.text_input("Owner", key="filter_owner")

    filter_col5, filter_col6, filter_col7, filter_col8 = st.columns(4)
    with filter_col5:
        filter_domain = st.selectbox("Domain", options=domain_options, key="filter_domain")
    with filter_col6:
        filter_type = st.selectbox("Type", options=type_options, key="filter_type")
    with filter_col7:
        st.markdown("")
    with filter_col8:
        st.markdown("")
        st.markdown("")
        st.button("Clear filters", on_click=_clear_filters)

    return filter_product_name, filter_op_status, filter_dp_status, filter_owner, filter_domain, filter_type


def _navigate_to_create() -> None:
    """Open the Editor in create mode with a blank form ([+ New])."""
    # Drop any leftover form state so the new document starts blank.
    for key in [k for k in st.session_state if str(k).startswith("create_")]:
        del st.session_state[key]
    st.session_state["editor_mode"] = "create"
    st.switch_page("views/editor.py")


def _render_new_button(key: str) -> None:
    """Render [+ New] for users allowed to create One Pagers (UI_Design §4.1)."""
    if can_create_one_pager(st.session_state.get("current_user_info")):
        # Button labels are Markdown; a leading "+" would render as a bullet.
        if st.button("➕ New", key=key, type="primary", help="Create a new One Pager"):
            _navigate_to_create()


def _navigate_to_preview(one_pager_id: str) -> None:
    """Navigate to the Preview page for a specific One Pager.

    Args:
        one_pager_id: The ID of the One Pager to preview.
    """
    # session_state survives st.switch_page; query params set here would be cleared.
    st.session_state["preview_one_pager_id"] = one_pager_id
    st.switch_page("views/preview.py")


# Column layout ratios shared by the header and every data row so cells align.
# Extra trailing column reserved for the per-row "View" action button.
_ROW_COLUMN_RATIOS = [1.2, 2.2, 1.4, 1.3, 1.6, 1.3, 1.3, 1.0]

# Prefix used for row-button keys so the CSS below can target only these buttons
_ROW_KEY_PREFIX = "opview-"


def _render_interactive_table(registry_page) -> None:
    """Render registry data as a table with a "View" button on each row.

    Args:
        registry_page: Page object with rows and metadata.
    """
    st.caption("Click the View button on a row to open the One Pager in the Preview page.")

    # Header row
    header_cols = st.columns(_ROW_COLUMN_RATIOS)
    for col, title in zip(header_cols, [*TABLE_COLUMNS, ""], strict=False):
        col.markdown(f"**{title}**")
    st.divider()

    # Data rows: text cells plus a trailing "View" button that navigates to preview
    for row in registry_page.rows:
        values = [
            row.one_pager_id,
            row.product_name,
            row.business_domain,
            row.data_product_type,
            row.owner_name,
            row.one_pager_status,
            row.data_product_status,
        ]
        row_cols = st.columns(_ROW_COLUMN_RATIOS)
        for col, value in zip(row_cols[:-1], values, strict=False):
            col.markdown(str(value) if str(value) else "—")
        with row_cols[-1]:
            if st.button(
                "View",
                key=f"{_ROW_KEY_PREFIX}{row.one_pager_id}",
                use_container_width=True,
                help=f"Open {row.one_pager_id} in Preview",
            ):
                _navigate_to_preview(row.one_pager_id)


def _render_pagination(current_page: int, total_pages: int) -> None:
    """Render pagination controls.
    
    Args:
        current_page: Current page number (1-indexed).
        total_pages: Total number of pages.
    """
    st.markdown("---")
    pag_cols = st.columns([4, 1, 1, 1, 4])
    with pag_cols[1]:
        if st.button("← Prev", disabled=current_page <= 1):
            st.session_state.registry_page = current_page - 1
            st.rerun()
    with pag_cols[2]:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px;'>"
            f"{current_page} / {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with pag_cols[3]:
        if st.button("Next →", disabled=current_page >= total_pages):
            st.session_state.registry_page = current_page + 1
            st.rerun()


# ============================================================================
# Page State Renderers
# ============================================================================

def _render_page_state_populated(registry_page, total_pages: int, current_page: int) -> None:
    """Render populated page state with interactive table and pagination.
    
    Args:
        registry_page: Page object with rows and metadata.
        total_pages: Total number of pages.
        current_page: Current page number.
    """
    st.markdown(f"**Showing {len(registry_page.rows)} of {registry_page.total_rows} One Pagers**")
    
    _render_interactive_table(registry_page)
    
    if total_pages > 1:
        _render_pagination(current_page, total_pages)


def _render_page_state_empty_no_filters() -> None:
    """Render page state when no One Pagers exist and no filters are applied."""
    if can_create_one_pager(st.session_state.get("current_user_info")):
        st.info("📋 **No One Pagers yet** — create the first one.")
        _render_new_button("registry_new_empty")
    else:
        st.info("📋 **No One Pagers found.**")


def _render_page_state_empty_with_filters() -> None:
    """Render page state when filters are applied but no results match."""
    st.warning(
        "🔍 **No One Pagers match your filters.** "
        "Try adjusting your filter criteria."
    )
    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        if st.button("Clear all filters"):
            _clear_filters()
            st.rerun()


# ============================================================================
# Registry Page
# ============================================================================

# Page title and description, with [+ New] on the right
title_col, new_col = st.columns([6, 1])
with title_col:
    st.title("One Pager Registry")
with new_col:
    st.markdown("")
    _render_new_button("registry_new")
st.markdown("Browse, search, and filter all One Pagers.")
st.markdown("")
st.markdown("")

# Initialize data access and load reference data
try:
    data_access = st.session_state.data_access
    op_status_colors = _get_cached_op_status_colors(data_access)
    dp_status_colors = _get_cached_dp_status_colors(data_access)
except RuntimeError as e:
    logger.exception("Failed to load reference data")
    st.error(
        f"**Unable to load reference data:**\n\n{e}\n\n"
        "Please check that:\n"
        "1. All tables have been deployed via Liquibase migrations\n"
        "2. Tables have the required columns: `status`, `display_label`, `sort_order`, `badge_color`, `is_terminal`\n"
        "3. Tables are not empty"
    )
    st.stop()
except KeyError as e:
    logger.exception("Missing required column in reference data")
    st.error(
        f"**Missing column in reference data:** `{e.args[0]}`\n\n"
        "Reference tables must have columns: `status`, `display_label`, `sort_order`, `badge_color`, `is_terminal`"
    )
    st.stop()
except Exception as e:
    logger.exception("Unexpected error loading reference data")
    st.error(
        f"**Unexpected error:** {type(e).__name__}: {e}\n\n"
        "Please check the server logs for more details."
    )
    st.stop()

# Load filter dropdown options
try:
    domains_df = _get_cached_business_domains(data_access)
    domain_options = ["All"] + domains_df["domain"].tolist()
except Exception as e:
    logger.exception("Failed to load business domains")
    domain_options = ["All"]

try:
    types_df = _get_cached_data_product_types(data_access)
    type_options = ["All"] + types_df["type"].tolist()
except Exception as e:
    logger.exception("Failed to load data product types")
    type_options = ["All"]

# Render metrics row
st.markdown("**Status Summary**")
filter_obj = RegistryFilter(
    product_name=None, op_status=None, dp_status=None, 
    owner=None, domain=None, data_product_type=None
)
try:
    status_counts = data_access.get_registry_status_counts(filter_obj)
except Exception as e:
    logger.exception("Failed to fetch status counts")
    status_counts = dict.fromkeys(op_status_colors, 0)

_render_metrics(status_counts, op_status_colors)
st.markdown("")
st.markdown("")

# Render filter bar
st.markdown("**Filters**")
(filter_product_name, filter_op_status, filter_dp_status, 
 filter_owner, filter_domain, filter_type) = _render_filter_bar(
    op_status_colors, dp_status_colors, domain_options, type_options
)
st.markdown("")
st.markdown("")

# Build current filter object
current_filter = RegistryFilter(
    product_name=filter_product_name if filter_product_name else None,
    op_status=filter_op_status if filter_op_status != "All" else None,
    dp_status=filter_dp_status if filter_dp_status != "All" else None,
    owner=filter_owner if filter_owner else None,
    domain=filter_domain if filter_domain != "All" else None,
    data_product_type=filter_type if filter_type != "All" else None,
)

# Check if any filter is active
has_active_filter = any([
    filter_product_name,
    filter_op_status != "All",
    filter_dp_status != "All",
    filter_owner,
    filter_domain != "All",
    filter_type != "All",
])

# Initialize pagination state
if "registry_page" not in st.session_state:
    st.session_state.registry_page = 1

# Fetch and render registry data
try:
    with st.spinner("Loading One Pagers..."):
        registry_page = data_access.get_registry(
            current_filter, st.session_state.registry_page, ROWS_PER_PAGE
        )
    
    total_pages = registry_page.total_pages
    current_page = st.session_state.registry_page
    current_page = min(current_page, total_pages) if total_pages > 0 else 1
    
    # Render appropriate page state
    if registry_page.rows:
        _render_page_state_populated(registry_page, total_pages, current_page)
    elif not has_active_filter and registry_page.total_rows == 0:
        _render_page_state_empty_no_filters()
    elif has_active_filter and registry_page.total_rows == 0:
        _render_page_state_empty_with_filters()

except RuntimeError as e:
    logger.exception("Failed to fetch registry data")
    st.error(
        f"**Unable to load One Pagers**\n\n"
        f"Please check that:\n"
        f"1. All tables have been deployed via Liquibase migrations\n"
        f"2. Tables are accessible and not empty\n"
        f"3. Check server logs for detailed error information"
    )

except Exception as e:
    logger.exception("Unexpected error fetching registry data")
    st.error(
        f"**Unexpected error**\n\n"
        f"An error occurred while loading One Pagers. "
        f"Please check the server logs for details and try again."
    )
