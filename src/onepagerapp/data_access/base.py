"""Abstract base class for data access."""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    LockInfo,
    OnePagerDocument,
    OnePagerHeader,
    OnePagerStatusRow,
    PreviewData,
    RegistryFilter,
    RegistryPage,
    RegistrySort,
    ReviewComment,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)


class NotFoundError(LookupError):
    """The requested record does not exist."""


# Admin-managed reference tables (Data_Model.md §7) → their key column. Each
# row has the key, ``sort_order`` and ``active``. Only these names are ever
# formatted into SQL.
REFERENCE_TABLES: dict[str, str] = {
    "ref_business_domains": "domain",
    "ref_data_product_types": "type",
    "ref_source_systems": "system_name",
}

# Status definition tables (Data_Model.md §3): the statuses themselves are
# fixed by the state machine; only their display columns are editable.
STATUS_TABLES: tuple[str, ...] = ("ref_op_status", "ref_dp_status")


def check_reference_table(table: str) -> str:
    """Return the key column of an Admin-managed reference table.

    Raises:
        ValueError: ``table`` is not one of ``REFERENCE_TABLES``.

    """
    if table not in REFERENCE_TABLES:
        msg = f"Unknown reference table {table!r}"
        raise ValueError(msg)
    return REFERENCE_TABLES[table]


def check_status_table(table: str) -> None:
    """Raise ``ValueError`` unless ``table`` is one of ``STATUS_TABLES``."""
    if table not in STATUS_TABLES:
        msg = f"Unknown status table {table!r}"
        raise ValueError(msg)


