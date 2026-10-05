"""Registry page — Browse/search/filter all One Pagers (paginated).

The view:
1. Loads and caches reference data (status colors, domains, types)
2. Renders the status cards; clicking a card filters the table to that status
   (clicking it again shows all statuses)
3. Offers quick views (All / Mine / My drafts), one search box (ID, product,
   owner), the two status filters and "More filters" (domain, type, use case)
4. Renders a paginated table; clicking a row opens the One Pager in Preview
5. Handles all page states: Loading, Populated, Empty (no filters), Empty (after
   filter), Error

Per Backend_Design.md §3, all authenticated users can read the registry (no role
filtering in v1).
"""

import logging

import pandas as pd
import streamlit as st

from adapters import cache
from adapters.navigation import open_editor, open_preview
from adapters.page import (
    current_roles,
    current_user,
    page_header,
    render_error_state,
    require_data_access,
    timestamp_label,
)
from adapters.tables import selected_value
from adapters.theme import TOTAL_CARD_COLOR, section_gap
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.help_content import TOPIC_REGISTRY, text_color
from onepagerapp.locking import get_active_locks
from onepagerapp.models import (
    LockInfo,
    RegistryFilter,
    RegistryPage,
    RegistrySort,
    UseCase,
    UseCaseFilter,
)
from onepagerapp.permissions import EDITABLE_STATUSES, can_create_one_pager
from onepagerapp.state_machine import READY_FOR_REVIEW

logger = logging.getLogger(__name__)

REFERENCE_ERROR_MESSAGE = "Couldn't load the reference data. Please retry."
LOAD_ERROR_MESSAGE = "Couldn't load the One Pagers. Please retry."

# Rows per page: the choices and the default.
PAGE_SIZES = [20, 50, 100]
ROWS_PER_PAGE = PAGE_SIZES[0]

# Quick views above the filters.
VIEW_ALL = "All One Pagers"
VIEW_MINE = "My One Pagers"
VIEW_MY_DRAFTS = "My drafts"
VIEWS = [VIEW_ALL, VIEW_MINE, VIEW_MY_DRAFTS]

# Filter state defaults
FILTER_DEFAULTS: dict[str, str] = {
    "filter_view": VIEW_ALL,
    "filter_search": "",
    "filter_op_status": "All",
    "filter_dp_status": "All",
    "filter_domain": "All",
    "filter_type": "All",
    "filter_use_case": "All",
}

# Table column title -> RegistryRow field it sorts by.
SORTABLE_COLUMNS: dict[str, str] = {
    "ID": "one_pager_id",
    "Product": "product_name",
    "Domain": "business_domain",
    "Product type": "data_product_type",
    "Owner": "owner_name",
    "One Pager status": "one_pager_status",
    "Data Product status": "data_product_status",
}

# Table columns in display order.
TABLE_COLUMNS = [*SORTABLE_COLUMNS, "Being edited by"]

STATUS_COLUMNS = ("One Pager status", "Data Product status")

# Use Cases read per query while building the Use Case filter options.
_USE_CASE_OPTIONS_PAGE_SIZE = 200

# Longest Use Case goal shown in a filter option before it is shortened.
_USE_CASE_GOAL_MAX_LENGTH = 60

LOCKS_UNAVAILABLE = "?"
LOCK_ICON = "\N{LOCK}"
TABLE_KEY = "registry_table"


# ============================================================================
# Cached Data
# ============================================================================


@st.cache_data
def _get_cached_business_domains(_data_access: DataAccess) -> pd.DataFrame:
    """Load and cache business domain reference data for the session."""
    return _data_access.get_ref_business_domains()


@st.cache_data
def _get_cached_data_product_types(_data_access: DataAccess) -> pd.DataFrame:
    """Load and cache data product type reference data for the session."""
    return _data_access.get_ref_data_product_types()


# ============================================================================
# Helpers
# ============================================================================


