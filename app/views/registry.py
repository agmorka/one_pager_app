"""Registry page — Browse/search/filter all One Pagers (paginated).

Displays a searchable, filterable list of all One Pagers with interactive navigation.

The view:
1. Loads and caches reference data (status colors, domains, types)
2. Renders status metrics showing counts by One Pager status
3. Provides multi-field filter bar (product name, statuses, owner, domain, type,
   use case); clicking a metric card filters the table to that status
4. Renders paginated interactive table with sortable column headers and a View
   button per row
5. Each ID click navigates to the Preview page for that One Pager
6. Handles all page states: Loading, Populated, Empty (no filters), Empty (after filter), Error

Per Backend_Design.md §3, all authenticated users can read the registry (no role filtering in v1).
Per UI_Design.md §4.2, table rows are interactive and ID links navigate to preview on click.
"""

import logging

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from adapters import cache
from adapters.theme import (
    TOTAL_CARD_COLOR,
    get_dp_status_colors,
    get_op_status_colors,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import ReadAccessDeniedError
from onepagerapp.locking import get_active_locks
from onepagerapp.models import (
    LockInfo,
    RegistryFilter,
    RegistrySort,
    UseCase,
    UseCaseFilter,
)
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
    "filter_use_case": "All",
}

# Table columns to display
TABLE_COLUMNS = ["ID", "Product", "Domain", "DP Type", "Owner", "OP Status", "DP Status", "Lock"]

# Table column title -> RegistryRow field it sorts by. Lock is not sortable.
SORTABLE_COLUMNS: dict[str, str] = {
    "ID": "one_pager_id",
    "Product": "product_name",
    "Domain": "business_domain",
    "DP Type": "data_product_type",
    "Owner": "owner_name",
    "OP Status": "one_pager_status",
    "DP Status": "data_product_status",
}

# Use Cases read per query while building the Use Case filter options.
_USE_CASE_OPTIONS_PAGE_SIZE = 200

# Longest Use Case goal shown in a filter option before it is shortened.
_USE_CASE_GOAL_MAX_LENGTH = 60

LOCKS_UNAVAILABLE = "?"


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

def _render_metric_card(
    label: str, value: int, color: str, *, active: bool = False
) -> str:
    """Generate HTML for a colored metric card.
    
    Args:
        label: Text label for the metric.
        value: Numeric value to display.
        color: Hex color code for the card background.
        active: Whether the table is currently filtered by this card.
        
    Returns:
        HTML string for the metric card.
    """
    outline = "outline:3px solid #1B1B1B;outline-offset:2px;" if active else ""
    return (
        f'<div style="background:{color};color:white;{outline}'
        f'height:110px;border-radius:8px;padding:12px;text-align:center;">'
        f'<div style="font-size:1.8rem;font-weight:bold;">{value}</div>'
        f'<div style="font-size:0.85rem;">{label}</div></div>'
    )


def _reset_page() -> None:
    """Go back to the first page after the filter or the sort order changes."""
    st.session_state.registry_page = 1


def _filter_by_status(status: str) -> None:
    """Metric card click: filter the table by that OP status ("All" for Total)."""
    st.session_state["filter_op_status"] = status
    _reset_page()


def _render_metric_button(
    col: DeltaGenerator, label: str, status: str, *, active: bool
) -> None:
    """Render the button under a metric card that applies the card's status filter."""
    col.button(
        "✓ Showing" if active else "Show",
        key=f"registry-metric-{status}",
        on_click=_filter_by_status,
        args=(status,),
        disabled=active,
        use_container_width=True,
        help=(
            "Show all One Pagers"
            if status == "All"
            else f"Show only One Pagers with status {label}"
        ),
    )


