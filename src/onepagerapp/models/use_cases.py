"""Use Cases page models: the shared Use Case registry."""

from dataclasses import dataclass, field
from datetime import datetime

from onepagerapp.models.pagination import Pagination

# Must match the priority enum of every schema in schemas/ (useCases.items.priority
# in v1, definitions.priority in v2).
PRIORITY_OPTIONS: tuple[str, ...] = ("Must Have", "High", "Medium", "Low")


@dataclass
class UseCase:
    """A single Use Case from the shared use_cases registry.

    Attributes:
        use_case_id: Primary key (e.g. "UC-001").
        persona: Role or job title of the consumer.
        goal: What the persona wants to achieve.
        scenario: How they use the data product.
        decision_enabled: What decision/action this makes possible.
        priority: One of PRIORITY_OPTIONS.
        deprecated: Soft-delete flag (deprecated Use Cases can't be linked to
            new One Pagers but stay on the ones that reference them).
        created_by: Initials of the creator.
        created_at: Creation timestamp.
        last_updated_by: Initials of the last editor.
        last_updated_at: Last modification timestamp.
        reference_count: Number of One Pagers referencing this Use Case.

    """

    use_case_id: str
    persona: str
    goal: str
    scenario: str
    decision_enabled: str
    priority: str
    deprecated: bool
    created_by: str
    created_at: datetime
    last_updated_by: str
    last_updated_at: datetime
    reference_count: int = 0


@dataclass
class UseCaseInput:
    """Editable fields of a Use Case, as submitted by the create/edit form."""

    persona: str
    goal: str
    scenario: str
    decision_enabled: str
    priority: str


@dataclass
class UseCaseFilter:
    """Filtering criteria for Use Case registry queries.

    None means "no filter on this dimension". Filters combine with AND semantics.

    Attributes:
        search: Partial, case-insensitive match on persona OR goal.
        priority: Exact match on priority.
        include_deprecated: When False (default), deprecated Use Cases are excluded.

    """

    search: str | None = None
    priority: str | None = None
    include_deprecated: bool = False


@dataclass
class UseCasePage(Pagination):
    """Paginated result set from a Use Case query.

    Attributes:
        rows: UseCase objects for the current page.
        total_rows: Total number of rows matching the filter (across all pages).
        page: Current page number (1-indexed).
        page_size: Rows per page.

    """

    rows: list[UseCase]
    total_rows: int
    page: int
    page_size: int = field(default=10)