def _render_metric_card(
    label: str, value: int, color: str, *, active: bool = False
) -> str:
    """Generate HTML for a colored metric card (text color by contrast).

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
        f'<div style="background:{color};color:{text_color(color)};{outline}'
        f'height:90px;border-radius:8px 8px 0 0;padding:10px;text-align:center;">'
        f'<div style="font-size:1.8rem;font-weight:bold;">{value}</div>'
        f'<div style="font-size:0.85rem;">{label}</div></div>'
    )


def _reset_page() -> None:
    """Go back to the first page after the filter or the sort order changes."""
    st.session_state.registry_page = 1


def _filter_by_card(status: str) -> None:
    """Card click: filter to ``status``; clicking the active card shows all."""
    current = st.session_state.get("filter_op_status", "All")
    st.session_state["filter_op_status"] = "All" if current == status else status
    _reset_page()


def shown_card_statuses(status_counts: dict, op_status_colors: dict) -> list[str]:
    """Statuses with a card: all but a transient status nobody is in."""
    return [
        status
        for status in op_status_colors
        if status != READY_FOR_REVIEW or status_counts.get(status, 0)
    ]


def _render_metrics(
    status_counts: dict, op_status_colors: dict, active_status: str
) -> None:
    """Status cards: the count on top, a filter button underneath.

    The card of the status filter in effect is outlined; its button reads
    "Showing" and clicking it again clears the status filter.
    """
    statuses = shown_card_statuses(status_counts, op_status_colors)
    metric_cols = st.columns(len(statuses) + 1)
    total = sum(status_counts.values())
    cards = [("All", "Total", total, TOTAL_CARD_COLOR)] + [
        (
            status,
            status,
            status_counts.get(status, 0),
            op_status_colors.get(status, "#808080"),
        )
        for status in statuses
    ]
    for col, (status, label, count, color) in zip(metric_cols, cards, strict=True):
        active = active_status == status
        col.markdown(
            _render_metric_card(label, count, color, active=active),
            unsafe_allow_html=True,
        )
        col.button(
            "✓ Showing" if active else "Show",
            key=f"registry-card-{status}",
            on_click=_filter_by_card,
            args=(status,),
            use_container_width=True,
            type="primary" if active else "secondary",
            help=f"Show only {label} One Pagers" if status != "All" else "Show all",
        )


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


def _init_filters() -> None:
    for key, default in FILTER_DEFAULTS.items():
        st.session_state.setdefault(key, default)


def _render_filter_bar(
    op_status_colors: dict,
    dp_status_colors: dict,
    domain_options: list,
    type_options: list,
    use_case_labels: dict[str, str],
) -> None:
    """Quick view, search, status filters and "More filters"."""
    st.radio(
        "Show",
        VIEWS,
        key="filter_view",
        horizontal=True,
        on_change=_reset_page,
        label_visibility="collapsed",
        help="My One Pagers: where you are Owner or SME.",
    )
    search_col, op_col, dp_col, clear_col = st.columns(
        [3, 1.5, 1.5, 1], vertical_alignment="bottom"
    )
    search_col.text_input(
        "Search",
        key="filter_search",
        on_change=_reset_page,
        placeholder="ID, product name or owner",
    )
    op_col.selectbox(
        "One Pager status",
        options=["All", *op_status_colors],
        key="filter_op_status",
        on_change=_reset_page,
    )
    dp_col.selectbox(
        "Data Product status",
        options=["All", *dp_status_colors],
        key="filter_dp_status",
        on_change=_reset_page,
    )
    clear_col.button("Clear filters", on_click=_clear_filters, use_container_width=True)

    # A remembered Use Case that no longer exists falls back to "All".
    if st.session_state.get("filter_use_case") not in ("All", *use_case_labels):
        st.session_state["filter_use_case"] = "All"
    more_active = sum(
        st.session_state[key] != "All"
        for key in ("filter_domain", "filter_type", "filter_use_case")
    )
    title = f"More filters ({more_active} active)" if more_active else "More filters"
    with st.expander(title, expanded=bool(more_active)):
        domain_col, type_col, use_case_col = st.columns([1, 1, 2])
        domain_col.selectbox(
            "Domain", options=domain_options, key="filter_domain", on_change=_reset_page
        )
        type_col.selectbox(
            "Product type",
            options=type_options,
            key="filter_type",
            on_change=_reset_page,
        )
        use_case_col.selectbox(
            "Use Case",
            options=["All", *use_case_labels],
            format_func=lambda uc_id: use_case_labels.get(uc_id, uc_id),
            key="filter_use_case",
            on_change=_reset_page,
            help="Only One Pagers linked to this Use Case",
        )


def build_filter(initials: str | None) -> RegistryFilter:
    """Build the Registry filter from the filter widgets in session state."""
    state = st.session_state
    view = state.get("filter_view", VIEW_ALL)
    mine = view in (VIEW_MINE, VIEW_MY_DRAFTS) and bool(initials)

    def chosen(key: str) -> str | None:
        value = state.get(key, "All")
        return None if value == "All" else value

    return RegistryFilter(
        search=state.get("filter_search", "").strip() or None,
        op_status=chosen("filter_op_status"),
        dp_status=chosen("filter_dp_status"),
        domain=chosen("filter_domain"),
        data_product_type=chosen("filter_type"),
        use_case_id=chosen("filter_use_case"),
        authorized_initials=initials if mine else None,
        op_statuses=EDITABLE_STATUSES if view == VIEW_MY_DRAFTS else None,
    )


def has_active_filter(registry_filter: RegistryFilter) -> bool:
    """Whether any filter narrows the list."""
    return registry_filter != RegistryFilter()


def _navigate_to_create() -> None:
    """Open the Editor in create mode with a blank form ([+ New One Pager])."""
    # Drop any leftover form state so the new document starts blank.
    for key in [k for k in st.session_state if str(k).startswith("create_")]:
        del st.session_state[key]
    open_editor("create")


def _can_create() -> bool:
    """Whether the signed-in user may create One Pagers (Owner/SME group)."""
    return can_create_one_pager(current_user(), current_roles())


def _render_new_button(key: str) -> None:
    """Render [+ New One Pager] for users allowed to create (UI_Design §4.1)."""
    if _can_create() and st.button(
        ":material/add: New One Pager",
        key=key,
        type="primary",
        use_container_width=True,
        help="Create a new One Pager",
    ):
        _navigate_to_create()


def current_sort() -> RegistrySort:
    """Return the Registry table's sort order, kept in session state."""
    sort = st.session_state.get("registry_sort")
    return sort if isinstance(sort, RegistrySort) else RegistrySort()