def _render_metrics(
    status_counts: dict, op_status_colors: dict, active_status: str
) -> None:
    """Render the status metrics row; each card filters the table by its status.
    
    Args:
        status_counts: Dict mapping status → count.
        op_status_colors: Dict mapping status → hex color.
        active_status: The OP status filter in effect ("All" when none).
    """
    metric_cols = st.columns(len(op_status_colors) + 1)
    total = sum(status_counts.values())
    metric_cols[0].markdown(
        _render_metric_card(
            "Total", total, TOTAL_CARD_COLOR, active=active_status == "All"
        ),
        unsafe_allow_html=True,
    )
    _render_metric_button(
        metric_cols[0], "Total", "All", active=active_status == "All"
    )
    status_counts_enriched = {status: status_counts.get(status, 0) for status in op_status_colors.keys()}
    for col, (status, count) in zip(metric_cols[1:], status_counts_enriched.items(), strict=False):
        color = op_status_colors.get(status, "#808080")
        col.markdown(
            _render_metric_card(status, count, color, active=active_status == status),
            unsafe_allow_html=True,
        )
        _render_metric_button(col, status, status, active=active_status == status)


def _clear_filters() -> None:
    """Reset all filter inputs to default values."""
    for key, default in FILTER_DEFAULTS.items():
        st.session_state[key] = default
    _reset_page()


def _load_use_cases(data_access: DataAccess) -> list[UseCase]:
    """Every Use Case (deprecated ones too), for the Use Case filter options."""
    use_cases: list[UseCase] = []
    page = 1
    while True:
        result = cache.get_use_cases(
            data_access,
            UseCaseFilter(include_deprecated=True),
            page,
            _USE_CASE_OPTIONS_PAGE_SIZE,
        )
        use_cases.extend(result.rows)
        if not result.has_next:
            return use_cases
        page += 1


def use_case_option_label(use_case: UseCase) -> str:
    """Use Case filter option: ID, persona and goal (goal shortened)."""
    goal = use_case.goal
    if len(goal) > _USE_CASE_GOAL_MAX_LENGTH:
        goal = f"{goal[: _USE_CASE_GOAL_MAX_LENGTH - 3]}..."
    label = f"{use_case.use_case_id} · {use_case.persona}: {goal}"
    return f"{label} (deprecated)" if use_case.deprecated else label


def _render_filter_bar(op_status_colors: dict, dp_status_colors: dict, 
                       domain_options: list, type_options: list,
                       use_case_labels: dict[str, str]) -> tuple:
    """Render the filter bar and return current filter values.
    
    Args:
        op_status_colors: Dict mapping OP status → color.
        dp_status_colors: Dict mapping DP status → color.
        domain_options: List of available domains.
        type_options: List of available data product types.
        use_case_labels: Use Case ID → option label ("All" is added here).
        
    Returns:
        Tuple of (product_name, op_status, dp_status, owner, domain, data_type, use_case) filters.
    """
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    with filter_col1:
        filter_product_name = st.text_input(
            "Product Name", key="filter_product_name", on_change=_reset_page
        )
    with filter_col2:
        filter_op_status = st.selectbox(
            "OP Status", options=["All", *op_status_colors], key="filter_op_status",
            on_change=_reset_page,
        )
    with filter_col3:
        filter_dp_status = st.selectbox(
            "DP Status", options=["All", *dp_status_colors], key="filter_dp_status",
            on_change=_reset_page,
        )
    with filter_col4:
        filter_owner = st.text_input("Owner", key="filter_owner", on_change=_reset_page)

    # A remembered Use Case that no longer exists falls back to "All".
    remembered = st.session_state.get("filter_use_case", "All")
    if remembered not in ("All", *use_case_labels):
        st.session_state["filter_use_case"] = "All"

    filter_col5, filter_col6, filter_col7, filter_col8 = st.columns(4)
    with filter_col5:
        filter_domain = st.selectbox(
            "Domain", options=domain_options, key="filter_domain", on_change=_reset_page
        )
    with filter_col6:
        filter_type = st.selectbox(
            "Type", options=type_options, key="filter_type", on_change=_reset_page
        )
    with filter_col7:
        filter_use_case = st.selectbox(
            "Use Case",
            options=["All", *use_case_labels],
            format_func=lambda uc_id: use_case_labels.get(uc_id, uc_id),
            key="filter_use_case",
            on_change=_reset_page,
            help="Only One Pagers linked to this Use Case",
        )
    with filter_col8:
        st.markdown("")
        st.markdown("")
        st.button("Clear filters", on_click=_clear_filters)

    return (filter_product_name, filter_op_status, filter_dp_status, filter_owner,
            filter_domain, filter_type, filter_use_case)


