"""Domain models for the Registry page.

These models define the structure of data flowing through the application layers:
- RegistryFilter: parameters for querying One Pagers
- StatusRef: reference data for status values (display label, color, sort order)
- RegistryRow: a single One Pager row for display in the Registry table
- RegistryPage: paginated result set with total count
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RegistryFilter:
    """Filtering criteria for Registry queries.
    
    All fields are optional (None). A field with value None means "no filter on this dimension."
    Multiple filters are combined with AND semantics.
    
    Attributes:
        product_name: Partial match (case-insensitive) on product name.
        op_status: One Pager document status (e.g. "Draft", "Approved").
        dp_status: Data Product lifecycle status (e.g. "Active", "In Development").
        owner: Partial match on owner name or email.
        domain: Business domain (exact match after normalization).
        data_product_type: Data product type: "Foundational", "Integrated", "Augmented".
    """

    product_name: Optional[str] = None
    op_status: Optional[str] = None
    dp_status: Optional[str] = None
    owner: Optional[str] = None
    domain: Optional[str] = None
    data_product_type: Optional[str] = None


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
    badge_color: Optional[str] = None
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
class RegistryPage:
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

    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.page_size <= 0:
            return 0
        return (self.total_rows + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        """Whether there is a next page."""
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        """Whether there is a previous page."""
        return self.page > 1

    @property
    def offset(self) -> int:
        """Calculate SQL OFFSET for this page (0-indexed)."""
        return (self.page - 1) * self.page_size


# ============================================================================
# Preview Page Models
# ============================================================================


@dataclass
class OnePagerHeader:
    """Header metadata for a One Pager preview (from one_pager_status table).
    
    Attributes:
        one_pager_id: Unique identifier (e.g. "OP-0001").
        product_name: Human-readable product name.
        owner_name: Data Product Owner full name.
        owner_initials: Owner initials (used for permissions).
        owner_email: Owner email address.
        version: Current version (MAJOR.MINOR.PATCH).
        one_pager_status: Document lifecycle status (Draft, Ready for Review, etc.).
        data_product_status: Product lifecycle status (In Development, Active, etc.).
        created_at: When the One Pager was first created.
        last_updated_at: Last modification timestamp.
        last_updated_by: Initials of last modifier.
    """

    one_pager_id: str
    product_name: str
    owner_name: str
    owner_initials: str
    owner_email: str
    version: str
    one_pager_status: str
    data_product_status: str
    created_at: datetime
    last_updated_at: datetime
    last_updated_by: str


@dataclass
class ChangeLogEntry:
    """A single entry in the change log (from change_log table).
    
    Represents an immutable audit trail event: content save, status transition, creation, etc.
    
    Attributes:
        id: Unique identifier (auto-generated identity).
        one_pager_id: Foreign key to one_pager_status.
        version: Document version at the time of this entry.
        event_type: Kind of event (content_save, status_transition, creation, cancellation).
        author_initials: Who made the change.
        author_name: Display name of author.
        summary: Human-readable description of the change.
        from_status: Previous status (only for status_transition events).
        to_status: New status (only for status_transition events).
        status_field: Which status changed (one_pager_status or data_product_status).
        created_at: Event timestamp.
    """

    id: int
    one_pager_id: str
    version: str
    event_type: str
    author_initials: str
    author_name: str
    summary: str
    created_at: datetime
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    status_field: Optional[str] = None


@dataclass
class ReviewComment:
    """A single review comment (from review_comments table).
    
    Represents feedback on a specific section of the One Pager during review workflow.
    
    Attributes:
        id: Unique identifier (auto-generated identity).
        one_pager_id: Foreign key to one_pager_status.
        version: Document version when comment was added.
        section: Schema section name (e.g. businessProblemStatement, useCases) or None for doc-level.
        reviewer_initials: Comment author's initials.
        reviewer_name: Display name of reviewer.
        comment: Comment text.
        resolved: Whether owner marked as addressed.
        resolved_by: Initials of person who resolved (nullable).
        created_at: When the comment was added.
        resolved_at: When marked as resolved (nullable).
    """

    id: int
    one_pager_id: str
    version: str
    section: Optional[str]
    reviewer_initials: str
    reviewer_name: str
    comment: str
    resolved: bool
    created_at: datetime
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None


@dataclass
class LockInfo:
    """Lock information for a One Pager (from locks table).
    
    Represents a pessimistic edit lock — one lock per One Pager max.
    
    Attributes:
        one_pager_id: The locked One Pager.
        locked_by_initials: Current lock holder's initials.
        locked_by_name: Lock holder's display name.
        session_id: Streamlit session hash (for multi-tab detection).
        acquired_at: When the lock was first acquired.
        last_heartbeat: Last Streamlit re-run timestamp (lock keepalive).
        expires_at: Auto-expiry time (last_heartbeat + 30 minutes).
    """

    one_pager_id: str
    locked_by_initials: str
    locked_by_name: str
    session_id: str
    acquired_at: datetime
    last_heartbeat: datetime
    expires_at: datetime


@dataclass
class OnePagerDocument:
    """The complete One Pager document content (from YAML in volume or Git).

    Mirrors the fields of structure_one_pager_v_1.json so the document can be
    round-tripped (read and written) without loss. In v1, displayed as text
    (never unsafe_allow_html).

    Attributes:
        structure_definition: Schema version reference.
        data_product: Unique registered name (immutable).
        product_name: Human-readable name.
        business_domain: Organizational domain.
        data_product_type: Foundational | Integrated | Augmented.
        one_pager_status: Document lifecycle status.
        data_product_status: Data product lifecycle status.
        version: Document version (MAJOR.MINOR.PATCH).
        description: Product description.
        owner_name: Data Product Owner name.
        owner_initials: Owner initials.
        owner_email: Owner email.
        owner_team: Owner team (nullable).
        business_problem_statement: Free-text problem statement.
        smes: List of SME objects (name/initials/email/team).
        use_cases: List of use case objects (persona/goal/scenario/...).
        business_requirements: List of requirement objects (requirement/priority).
        data_sources: List of data source objects (sourceName/sourceType/description).
        data_element_preview: List of data element objects (elementName/dataType/...).
        data_classification: Dict with classificationLevel and sensitivity flags.
        created_by: Display name of the user who created the One Pager.
        created_at: ISO-8601 creation timestamp (as stored in YAML).
        last_updated: ISO-8601 timestamp of the last content change.
        change_log: Denormalized copy of the change log (version/date/author/summary).
        raw_content: Complete raw YAML as string (read cache / fallback display).
    """

    structure_definition: str
    data_product: str
    product_name: str
    business_domain: str
    data_product_type: str
    one_pager_status: str
    data_product_status: str
    version: str
    description: str
    owner_name: str
    owner_initials: str
    owner_email: str
    owner_team: Optional[str]
    business_problem_statement: str = ""
    smes: list[dict] = field(default_factory=list)
    use_cases: list[dict] = field(default_factory=list)
    business_requirements: list[dict] = field(default_factory=list)
    data_sources: list[dict] = field(default_factory=list)
    data_element_preview: list[dict] = field(default_factory=list)
    data_classification: dict = field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    last_updated: Optional[str] = None
    change_log: list[dict] = field(default_factory=list)
    raw_content: str = ""


@dataclass
class PreviewData:
    """Composed data for the Preview page (all sources).
    
    Aggregates metadata, content, change log, review comments, and lock state
    from multiple Delta tables and the volume/Git document store.
    
    Attributes:
        header: OnePagerHeader from one_pager_status.
        document: OnePagerDocument from volume/Git YAML.
        change_log: List of ChangeLogEntry (newest-first).
        review_comments: List of ReviewComment.
        lock: LockInfo if currently locked, None otherwise.
    """

    header: OnePagerHeader
    document: OnePagerDocument
    change_log: list[ChangeLogEntry] = field(default_factory=list)
    review_comments: list[ReviewComment] = field(default_factory=list)
    lock: Optional[LockInfo] = None


# ============================================================================
# Create One Pager Models
# ============================================================================


@dataclass
class PersonRef:
    """A person referenced in a One Pager (Data Product Owner or SME).

    Mirrors the ``dataProductOwner`` / ``smes`` item shape of the JSON Schema.
    ``initials`` is the authorization key; ``email`` is for display only.
    """

    name: str
    initials: str
    email: str
    team: Optional[str] = None


@dataclass
class CurrentUser:
    """The authenticated user of the current session.

    Attributes:
        username: Raw identity from Databricks (e.g. "MJOADM@BECOC001.onmicrosoft.com").
        initials: Corporate initials derived from the username (authorization key).
        display_name: Human-readable name used in change log / YAML ``createdBy``.
    """

    username: str
    initials: str
    display_name: str


@dataclass
class NewOnePagerInput:
    """User input for creating a new One Pager (Editor, create mode)."""

    data_product: str
    product_name: str
    business_domain: str
    data_product_type: str
    description: str
    owner: PersonRef
    smes: list[PersonRef] = field(default_factory=list)
    business_problem_statement: str = ""


@dataclass
class ValidationError:
    """A single validation problem, reported (not raised) per Backend_Design §4.

    Attributes:
        field_path: Dotted path of the offending field (e.g. "dataProductOwner.email",
            "smes[1].initials"); "" for form-level errors.
        message: User-facing message.
    """

    field_path: str
    message: str


@dataclass
class CreateResult:
    """Outcome of a create attempt.

    Either ``one_pager_id`` is set (success) or ``errors`` is non-empty
    (validation failed, nothing was written).
    """

    one_pager_id: Optional[str] = None
    version: Optional[str] = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the One Pager was created."""
        return self.one_pager_id is not None and not self.errors


