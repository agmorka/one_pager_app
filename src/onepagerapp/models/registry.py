"""Registry page models: filter, sort, status reference data, rows and pages."""

from dataclasses import dataclass, field
from datetime import datetime

from onepagerapp.models.pagination import Pagination


@dataclass
class RegistryFilter:
    """Filtering criteria for Registry queries.

    All fields are optional (None). A field with value None means "no filter on
    this dimension."
    Multiple filters are combined with AND semantics.

    Attributes:
        product_name: Partial match (case-insensitive) on product name.
        op_status: One Pager document status (e.g. "Draft", "Approved").
        dp_status: Data Product lifecycle status (e.g. "Active", "In Development").
        owner: Partial match on owner name or email.
        domain: Business domain (exact match after normalization).
        data_product_type: Data product type: "Foundational", "Integrated", "Augmented".
        use_case_id: Only One Pagers linked to this Use Case (use_case_references).
        search: Partial match (case-insensitive) on ID, product name, owner
            name or owner email; one search box for all of them.
        authorized_initials: Only One Pagers where these initials are Owner
            or SME (one_pager_authorized_users), e.g. "My One Pagers".
        op_statuses: One Pager status is one of these (e.g. the statuses that
            need the user's action).

    """

    product_name: str | None = None
    op_status: str | None = None
    dp_status: str | None = None
    owner: str | None = None
    domain: str | None = None
    data_product_type: str | None = None
    use_case_id: str | None = None
    search: str | None = None
    authorized_initials: str | None = None
    op_statuses: tuple[str, ...] | None = None


# RegistryRow fields the Registry table can be sorted by (UI_Design.md §4.1).
REGISTRY_SORT_COLUMNS = (
    "one_pager_id",
    "product_name",
    "business_domain",
    "data_product_type",
    "owner_name",
    "one_pager_status",
    "data_product_status",
)


@dataclass(frozen=True)
class RegistrySort:
    """Sort order of a Registry query.

    Ties are broken by one_pager_id (ascending) so pagination stays stable.

    Attributes:
        column: One of REGISTRY_SORT_COLUMNS.
        descending: Sort from highest to lowest.

    """

    column: str = "one_pager_id"
    descending: bool = False

    def __post_init__(self) -> None:
        if self.column not in REGISTRY_SORT_COLUMNS:
            msg = f"Cannot sort the registry by {self.column!r}"
            raise ValueError(msg)

    def toggled(self, column: str) -> "RegistrySort":
        """Return the sort after clicking ``column``.

        The same column flips the direction; another column sorts ascending by it.
        """
        if column == self.column:
            return RegistrySort(column, not self.descending)
        return RegistrySort(column)


@dataclass
class StatusRef:
    """Reference data for a status value.

    Represents a row from ref_op_status or ref_dp_status, used to populate dropdowns,
    filter options, and badge rendering.

    Attributes:
        status: The status enum value (e.g. "Draft", "Approved").
        display_label: Human-readable label for UI display.
        sort_order: Numeric order for dropdown/filter ordering.
        badge_color: Hex color code for status badges (e.g. "#65B676").
        is_terminal: Whether this is a terminal state (no outgoing transitions).

    """

    status: str
    display_label: str
    sort_order: int
    badge_color: str | None = None
    is_terminal: bool = False


@dataclass
class RegistryRow:
    """A single One Pager record for display in the Registry table.

    Attributes:
        one_pager_id: Primary key (e.g. "OP-0001").
        product_name: Human-readable product name.
        business_domain: Business domain (e.g. "Finance", "Operations").
        data_product_type: Type: "Foundational", "Integrated", or "Augmented".
        one_pager_status: Document lifecycle status (e.g. "Draft", "Approved").
        data_product_status: Product lifecycle status (e.g. "Active", "In Development").
        owner_name: Data Product Owner display name.
        owner_email: Owner email address.
        version: Current version (e.g. "1.0.0").
        last_updated_at: Timestamp of last modification.
        last_updated_by: Initials of last modifier.

    """

    one_pager_id: str
    product_name: str
    business_domain: str
    data_product_type: str
    one_pager_status: str
    data_product_status: str
    owner_name: str
    owner_email: str
    version: str
    last_updated_at: datetime
    last_updated_by: str


@dataclass
class RegistryPage(Pagination):
    """Paginated result set from a Registry query.

    Attributes:
        rows: List of RegistryRow objects for the current page.
        total_rows: Total number of rows matching the filter (across all pages).
        page: Current page number (1-indexed).
        page_size: Rows per page.

    """

    rows: list[RegistryRow]
    total_rows: int
    page: int
    page_size: int = field(default=10)