def sort_option_label(sort: RegistrySort) -> str:
    """Return the label of a sort order, e.g. "Product (A→Z)"."""
    title = next(t for t, c in SORTABLE_COLUMNS.items() if c == sort.column)
    return f"{title} ({'Z→A' if sort.descending else 'A→Z'})"


SORT_OPTIONS = [
    RegistrySort(column, descending)
    for column in SORTABLE_COLUMNS.values()
    for descending in (False, True)
]


def _sort_changed() -> None:
    st.session_state["registry_sort"] = st.session_state["registry_sort_choice"]
    _reset_page()


def lock_cell(lock: LockInfo | None) -> str:
    """Return the lock cell: lock icon, holder initials and since when."""
    if lock is None:
        return ""
    return f"{LOCK_ICON} {lock.locked_by_initials} since " + timestamp_label(
        lock.acquired_at, "%H:%M"
    )


def _load_locks(
    data_access: DataAccess, registry_page: RegistryPage
) -> dict[str, LockInfo] | None:
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


def table_frame(
    registry_page: RegistryPage, locks: dict[str, LockInfo] | None
) -> pd.DataFrame:
    """Return the rows of the page as the table shows them (one column per title)."""
    records = []
    for row in registry_page.rows:
        values = [getattr(row, field) or "—" for field in SORTABLE_COLUMNS.values()]
        lock = (
            lock_cell(locks.get(row.one_pager_id))
            if locks is not None
            else LOCKS_UNAVAILABLE
        )
        records.append([*values, lock])
    return pd.DataFrame(records, columns=TABLE_COLUMNS)


