"""Abstract base class for data access."""

from abc import ABC, abstractmethod

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
    ReviewComment,
)


class DataAccess(ABC):
    """Interface for accessing application data."""

    @abstractmethod
    def get_current_user(self) -> str: ...

    @abstractmethod
    def read_table(self, table_name: str) -> pd.DataFrame: ...

    @abstractmethod
    def get_ref_op_status(self) -> pd.DataFrame:
        """Get One Pager statuses with display labels and badge colors.
        
        Returns DataFrame with columns: status, display_label, sort_order, badge_color, is_terminal
        """
        ...

    @abstractmethod
    def get_ref_dp_status(self) -> pd.DataFrame:
        """Get Data Product statuses with display labels and badge colors.
        
        Returns DataFrame with columns: status, display_label, sort_order, badge_color, is_terminal
        """
        ...

    @abstractmethod
    def get_ref_business_domains(self) -> pd.DataFrame:
        """Get valid business domain values.
        
        Returns DataFrame with columns: domain, display_label, sort_order
        """
        ...

    @abstractmethod
    def get_ref_data_product_types(self) -> pd.DataFrame:
        """Get valid data product type values.
        
        Returns DataFrame with columns: type, display_label, sort_order
        """
        ...

    @abstractmethod
    def get_registry(self, filter: RegistryFilter, page: int, page_size: int) -> RegistryPage:
        """Query One Pagers with filtering and pagination.
        
        Applies filters with AND semantics. Returns a RegistryPage with the requested page
        of results and the total count across all matching rows.
        
        Args:
            filter: RegistryFilter with optional criteria (None = no filter on that dimension).
            page: 1-indexed page number.
            page_size: Number of rows per page.
            
        Returns:
            RegistryPage containing rows for the requested page, total row count, and pagination info.
        """
        ...

    @abstractmethod
    def get_registry_status_counts(self, filter: RegistryFilter) -> dict[str, int]:
        """Aggregate count of One Pagers by one_pager_status.
        
        Applies the same filter as get_registry(), then groups by one_pager_status
        to produce status counts for metric cards.
        
        Args:
            filter: RegistryFilter with optional criteria (None = no filter on that dimension).
            
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
    def read_document(self, one_pager_id: str, version: str | None = None) -> OnePagerDocument | None:
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
            LockInfo if locked, None if not locked.
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
