"""One Pager models: the document, its status row, change log, comments, lock.

Used by the Preview page, the Editor and the workflow services.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from onepagerapp.models.registry import RegistryRow


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

    Represents an immutable audit trail event: content save, status transition,
    creation, etc.

    Attributes:
        id: Unique identifier (auto-generated identity).
        one_pager_id: Foreign key to one_pager_status.
        version: Document version at the time of this entry.
        event_type: Kind of event (content_save, status_transition, creation,
            cancellation).
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
    from_status: str | None = None
    to_status: str | None = None
    status_field: str | None = None


@dataclass
class ReviewComment:
    """A single review comment (from review_comments table).

    Represents feedback on a specific section of the One Pager during review workflow.

    Attributes:
        id: Unique identifier (auto-generated identity).
        one_pager_id: Foreign key to one_pager_status.
        version: Document version when comment was added.
        section: Schema section name (e.g. businessProblemStatement, useCases) or
            None for a comment on the whole document.
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
    section: str | None
    reviewer_initials: str
    reviewer_name: str
    comment: str
    resolved: bool
    created_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None


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

    Mirrors the fields of the current schema (structure_one_pager_v_2.json) so
    the document can be round-tripped (read and written) without loss. Older
    documents (v1) load into the same model; see documents/serialization.py.
    In v1, displayed as text (never unsafe_allow_html).

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
        use_cases: Use Case references ({"useCaseId": "UC-###"}); v1 documents
            hold inline objects (persona/goal/...) instead.
        business_requirements: Requirement objects (id BR-###/requirement/
            priority/notes).
        data_sources: Data source objects (name/sourceSystem/epoId/
            dataProvided/refreshFrequency).
        data_product_preview: Data element grid (elementName/dataType/
            isPrimaryKey/containsPII/isCriticalDataElement/cdeCriticalityTiering/
            description/example/source/useCaseLinks).
        data_classification: Dict with classificationLevel and sensitivity flags.
        retention_requirements: Retention objects (dataCategory/retentionPeriod/
            legalBasis).
        data_governance_artifacts: Dict with businessConcepts, cdeQuality and
            cdeLineage lists.
        out_of_scope: Items explicitly out of scope.
        open_questions: Question objects (question/owner/dueDate/status/answer).
        assumptions: Assumptions the One Pager relies on.
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
    owner_team: str | None
    business_problem_statement: str = ""
    smes: list[dict[str, Any]] = field(default_factory=list)
    use_cases: list[dict[str, Any]] = field(default_factory=list)
    business_requirements: list[dict[str, Any]] = field(default_factory=list)
    data_sources: list[dict[str, Any]] = field(default_factory=list)
    data_product_preview: list[dict[str, Any]] = field(default_factory=list)
    data_classification: dict[str, Any] = field(default_factory=dict)
    retention_requirements: list[dict[str, Any]] = field(default_factory=list)
    data_governance_artifacts: dict[str, Any] = field(default_factory=dict)
    out_of_scope: list[str] = field(default_factory=list)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    created_by: str | None = None
    created_at: str | None = None
    last_updated: str | None = None
    change_log: list[dict[str, Any]] = field(default_factory=list)
    raw_content: str = ""

    @property
    def use_case_ids(self) -> list[str]:
        """IDs of the referenced Use Cases, in document order."""
        return [str(uc["useCaseId"]) for uc in self.use_cases if uc.get("useCaseId")]


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
    lock: LockInfo | None = None


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
    owner_team: str | None
    created_by: str
    created_at: datetime
    last_updated_at: datetime
    last_updated_by: str
    structure_definition: str
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
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
    user_team: str | None = None