def tint(color: str, strength: float = 0.3) -> str:
    """Return ``color`` mixed with white, e.g. a light background for text."""
    value = color.lstrip("#")
    try:
        rgb = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return "#FFFFFF"
    mixed = (round(255 - (255 - c) * strength) for c in rgb)
    return "#" + "".join(f"{c:02X}" for c in mixed)


def _styled(frame: pd.DataFrame, colors: dict[str, str]) -> object:
    """Status cells on a light tint of their badge color (text stays dark)."""

    def style(value: object) -> str:
        color = colors.get(str(value))
        return f"background-color: {tint(color)}; color: #1B1B1B;" if color else ""

    return frame.style.map(style, subset=list(STATUS_COLUMNS))


def _render_table(
    registry_page: RegistryPage,
    locks: dict[str, LockInfo] | None,
    colors: dict[str, str],
) -> None:
    """Render the One Pagers of this page; clicking a row opens it in Preview."""
    frame = table_frame(registry_page, locks)
    event = st.dataframe(
        _styled(frame, colors),
        key=TABLE_KEY,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Being edited by": st.column_config.TextColumn(
                help="Who is editing the One Pager now (edit lock), and since when"
            ),
        },
    )
    if locks is None:
        st.caption("Lock status is unavailable right now.")
    one_pager_id = selected_value(event, frame, "ID")
    if one_pager_id:
        # Forget the selection, or coming back would open the same row again.
        st.session_state.pop(TABLE_KEY, None)
        open_preview(one_pager_id)


def page_range_label(registry_page: RegistryPage, page: int, page_size: int) -> str:
    """Return e.g. "Showing 21-40 of 134 One Pagers"."""
    first = (page - 1) * page_size + 1
    last = first + len(registry_page.rows) - 1
    return f"Showing {first}-{last} of {registry_page.total_rows} One Pagers"


def _go_to_page(page: int) -> None:
    st.session_state.registry_page = page


def _page_size_changed() -> None:
    _reset_page()


def _render_pagination(current_page: int, total_pages: int) -> None:
    """Prev / page number / Next, and the rows-per-page choice."""
    prev_col, page_col, next_col, _, size_col = st.columns(
        [1, 1.2, 1, 3, 1.3], vertical_alignment="bottom"
    )
    if total_pages > 1:
        prev_col.button(
            "← Prev",
            disabled=current_page <= 1,
            on_click=_go_to_page,
            args=(current_page - 1,),
            use_container_width=True,
        )
        st.session_state["registry_page_choice"] = current_page
        page_col.selectbox(
            "Page",
            options=list(range(1, total_pages + 1)),
            format_func=lambda p: f"Page {p} of {total_pages}",
            key="registry_page_choice",
            on_change=lambda: _go_to_page(st.session_state["registry_page_choice"]),
            label_visibility="collapsed",
        )
        next_col.button(
            "Next →",
            disabled=current_page >= total_pages,
            on_click=_go_to_page,
            args=(current_page + 1,),
            use_container_width=True,
        )
    size_col.selectbox(
        "Rows per page",
        PAGE_SIZES,
        key="registry_page_size",
        on_change=_page_size_changed,
    )


# ============================================================================
# Page States
# ============================================================================


def _render_page_state_populated(  # noqa: PLR0913 - the page state
    registry_page: RegistryPage,
    total_pages: int,
    current_page: int,
    page_size: int,
    locks: dict[str, LockInfo] | None,
    colors: dict[str, str],
) -> None:
    """Render populated page state with the table and pagination."""
    count_col, sort_col = st.columns([3, 1.3], vertical_alignment="bottom")
    count_col.markdown(
        f"**{page_range_label(registry_page, current_page, page_size)}** · "
        "click a row to open it"
    )
    st.session_state["registry_sort_choice"] = current_sort()
    sort_col.selectbox(
        "Sort by",
        SORT_OPTIONS,
        key="registry_sort_choice",
        format_func=sort_option_label,
        on_change=_sort_changed,
    )
    _render_table(registry_page, locks, colors)
    _render_pagination(current_page, max(total_pages, 1))


