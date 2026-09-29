"""Abstract base class for data access."""

from abc import ABC, abstractmethod

import pandas as pd

from onepagerapp.models import (
    ChangeLogEntry,
    LockInfo,
    OnePagerDocument,
    OnePagerHeader,
    PreviewData,
    RegistryFilter,
    RegistryPage,
    ReviewComment,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)


class NotFoundError(LookupError):
    """The requested record does not exist."""


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
    # Use Cases Page Methods
    # ========================================================================

    @abstractmethod
    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
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
        self, use_case_id: str, deprecated: bool, user_initials: str  # noqa: FBT001
    ) -> None:
        """Deprecate (soft delete) or restore a Use Case and update audit columns.

        Raises:
            NotFoundError: If no Use Case has this ID.
        """
        ...