def _navigate_to_create() -> None:
    """Open the Editor in create mode with a blank form ([+ New])."""
    # Drop any leftover form state so the new document starts blank.
    for key in [k for k in st.session_state if str(k).startswith("create_")]:
        del st.session_state[key]
    st.session_state["editor_mode"] = "create"
    st.switch_page("views/editor.py")


def _can_create() -> bool:
    """Whether the signed-in user may create One Pagers (Owner/SME group)."""
    return can_create_one_pager(
        st.session_state.get("current_user_info"),
        st.session_state.get("current_user_roles", frozenset()),
    )


def _render_new_button(key: str) -> None:
    """Render [+ New] for users allowed to create One Pagers (UI_Design §4.1)."""
    if _can_create():
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
_ROW_COLUMN_RATIOS = [1.2, 2.2, 1.4, 1.3, 1.6, 1.3, 1.3, 0.9, 1.0]

# Prefix used for row-button keys so the CSS below can target only these buttons
_ROW_KEY_PREFIX = "opview-"


def current_sort() -> RegistrySort:
    """Return the Registry table's sort order, kept in session state."""
    sort = st.session_state.get("registry_sort")
    return sort if isinstance(sort, RegistrySort) else RegistrySort()


def _sort_by(column: str) -> None:
    """Sort by ``column`` on a header click, or flip the direction if sorted by it."""
    st.session_state["registry_sort"] = current_sort().toggled(column)
    _reset_page()


def sort_header_label(title: str, sort: RegistrySort) -> str:
    """Return the header button text: the title, with ▲/▼ on the sorted column."""
    if SORTABLE_COLUMNS.get(title) != sort.column:
        return title
    return f"{title} {'▼' if sort.descending else '▲'}"


def _render_header_row() -> None:
    """Render the column titles; sortable ones are buttons (UI_Design §4.1)."""
    sort = current_sort()
    header_cols = st.columns(_ROW_COLUMN_RATIOS)
    for col, title in zip(header_cols, [*TABLE_COLUMNS, ""], strict=False):
        column = SORTABLE_COLUMNS.get(title)
        if column is None:
            col.markdown(f"**{title}**" if title else "")
            continue
        if column == sort.column:
            direction = "descending" if sort.descending else "ascending"
            help_text = f"Sorted by {title} ({direction}). Click to reverse."
        else:
            help_text = f"Sort by {title}"
        col.button(
            sort_header_label(title, sort),
            key=f"registry-sort-{column}",
            on_click=_sort_by,
            args=(column,),
            use_container_width=True,
            help=help_text,
        )


def lock_cell(lock: LockInfo | None) -> str:
    """Lock column text: icon plus the holder's initials (UI_Design.md §4.1, §7)."""
    return f"🔒 {lock.locked_by_initials}" if lock else ""


def _load_locks(data_access, registry_page) -> dict[str, LockInfo] | None:
    """Active locks of the rows on this page, read fresh (never cached).

    Returns None when the locks cannot be read; the table still renders.
    """
    try:
        return get_active_locks(
            data_access, [row.one_pager_id for row in registry_page.rows]
        )
    except Exception:
        logger.exception("Failed to fetch locks for the registry page")
        return None