@dataclass
class OnePagerStatusRow:
    """A full row of the ``one_pager_status`` Delta table (Data_Model §3)."""

    one_pager_id: str
    data_product: str
    product_name: str
    business_domain: str
    data_product_type: str
    one_pager_status: str
    data_product_status: str
    version: str
    owner_name: str
    owner_initials: str
    owner_email: str
    owner_team: Optional[str]
    created_by: str
    created_at: datetime
    last_updated_at: datetime
    last_updated_by: str
    structure_definition: str
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    pending_pr: bool = False

    def to_registry_row(self) -> RegistryRow:
        """Project to the Registry table row."""
        return RegistryRow(
            one_pager_id=self.one_pager_id,
            product_name=self.product_name,
            business_domain=self.business_domain,
            data_product_type=self.data_product_type,
            one_pager_status=self.one_pager_status,
            data_product_status=self.data_product_status,
            owner_name=self.owner_name,
            owner_email=self.owner_email,
            version=self.version,
            last_updated_at=self.last_updated_at,
            last_updated_by=self.last_updated_by,
        )

    def to_header(self) -> OnePagerHeader:
        """Project to the Preview header."""
        return OnePagerHeader(
            one_pager_id=self.one_pager_id,
            product_name=self.product_name,
            owner_name=self.owner_name,
            owner_initials=self.owner_initials,
            owner_email=self.owner_email,
            version=self.version,
            one_pager_status=self.one_pager_status,
            data_product_status=self.data_product_status,
            created_at=self.created_at,
            last_updated_at=self.last_updated_at,
            last_updated_by=self.last_updated_by,
        )


@dataclass
class AuthorizedUser:
    """A row of ``one_pager_authorized_users`` (role is "owner" or "sme")."""

    one_pager_id: str
    user_initials: str
    user_name: str
    user_email: str
    role: str
    user_team: Optional[str] = None
