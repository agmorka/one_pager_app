"""Mock data access for local development and unit tests."""

from datetime import datetime

import pandas as pd

from onepagerapp.data_access.base import DataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    ChangeLogEntry,
    LockInfo,
    OnePagerDocument,
    OnePagerHeader,
    PreviewData,
    RegistryFilter,
    RegistryPage,
    RegistryRow,
    ReviewComment,
)


class MockDataAccess(DataAccess):
    """In-memory fake for tabular data; documents come from the YAML store."""

    def __init__(self, document_store: OnePagerDocumentStore) -> None:  # noqa: D107
        self._document_store = document_store

    def get_current_user(self) -> str:
        return "local-dev-user@mock"

    def read_table(self, table_name: str) -> pd.DataFrame:  # noqa: ARG002
        return pd.DataFrame()

    def get_ref_op_status(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "status": "Draft",
                    "display_label": "Draft",
                    "sort_order": 1,
                    "badge_color": "#808080",
                    "is_terminal": False,
                },
                {
                    "status": "Ready for Review",
                    "display_label": "Ready for Review",
                    "sort_order": 2,
                    "badge_color": "#F9BD00",
                    "is_terminal": False,
                },
                {
                    "status": "In Review",
                    "display_label": "In Review",
                    "sort_order": 3,
                    "badge_color": "#FFA500",
                    "is_terminal": False,
                },
                {
                    "status": "Approved",
                    "display_label": "Approved",
                    "sort_order": 4,
                    "badge_color": "#65B676",
                    "is_terminal": False,
                },
                {
                    "status": "Draft Update",
                    "display_label": "Draft Update",
                    "sort_order": 5,
                    "badge_color": "#7E57C2",
                    "is_terminal": False,
                },
                {
                    "status": "Cancelled",
                    "display_label": "Cancelled",
                    "sort_order": 6,
                    "badge_color": "#F34421",
                    "is_terminal": True,
                },
            ]
        )

    def get_ref_dp_status(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "status": "In Definition",
                    "display_label": "In Definition",
                    "sort_order": 1,
                    "badge_color": "#808080",
                    "is_terminal": False,
                },
                {
                    "status": "Ready for Development",
                    "display_label": "Ready for Development",
                    "sort_order": 2,
                    "badge_color": "#65B676",
                    "is_terminal": False,
                },
                {
                    "status": "In Development",
                    "display_label": "In Development",
                    "sort_order": 3,
                    "badge_color": "#3599B8",
                    "is_terminal": False,
                },
                {
                    "status": "Active",
                    "display_label": "Active",
                    "sort_order": 4,
                    "badge_color": "#00975f",
                    "is_terminal": False,
                },
                {
                    "status": "In Enhancement",
                    "display_label": "In Enhancement",
                    "sort_order": 5,
                    "badge_color": "#F9BD00",
                    "is_terminal": False,
                },
                {
                    "status": "Deprecated",
                    "display_label": "Deprecated",
                    "sort_order": 6,
                    "badge_color": "#7E57C2",
                    "is_terminal": True,
                },
                {
                    "status": "Cancelled",
                    "display_label": "Cancelled",
                    "sort_order": 7,
                    "badge_color": "#F34421",
                    "is_terminal": True,
                },
            ]
        )

    def get_ref_business_domains(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"domain": "Finance", "display_label": "Finance", "sort_order": 1},
                {"domain": "Operations", "display_label": "Operations", "sort_order": 2},
                {"domain": "HR", "display_label": "Human Resources", "sort_order": 3},
                {"domain": "Technology", "display_label": "Technology", "sort_order": 4},
                {"domain": "Marketing", "display_label": "Marketing", "sort_order": 5},
                {"domain": "Sales", "display_label": "Sales", "sort_order": 6},
                {"domain": "Customer", "display_label": "Customer", "sort_order": 7},
            ]
        )

    def get_ref_data_product_types(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "type": "Foundational",
                    "display_label": "Foundational",
                    "sort_order": 1,
                },
                {
                    "type": "Integrated",
                    "display_label": "Integrated",
                    "sort_order": 2,
                },
                {
                    "type": "Augmented",
                    "display_label": "Augmented",
                    "sort_order": 3,
                },
            ]
        )

    def _get_sample_registry_data(self) -> list[RegistryRow]:
        """Return sample One Pager data for local testing."""
        return [
            RegistryRow(
                one_pager_id="OP-0001",
                product_name="Person Master Data",
                business_domain="Customer",
                data_product_type="Foundational",
                one_pager_status="Approved",
                data_product_status="Ready for Development",
                owner_name="Alice Brown",
                owner_email="alice.brown@company.com",
                version="1.0.0",
                last_updated_at=datetime(2026, 9, 20, 14, 30),
                last_updated_by="AB",
            ),
            RegistryRow(
                one_pager_id="OP-0002",
                product_name="Order Master Data",
                business_domain="Sales",
                data_product_type="Foundational",
                one_pager_status="In Review",
                data_product_status="In Definition",
                owner_name="Bob Smith",
                owner_email="bob.smith@company.com",
                version="0.3.0",
                last_updated_at=datetime(2026, 9, 19, 10, 15),
                last_updated_by="BS",
            ),
        ]

    def get_registry(
        self, filter: RegistryFilter, page: int, page_size: int
    ) -> RegistryPage:
        """Query One Pagers with filtering and pagination.
        
        Applies all non-None filter criteria with AND semantics.
        """
        rows = self._get_sample_registry_data()
        df = pd.DataFrame(
            [
                {
                    "one_pager_id": r.one_pager_id,
                    "product_name": r.product_name,
                    "business_domain": r.business_domain,
                    "data_product_type": r.data_product_type,
                    "one_pager_status": r.one_pager_status,
                    "data_product_status": r.data_product_status,
                    "owner_name": r.owner_name,
                    "owner_email": r.owner_email,
                    "version": r.version,
                    "last_updated_at": r.last_updated_at,
                    "last_updated_by": r.last_updated_by,
                }
                for r in rows
            ]
        )

        # Apply filters
        if filter.product_name:
            df = df[
                df["product_name"]
                .str.lower()
                .str.contains(filter.product_name.lower(), na=False)
            ]
        if filter.op_status:
            df = df[df["one_pager_status"] == filter.op_status]
        if filter.dp_status:
            df = df[df["data_product_status"] == filter.dp_status]
        if filter.owner:
            df = df[
                (
                    df["owner_name"]
                    .str.lower()
                    .str.contains(filter.owner.lower(), na=False)
                )
                | (
                    df["owner_email"]
                    .str.lower()
                    .str.contains(filter.owner.lower(), na=False)
                )
            ]
        if filter.domain:
            df = df[df["business_domain"] == filter.domain]
        if filter.data_product_type:
            df = df[df["data_product_type"] == filter.data_product_type]

        total_rows = len(df)

        # Sort by one_pager_id for consistent pagination
        df = df.sort_values("one_pager_id").reset_index(drop=True)

        # Paginate
        offset = (page - 1) * page_size
        limit = page_size
        df_page = df.iloc[offset : offset + limit]

        # Convert back to RegistryRow objects
        page_rows = [
            RegistryRow(
                one_pager_id=row["one_pager_id"],
                product_name=row["product_name"],
                business_domain=row["business_domain"],
                data_product_type=row["data_product_type"],
                one_pager_status=row["one_pager_status"],
                data_product_status=row["data_product_status"],
                owner_name=row["owner_name"],
                owner_email=row["owner_email"],
                version=row["version"],
                last_updated_at=row["last_updated_at"],
                last_updated_by=row["last_updated_by"],
            )
            for _, row in df_page.iterrows()
        ]

        return RegistryPage(
            rows=page_rows,
            total_rows=total_rows,
            page=page,
            page_size=page_size,
        )

    def get_registry_status_counts(self, filter: RegistryFilter) -> dict[str, int]:
        """Aggregate count of One Pagers by one_pager_status.
        
        Applies the same filter as get_registry(), then groups by one_pager_status.
        """
        rows = self._get_sample_registry_data()
        df = pd.DataFrame(
            [
                {
                    "one_pager_status": r.one_pager_status,
                    "product_name": r.product_name,
                    "business_domain": r.business_domain,
                    "data_product_type": r.data_product_type,
                    "data_product_status": r.data_product_status,
                    "owner_name": r.owner_name,
                    "owner_email": r.owner_email,
                }
                for r in rows
            ]
        )

        # Apply the same filters as get_registry()
        if filter.product_name:
            df = df[
                df["product_name"]
                .str.lower()
                .str.contains(filter.product_name.lower(), na=False)
            ]
        if filter.op_status:
            df = df[df["one_pager_status"] == filter.op_status]
        if filter.dp_status:
            df = df[df["data_product_status"] == filter.dp_status]
        if filter.owner:
            df = df[
                (
                    df["owner_name"]
                    .str.lower()
                    .str.contains(filter.owner.lower(), na=False)
                )
                | (
                    df["owner_email"]
                    .str.lower()
                    .str.contains(filter.owner.lower(), na=False)
                )
            ]
        if filter.domain:
            df = df[df["business_domain"] == filter.domain]
        if filter.data_product_type:
            df = df[df["data_product_type"] == filter.data_product_type]

        # Group by one_pager_status and count
        counts = df["one_pager_status"].value_counts().to_dict()
        return counts

    # ========================================================================
    # Preview Page Methods
    # ========================================================================

    def get_one_pager(self, one_pager_id: str) -> PreviewData | None:
        """Fetch a complete One Pager for preview display (mock data)."""
        header = self.get_one_pager_status(one_pager_id)
        if not header:
            return None

        document = self.read_document(one_pager_id, header.version)
        if not document:
            return None

        change_log = self.get_change_log(one_pager_id)
        review_comments = self.get_review_comments(one_pager_id)
        lock = self.get_lock(one_pager_id)

        return PreviewData(
            header=header,
            document=document,
            change_log=change_log,
            review_comments=review_comments,
            lock=lock,
        )

    def get_one_pager_status(self, one_pager_id: str) -> OnePagerHeader | None:
        """Fetch header metadata for a single One Pager (mock data)."""
        headers = {
            "OP-0001": OnePagerHeader(
                one_pager_id="OP-0001",
                product_name="Person Master Data",
                owner_name="Alice Brown",
                owner_initials="AB",
                owner_email="alice.brown@company.com",
                version="1.0.0",
                one_pager_status="Approved",
                data_product_status="Ready for Development",
                created_at=datetime(2026, 8, 1, 9, 0),
                last_updated_at=datetime(2026, 9, 20, 14, 30),
                last_updated_by="AB",
            ),
            "OP-0002": OnePagerHeader(
                one_pager_id="OP-0002",
                product_name="Order Master Data",
                owner_name="Bob Smith",
                owner_initials="BS",
                owner_email="bob.smith@company.com",
                version="0.3.0",
                one_pager_status="In Review",
                data_product_status="In Definition",
                created_at=datetime(2026, 9, 1, 9, 0),
                last_updated_at=datetime(2026, 9, 19, 10, 15),
                last_updated_by="BS",
            ),
        }
        return headers.get(one_pager_id)

    def read_document(self, one_pager_id: str, version: str | None = None) -> OnePagerDocument | None:
        """Read the YAML document content for a One Pager from the document store."""
        return self._document_store.read(one_pager_id, version)

    def get_change_log(self, one_pager_id: str) -> list[ChangeLogEntry]:
        """Fetch the change log for a One Pager (mock data, newest-first)."""
        if one_pager_id != "OP-0001":
            return []

        return [
            ChangeLogEntry(
                id=3,
                one_pager_id="OP-0001",
                version="1.0.0",
                event_type="status_transition",
                author_initials="ADMIN",
                author_name="Approval System",
                summary="Document approved and published to Git",
                created_at=datetime(2026, 9, 20, 14, 30),
                from_status="In Review",
                to_status="Approved",
                status_field="one_pager_status",
            ),
            ChangeLogEntry(
                id=2,
                one_pager_id="OP-0001",
                version="0.9.0",
                event_type="content_save",
                author_initials="AB",
                author_name="Alice Brown",
                summary="Addressed review comments on data sources",
                created_at=datetime(2026, 9, 15, 10, 0),
            ),
            ChangeLogEntry(
                id=1,
                one_pager_id="OP-0001",
                version="0.1.0",
                event_type="creation",
                author_initials="AB",
                author_name="Alice Brown",
                summary="Initial One Pager created",
                created_at=datetime(2026, 8, 1, 9, 0),
            ),
        ]

    def get_review_comments(self, one_pager_id: str) -> list[ReviewComment]:
        """Fetch review comments for a One Pager (mock data)."""
        if one_pager_id != "OP-0001":
            return []

        return [
            ReviewComment(
                id=2,
                one_pager_id="OP-0001",
                version="0.9.0",
                section="dataSources",
                reviewer_initials="BS",
                reviewer_name="Bob Smith",
                comment="Need to clarify the refreshFrequency for Salesforce.",
                resolved=True,
                created_at=datetime(2026, 9, 12, 11, 0),
                resolved_by="AB",
                resolved_at=datetime(2026, 9, 15, 10, 30),
            ),
            ReviewComment(
                id=1,
                one_pager_id="OP-0001",
                version="0.1.0",
                section="businessRequirements",
                reviewer_initials="CJ",
                reviewer_name="Charlie Jones",
                comment="Add a requirement for audit trail compliance.",
                resolved=True,
                created_at=datetime(2026, 9, 1, 9, 0),
                resolved_by="AB",
                resolved_at=datetime(2026, 9, 5, 14, 0),
            ),
        ]

    def get_lock(self, one_pager_id: str) -> LockInfo | None:
        """Check if a One Pager is currently locked for editing (mock data)."""
        # In mock mode, OP-0001 is not locked
        # (In real scenario, would check locks table)
        return None