def _render_interactive_table(registry_page, locks: dict[str, LockInfo] | None) -> None:
    """Render registry data as a table with a "View" button on each row.

    Args:
        registry_page: Page object with rows and metadata.
        locks: Active locks by One Pager ID, or None if they could not be read.
    """
    st.caption(
        "Click the View button on a row to open the One Pager in the Preview page. "
        "Click a column title to sort by it; click it again to reverse the order. "
        "🔒 marks a One Pager that is being edited, with the editor's initials."
    )
    if locks is None:
        st.caption("Lock status is unavailable right now.")

    _render_header_row()
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
            lock_cell(locks.get(row.one_pager_id)) if locks is not None else LOCKS_UNAVAILABLE,
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

def _render_page_state_populated(
    registry_page, total_pages: int, current_page: int, locks: dict[str, LockInfo] | None
) -> None:
    """Render populated page state with interactive table and pagination.
    
    Args:
        registry_page: Page object with rows and metadata.
        total_pages: Total number of pages.
        current_page: Current page number.
        locks: Active locks by One Pager ID, or None if unavailable.
    """
    st.markdown(f"**Showing {len(registry_page.rows)} of {registry_page.total_rows} One Pagers**")
    
    _render_interactive_table(registry_page, locks)
    
    if total_pages > 1:
        _render_pagination(current_page, total_pages)


def _render_page_state_empty_no_filters() -> None:
    """Render page state when no One Pagers exist and no filters are applied."""
    if _can_create():
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
except ReadAccessDeniedError as e:
    st.error(str(e))
    st.stop()
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

try:
    use_case_labels = {
        uc.use_case_id: use_case_option_label(uc)
        for uc in _load_use_cases(data_access)
    }
except Exception:
    logger.exception("Failed to load use cases for the registry filter")
    use_case_labels = {}

# Render metrics row
st.markdown("**Status Summary**")
filter_obj = RegistryFilter(
    product_name=None, op_status=None, dp_status=None, 
    owner=None, domain=None, data_product_type=None
)
try:
    status_counts = cache.get_registry_status_counts(data_access, filter_obj)
except Exception as e:
    logger.exception("Failed to fetch status counts")
    status_counts = dict.fromkeys(op_status_colors, 0)

_render_metrics(
    status_counts, op_status_colors, st.session_state.get("filter_op_status", "All")
)
st.markdown("")
st.markdown("")

# Render filter bar
st.markdown("**Filters**")
(filter_product_name, filter_op_status, filter_dp_status, 
 filter_owner, filter_domain, filter_type, filter_use_case) = _render_filter_bar(
    op_status_colors, dp_status_colors, domain_options, type_options, use_case_labels
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
    use_case_id=filter_use_case if filter_use_case != "All" else None,
)

# Check if any filter is active
has_active_filter = any([
    filter_product_name,
    filter_op_status != "All",
    filter_dp_status != "All",
    filter_owner,
    filter_domain != "All",
    filter_type != "All",
    filter_use_case != "All",
])

# Initialize pagination state
if "registry_page" not in st.session_state:
    st.session_state.registry_page = 1

# Fetch and render registry data
try:
    with st.spinner("Loading One Pagers..."):
        registry_page = cache.get_registry(
            data_access,
            current_filter,
            st.session_state.registry_page,
            ROWS_PER_PAGE,
            current_sort(),
        )
    
    total_pages = registry_page.total_pages
    current_page = st.session_state.registry_page
    current_page = min(current_page, total_pages) if total_pages > 0 else 1
    
    # Render appropriate page state
    if registry_page.rows:
        _render_page_state_populated(
            registry_page, total_pages, current_page, _load_locks(data_access, registry_page)
        )
    elif not has_active_filter and registry_page.total_rows == 0:
        _render_page_state_empty_no_filters()
    elif has_active_filter and registry_page.total_rows == 0:
        _render_page_state_empty_with_filters()

except ReadAccessDeniedError as e:
    st.error(str(e))

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