class DataAccess(ABC):
    """Interface for accessing application data."""

    @property
    def cache_scope(self) -> str:
        """Key of the data this instance reads, for the app's shared caches.

        Instances with the same scope read the same data, so they may share
        cached results. The default is unique per instance (in-memory data).
        """
        scope = getattr(self, "_cache_scope", None)
        if scope is None:
            scope = f"{type(self).__name__}:{uuid.uuid4().hex}"
            self._cache_scope = scope
        return scope

    @abstractmethod
    def get_current_user(self) -> str: ...

    @abstractmethod
    def get_group_memberships(self, groups: dict[str, str]) -> dict[str, bool]:
        """Whether the signed-in user is a member of each group.

        Args:
            groups: Group name per key, e.g. ``{"approver": "OPA-Approver"}``.

        Returns:
            Membership per key. Account groups (Entra ID groups through
            automatic identity management, including nested groups) and
            workspace-local groups both count.

        """
        ...

    @abstractmethod
    def read_table(self, table_name: str) -> pd.DataFrame: ...

    @abstractmethod
    def get_ref_op_status(self) -> pd.DataFrame:
        """Get One Pager statuses with display labels and badge colors.

        Returns DataFrame with columns: status, display_label, sort_order,
        badge_color, is_terminal
        """
        ...

    @abstractmethod
    def get_ref_dp_status(self) -> pd.DataFrame:
        """Get Data Product statuses with display labels and badge colors.

        Returns DataFrame with columns: status, display_label, sort_order,
        badge_color, is_terminal
        """
        ...

    @abstractmethod
    def get_ref_business_domains(self) -> pd.DataFrame:
        """Get the business domain values (active and inactive).

        Returns DataFrame with columns: domain, sort_order, active
        """
        ...

    @abstractmethod
    def get_ref_data_product_types(self) -> pd.DataFrame:
        """Get the data product type values (active and inactive).

        Returns DataFrame with columns: type, sort_order, active
        """
        ...

    @abstractmethod
    def get_ref_source_systems(self) -> pd.DataFrame:
        """Get the known source systems (active and inactive).

        Returns DataFrame with columns: system_name, sort_order, active
        """
        ...

    def get_reference_values(self, table: str) -> pd.DataFrame:
        """Rows of one ``REFERENCE_TABLES`` table, by its name."""
        check_reference_table(table)
        readers = {
            "ref_business_domains": self.get_ref_business_domains,
            "ref_data_product_types": self.get_ref_data_product_types,
            "ref_source_systems": self.get_ref_source_systems,
        }
        return readers[table]()

    # ========================================================================
    # Admin: Reference Data (UI_Design.md §4.7)
    # ========================================================================

    @abstractmethod
    def insert_reference_value(
        self,
        table: str,
        value: str,
        *,
        sort_order: int,
        active: bool,
        user_initials: str,
    ) -> bool:
        """Add a row to a ``REFERENCE_TABLES`` table, unless the key exists.

        ``user_initials`` is stored in ``last_updated_by`` (with the time in
        ``last_updated_at``), because writes run as the service principal.

        Returns:
            True if the row was added, False if a row with this key exists.

        """
        ...

    @abstractmethod
    def update_reference_value(
        self,
        table: str,
        value: str,
        *,
        sort_order: int,
        active: bool,
        user_initials: str,
    ) -> bool:
        """Change ``sort_order`` and ``active`` of a reference row.

        Also sets ``last_updated_by`` to ``user_initials`` and
        ``last_updated_at`` to now.

        Returns:
            True if the row exists and was updated, False otherwise.

        """
        ...

    @abstractmethod
    def delete_reference_value(self, table: str, value: str) -> bool:
        """Delete a reference row.

        No row is left to record the actor; the caller logs the deletion as a
        security event (Data_Model.md §5).

        Returns:
            True if a row was deleted, False if it did not exist.

        """
        ...

    @abstractmethod
    def update_status_definition(  # noqa: PLR0913 - the display columns of one status
        self,
        table: str,
        status: str,
        *,
        display_label: str,
        sort_order: int,
        badge_color: str,
        user_initials: str,
    ) -> bool:
        """Change the display columns of a ``STATUS_TABLES`` row.

        ``status`` and ``is_terminal`` never change: the state machine owns
        them. Also sets ``last_updated_by`` to ``user_initials`` and
        ``last_updated_at`` to now.

        Returns:
            True if the row exists and was updated, False otherwise.

        """
        ...

    @abstractmethod
    def get_registry(
        self,
        filter: RegistryFilter,  # noqa: A002 - matches DataAccess
        page: int,
        page_size: int,
        sort: RegistrySort | None = None,
    ) -> RegistryPage:
        """Query One Pagers with filtering, sorting and pagination.

        Applies filters with AND semantics. Returns a RegistryPage with the
        requested page of results and the total count across all matching rows.

        Args:
            filter: RegistryFilter with optional criteria (None = no filter on
                that dimension).
            page: 1-indexed page number.
            page_size: Number of rows per page.
            sort: Sort order; None sorts by one_pager_id ascending.

        Returns:
            RegistryPage containing rows for the requested page, total row count,
            and pagination info.

        """
        ...

    @abstractmethod
    def get_registry_status_counts(self, filter: RegistryFilter) -> dict[str, int]:  # noqa: A002
        """Aggregate count of One Pagers by one_pager_status.

        Applies the same filter as get_registry(), then groups by one_pager_status
        to produce status counts for metric cards.

        Args:
            filter: RegistryFilter with optional criteria (None = no filter on
                that dimension).

        Returns:
            Dictionary mapping status values (e.g. "Draft", "Approved") to counts.

        """
        ...

    # ========================================================================
    # Preview Page Methods
    # ========================================================================

    @abstractmethod
    def get_one_pager(self, one_pager_id: str) -> PreviewData | None:
        """Fetch a complete One Pager for preview display.

        Composes data from multiple sources:
        - Header metadata from one_pager_status table
        - Document content from YAML (volume for pre-approval, Git for approved)
        - Change log from change_log table (newest-first)
        - Review comments from review_comments table
        - Lock status from locks table

        Args:
            one_pager_id: The One Pager identifier (e.g. "OP-0001").

        Returns:
            PreviewData with all regions populated, or None if not found.

        Raises:
            RuntimeError: If a table doesn't exist or if document read fails.

        """
        ...

    @abstractmethod
    def get_one_pager_status(self, one_pager_id: str) -> OnePagerHeader | None:
        """Fetch header metadata for a single One Pager.

        Args:
            one_pager_id: The One Pager identifier.

        Returns:
            OnePagerHeader if found, None otherwise.

        """
        ...

    @abstractmethod
    def read_document(
        self, one_pager_id: str, version: str | None = None
    ) -> OnePagerDocument | None:
        """Read the YAML document content for a One Pager.

        Content always comes from the One Pager YAML store (local folder in
        development, mounted volume in the workspace). The exact version is read
        when provided (from the one_pager_status table); otherwise the highest
        available version is used.

        Args:
            one_pager_id: The One Pager identifier.
            version: Current document version (e.g. "1.0.0"), or None.

        Returns:
            OnePagerDocument if found and parseable, None otherwise.

        Raises:
            RuntimeError: If the document exists but cannot be read.

        """
        ...

    @abstractmethod
    def get_change_log(self, one_pager_id: str) -> list[ChangeLogEntry]:
        """Fetch the change log for a One Pager.

        Returns entries ordered newest-first (by created_at DESC).

        Args:
            one_pager_id: The One Pager identifier.

        Returns:
            List of ChangeLogEntry (empty list if none found).

        """
        ...

    @abstractmethod
    def get_review_comments(self, one_pager_id: str) -> list[ReviewComment]:
        """Fetch review comments for a One Pager.

        Args:
            one_pager_id: The One Pager identifier.

        Returns:
            List of ReviewComment (empty list if none found).

        """
        ...

    @abstractmethod
    def get_lock(self, one_pager_id: str) -> LockInfo | None:
        """Check if a One Pager is currently locked for editing.

        Args:
            one_pager_id: The One Pager identifier.

        Returns:
            LockInfo if a lock row exists (it may have expired — see
            ``locking.is_expired``), None otherwise.

        """
        ...

    @abstractmethod
    def get_locks(self, one_pager_ids: list[str]) -> list[LockInfo]:
        """Return the lock rows (expired or not) of the given One Pagers.

        One query for a whole Registry page. One Pagers without a lock row are
        simply absent from the result.
        """
        ...

    @abstractmethod
    def write_lock(self, lock: LockInfo, *, now: datetime) -> bool:
        """Insert or replace the lock row of ``lock.one_pager_id``, conditionally.

        The row is written only if there is no lock row, the existing row has
        expired (``expires_at <= now``), or it belongs to the same holder and
        session (``locked_by_initials`` and ``session_id`` match). The check and
        the write are a single statement so a concurrent acquire cannot slip in
        between.

        Returns:
            True if the row was written, False if an active lock of someone else
            was in the way or a concurrent write conflicted.

        """
        ...

    @abstractmethod
    def refresh_lock(
        self,
        one_pager_id: str,
        *,
        locked_by_initials: str,
        session_id: str,
        last_heartbeat: datetime,
        expires_at: datetime,
    ) -> bool:
        """Update the heartbeat of the lock held by this user and session.

        Returns:
            True if that lock row exists and was updated, False otherwise.

        """
        ...

    @abstractmethod
    def delete_lock(self, one_pager_id: str, *, locked_by_initials: str) -> bool:
        """Delete the lock row of a One Pager if it is held by ``locked_by_initials``.

        Any session of the holder may release it (Backend_Design.md §6).

        Returns:
            True if a row was deleted, False otherwise.

        """
        ...

    # ========================================================================
    # Create One Pager Methods
    # ========================================================================

    @abstractmethod
    def get_sequence_value(self, id_type: str) -> int:
        """Return the last assigned value of an ``id_sequences`` counter.

        Raises:
            RuntimeError: If the counter row does not exist.

        """
        ...

    @abstractmethod
    def compare_and_set_sequence(self, id_type: str, expected: int, new: int) -> bool:
        """Set the counter to ``new`` only if it still equals ``expected``.

        Returns:
            True if exactly this call advanced the counter, False if another
            writer changed it first (the caller retries).

        """
        ...

    @abstractmethod
    def get_one_pager_ids_for_data_product(self, data_product: str) -> list[str]:
        """Return the IDs of all One Pagers registered for a data product.

        Used for the uniqueness pre-check and post-insert re-check, because
        Delta does not enforce the logical UNIQUE constraint on data_product.
        Sorted ascending.
        """
        ...

    @abstractmethod
    def get_authorized_users(self, one_pager_id: str) -> list[AuthorizedUser]:
        """Return the Owner/SME rows of ``one_pager_authorized_users``."""
        ...

    @abstractmethod
    def insert_authorized_users(self, users: list[AuthorizedUser]) -> None:
        """Insert rows into ``one_pager_authorized_users``."""
        ...

    @abstractmethod
    def append_change_log(self, entry: ChangeLogEntry) -> None:
        """Append a ``change_log`` entry. ``entry.id`` is ignored (identity)."""
        ...

    @abstractmethod
    def append_change_log_entries(self, entries: list[ChangeLogEntry]) -> None:
        """Append several ``change_log`` entries in one statement (all or none).

        Used when one action changes more than one status (Submit, Cancel,
        Approve), so the audit trail never shows half of it.
        """
        ...

    @abstractmethod
    def insert_one_pager_status(self, row: OnePagerStatusRow) -> None:
        """Insert the ``one_pager_status`` row (makes the One Pager visible)."""
        ...

    @abstractmethod
    def delete_one_pager_records(self, one_pager_id: str) -> None:
        """Compensation for a failed create ONLY (New_One_Pager_Plan D5).

        Deletes the ``one_pager_status``, ``one_pager_authorized_users`` and
        ``change_log`` rows of a One Pager whose creation did not complete.
        Must never be used for a One Pager that was successfully created —
        the change log is otherwise append-only.
        """
        ...

    # ========================================================================
    # Edit / Workflow Methods
    # ========================================================================

    @abstractmethod
    def get_one_pager_status_row(self, one_pager_id: str) -> OnePagerStatusRow | None:
        """Return the full ``one_pager_status`` row, or None if it does not exist.

        Read fresh on every call (never cached): the editor and the workflow
        transitions decide on its status and version.
        """
        ...

    @abstractmethod
    def get_one_pager_status_rows(
        self, one_pager_status: str
    ) -> list[OnePagerStatusRow]:
        """Return the ``one_pager_status`` rows with this One Pager status.

        Read fresh (never cached). Sorted by ``last_updated_at``, oldest first
        (for ``In Review`` that is the submission time: nothing else changes
        the row while it waits for review).
        """
        ...

    @abstractmethod
    def get_pending_pr_rows(self) -> list[OnePagerStatusRow]:
        """Return the ``one_pager_status`` rows with ``pending_pr = true``.

        Approved One Pagers whose Git PR could not be created (Backend §8).
        Read fresh (never cached), oldest approval first.
        """
        ...

    @abstractmethod
    def update_authorized_users(self, users: list[AuthorizedUser]) -> None:
        """Update name, email, team and role of existing authorized-user rows.

        Rows are matched on (``one_pager_id``, ``user_initials``).
        """
        ...

    @abstractmethod
    def delete_authorized_users(
        self, one_pager_id: str, user_initials: list[str]
    ) -> None:
        """Delete the authorized-user rows of these initials for a One Pager."""
        ...

    @abstractmethod
    def update_one_pager_status(
        self, row: OnePagerStatusRow, *, expected_version: str, expected_status: str
    ) -> bool:
        """Replace the mutable columns of a ``one_pager_status`` row, conditionally.

        The row is updated only if it still has ``expected_version`` and
        ``expected_status`` (optimistic concurrency: a single statement, so a
        concurrent save or transition cannot slip in between). ``one_pager_id``,
        ``data_product``, ``created_by`` and ``created_at`` are never changed.

        Returns:
            True if exactly this row was updated, False if it had changed.

        """
        ...

    # ========================================================================
    # Review Comment Methods (Backend_Design.md §13)
    # ========================================================================

    @abstractmethod
    def add_review_comment(self, comment: ReviewComment) -> None:
        """Insert a ``review_comments`` row. ``comment.id`` is ignored (identity)."""
        ...

    @abstractmethod
    def resolve_review_comment(
        self,
        one_pager_id: str,
        comment_id: int,
        *,
        resolved_by: str,
        resolved_at: datetime,
    ) -> bool:
        """Mark an unresolved comment of a One Pager as resolved, conditionally.

        Returns:
            True if exactly this comment was updated, False if it does not
            exist, belongs to another One Pager or was already resolved.

        """
        ...

    @abstractmethod
    def delete_review_comment(self, comment: ReviewComment) -> None:
        """Compensation for a failed Reject ONLY.

        Deletes the row matching ``one_pager_id``, ``reviewer_initials`` and
        ``created_at`` of a comment whose action did not complete. Comments
        are otherwise never deleted: they are part of the audit trail.
        """
        ...

    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================

    @abstractmethod
    def get_use_cases(
        self,
        filter: UseCaseFilter,  # noqa: A002 - matches DataAccess
        page: int,
        page_size: int,
    ) -> UseCasePage:
        """Query the shared Use Case registry with filtering and pagination.

        Applies filters with AND semantics and orders by use_case_id. Each
        returned UseCase has reference_count populated from use_case_references.

        Args:
            filter: UseCaseFilter (None fields = no filter on that dimension).
            page: 1-indexed page number.
            page_size: Number of rows per page.

        Returns:
            UseCasePage with the requested page and the total matching count.

        """
        ...

    @abstractmethod
    def get_use_case(self, use_case_id: str) -> UseCase | None:
        """Fetch a single Use Case (with reference_count), or None if not found."""
        ...

    @abstractmethod
    def get_use_case_references(self, use_case_id: str) -> list[str]:
        """List the IDs of the One Pagers referencing a Use Case (sorted)."""
        ...

    @abstractmethod
    def get_linked_use_case_ids(self, one_pager_id: str) -> list[str]:
        """List the Use Case IDs a One Pager references (sorted)."""
        ...

    @abstractmethod
    def add_use_case_reference(self, one_pager_id: str, use_case_id: str) -> None:
        """Insert a ``use_case_references`` row (no-op if it already exists)."""
        ...

    @abstractmethod
    def remove_use_case_reference(self, one_pager_id: str, use_case_id: str) -> None:
        """Delete a ``use_case_references`` row (no-op if it does not exist)."""
        ...

    @abstractmethod
    def create_use_case(self, data: UseCaseInput, user_initials: str) -> str:
        """Create a Use Case and return its newly allocated UC-### ID.

        The ID is generated by the application from id_sequences. The new Use
        Case is not deprecated and both audit timestamps are set to now.

        Args:
            data: Cleaned and validated field values.
            user_initials: Initials of the creator (created_by/last_updated_by).

        Returns:
            The new use_case_id.

        """
        ...

    @abstractmethod
    def update_use_case(
        self, use_case_id: str, data: UseCaseInput, user_initials: str
    ) -> None:
        """Update a Use Case's editable fields and its audit columns.

        Raises:
            NotFoundError: If no Use Case has this ID.

        """
        ...

    @abstractmethod
    def set_use_case_deprecated(
        self,
        use_case_id: str,
        deprecated: bool,  # noqa: FBT001 - matches DataAccess
        user_initials: str,
    ) -> None:
        """Deprecate (soft delete) or restore a Use Case and update audit columns.

        Raises:
            NotFoundError: If no Use Case has this ID.

        """
        ...


def require_status_row(data_access: DataAccess, one_pager_id: str) -> OnePagerStatusRow:
    """Read the ``one_pager_status`` row of a One Pager that must exist.

    Raises:
        NotFoundError: No One Pager with this ID.

    """
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    return row