def _render_page_state_empty_no_filters() -> None:
    """Render page state when no One Pagers exist and no filters are applied."""
    if _can_create():
        st.info("**No One Pagers yet** — create the first one.")
        _render_new_button("registry_new_empty")
    else:
        st.info("**No One Pagers found.**")


def _render_page_state_empty_with_filters() -> None:
    """Render page state when filters are applied but no results match."""
    st.warning(
        "**No One Pagers match your filters.** Try adjusting your filter criteria."
    )
    st.button("Clear all filters", on_click=_clear_filters, key="registry_clear_empty")


# ============================================================================
# Registry Page
# ============================================================================

data_access = require_data_access()
_init_filters()
st.session_state.setdefault("registry_page_size", ROWS_PER_PAGE)

# Page title and description, with [+ New One Pager] on the right
title_col, new_col = st.columns([5, 1.2], vertical_alignment="bottom")
with title_col:
    page_header(
        "One Pager Registry",
        "Browse, search, and filter all One Pagers.",
        help_topic=TOPIC_REGISTRY,
    )
with new_col:
    _render_new_button("registry_new")

# Load reference data (status colors)
try:
    op_status_colors = cache.op_status_colors(data_access)
    dp_status_colors = cache.dp_status_colors(data_access)
except Exception as e:
    logger.exception("Failed to load reference data")
    render_error_state(
        user_error_message(e, REFERENCE_ERROR_MESSAGE), key="registry_retry_reference"
    )

# Load filter dropdown options
try:
    domains_df = _get_cached_business_domains(data_access)
    domain_options = ["All", *domains_df["domain"].tolist()]
except Exception:
    logger.exception("Failed to load business domains")
    domain_options = ["All"]

try:
    types_df = _get_cached_data_product_types(data_access)
    type_options = ["All", *types_df["type"].tolist()]
except Exception:
    logger.exception("Failed to load data product types")
    type_options = ["All"]

try:
    use_case_labels = {
        uc.use_case_id: use_case_option_label(uc) for uc in _load_use_cases(data_access)
    }
except Exception:
    logger.exception("Failed to load use cases for the registry filter")
    use_case_labels = {}

# Status cards: counts of all One Pagers; a click filters by the status.
st.markdown("**One Pagers by status** (all One Pagers)")
try:
    status_counts = cache.get_registry_status_counts(data_access, RegistryFilter())
except Exception:
    logger.exception("Failed to fetch status counts")
    status_counts = dict.fromkeys(op_status_colors, 0)

_render_metrics(
    status_counts, op_status_colors, st.session_state.get("filter_op_status", "All")
)
section_gap()

_render_filter_bar(
    op_status_colors, dp_status_colors, domain_options, type_options, use_case_labels
)
section_gap()

user = current_user()
current_filter = build_filter(user.initials if user else None)

if "registry_page" not in st.session_state:
    st.session_state.registry_page = 1
page_size = int(st.session_state["registry_page_size"])

# Fetch and render registry data
try:
    with st.spinner("Loading One Pagers..."):
        registry_page = cache.get_registry(
            data_access,
            current_filter,
            st.session_state.registry_page,
            page_size,
            current_sort(),
        )

    total_pages = registry_page.total_pages
    current_page = st.session_state.registry_page
    if not registry_page.rows and current_page > 1 and registry_page.total_rows:
        # Past the last page (e.g. after a bigger page size): go to page 1.
        _reset_page()
        st.rerun()

    if registry_page.rows:
        _render_page_state_populated(
            registry_page,
            total_pages,
            current_page,
            page_size,
            _load_locks(data_access, registry_page),
            {**dp_status_colors, **op_status_colors},
        )
    elif registry_page.total_rows == 0 and not has_active_filter(current_filter):
        _render_page_state_empty_no_filters()
    elif registry_page.total_rows == 0:
        _render_page_state_empty_with_filters()

except Exception as e:
    logger.exception("Failed to fetch registry data")
    render_error_state(
        user_error_message(e, LOAD_ERROR_MESSAGE), key="registry_retry_load"
    )
