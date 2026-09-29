"""Mock data access for local development and unit tests."""

import copy
from dataclasses import replace
from datetime import datetime

import pandas as pd

from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.id_generator import next_id
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
    RegistryRow,
    ReviewComment,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)
from onepagerapp.validation import CURRENT_STRUCTURE_DEFINITION


class MockDataAccess(DataAccess):
    """In-memory fake for tabular data; documents come from the YAML store.

    Tabular state (status rows, change log, authorized users, ID sequences)
    lives in instance dicts so writes are visible to later reads within the
    same instance (one per Streamlit session). The seed data matches the
    fixtures in tests/fixtures/sample_one_pagers/.
    """

    def __init__(self, document_store: OnePagerDocumentStore) -> None:  # noqa: D107
        self._document_store = document_store
        self._status_rows: dict[str, OnePagerStatusRow] = {
            row.one_pager_id: row for row in _seed_status_rows()
        }
        self._change_logs: dict[str, list[ChangeLogEntry]] = _seed_change_logs()
        self._authorized_users: dict[str, list[AuthorizedUser]] = (
            _seed_authorized_users()
        )
        self._use_cases: dict[str, UseCase] = {
            uc.use_case_id: uc for uc in _sample_use_cases()
        }
        # (one_pager_id, use_case_id) rows of the use_case_references table.
        # The sample YAML documents store Use Cases inline without IDs, so the
        # references are seeded here rather than derived from the documents.
        self._use_case_references: set[tuple[str, str]] = {
            ("OP-0001", "UC-001"),
            ("OP-0001", "UC-002"),
            ("OP-0002", "UC-002"),
            ("OP-0002", "UC-003"),
            ("OP-0002", "UC-004"),
        }
        # Rows of the locks table, keyed by one_pager_id (one lock per One Pager).
        self._locks: dict[str, LockInfo] = {}
        # D14: counters start after the highest seeded mock IDs.
        self._sequences: dict[str, int] = {
            "OP": max(int(i.removeprefix("OP-")) for i in self._status_rows),
            "UC": len(self._use_cases),
            "BR": 0,
        }
        self._next_change_log_id = (
            max(
                (e.id for entries in self._change_logs.values() for e in entries),
                default=0,
            )
            + 1
        )

    def get_current_user(self) -> str:
        return "local-dev-user@mock.local"

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
        """Return the current in-memory One Pager rows as Registry rows."""
        return [row.to_registry_row() for row in self._status_rows.values()]

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
        row = self._status_rows.get(one_pager_id)
        return row.to_header() if row else None

    def read_document(self, one_pager_id: str, version: str | None = None) -> OnePagerDocument | None:
        """Read the YAML document content for a One Pager from the document store."""
        return self._document_store.read(one_pager_id, version)

    def get_change_log(self, one_pager_id: str) -> list[ChangeLogEntry]:
        """Fetch the change log for a One Pager (mock data, newest-first)."""
        entries = self._change_logs.get(one_pager_id, [])
        return sorted(entries, key=lambda e: (e.created_at, e.id), reverse=True)

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
        """Return the lock row of a One Pager (mock data), expired or not."""
        lock = self._locks.get(one_pager_id)
        return copy.copy(lock) if lock else None

    def get_locks(self, one_pager_ids: list[str]) -> list[LockInfo]:
        return [
            copy.copy(self._locks[op_id])
            for op_id in one_pager_ids
            if op_id in self._locks
        ]

    def write_lock(self, lock: LockInfo, *, now: datetime) -> bool:
        current = self._locks.get(lock.one_pager_id)
        if current is not None and not (
            current.expires_at <= now
            or (
                current.locked_by_initials == lock.locked_by_initials
                and current.session_id == lock.session_id
            )
        ):
            return False
        self._locks[lock.one_pager_id] = copy.copy(lock)
        return True

    def refresh_lock(
        self,
        one_pager_id: str,
        *,
        locked_by_initials: str,
        session_id: str,
        last_heartbeat: datetime,
        expires_at: datetime,
    ) -> bool:
        current = self._locks.get(one_pager_id)
        if (
            current is None
            or current.locked_by_initials != locked_by_initials
            or current.session_id != session_id
        ):
            return False
        self._locks[one_pager_id] = replace(
            current, last_heartbeat=last_heartbeat, expires_at=expires_at
        )
        return True

    def delete_lock(self, one_pager_id: str, *, locked_by_initials: str) -> bool:
        current = self._locks.get(one_pager_id)
        if current is None or current.locked_by_initials != locked_by_initials:
            return False
        del self._locks[one_pager_id]
        return True

    # ========================================================================
    # Create One Pager Methods
    # ========================================================================

    def get_sequence_value(self, id_type: str) -> int:
        if id_type not in self._sequences:
            msg = f"id_sequences has no row for {id_type}"
            raise RuntimeError(msg)
        return self._sequences[id_type]

    def compare_and_set_sequence(self, id_type: str, expected: int, new: int) -> bool:
        if self._sequences.get(id_type) != expected:
            return False
        self._sequences[id_type] = new
        return True

    def get_one_pager_ids_for_data_product(self, data_product: str) -> list[str]:
        return sorted(
            row.one_pager_id
            for row in self._status_rows.values()
            if row.data_product == data_product
        )

    def get_authorized_users(self, one_pager_id: str) -> list[AuthorizedUser]:
        return list(self._authorized_users.get(one_pager_id, []))

    def insert_authorized_users(self, users: list[AuthorizedUser]) -> None:
        for user in users:
            self._authorized_users.setdefault(user.one_pager_id, []).append(
                copy.copy(user)
            )

    def append_change_log(self, entry: ChangeLogEntry) -> None:
        stored = copy.copy(entry)
        stored.id = self._next_change_log_id
        self._next_change_log_id += 1
        self._change_logs.setdefault(entry.one_pager_id, []).append(stored)

    def insert_one_pager_status(self, row: OnePagerStatusRow) -> None:
        if row.one_pager_id in self._status_rows:
            msg = f"one_pager_status already contains {row.one_pager_id}"
            raise RuntimeError(msg)
        self._status_rows[row.one_pager_id] = copy.copy(row)

    def delete_one_pager_records(self, one_pager_id: str) -> None:
        self._status_rows.pop(one_pager_id, None)
        self._authorized_users.pop(one_pager_id, None)
        self._change_logs.pop(one_pager_id, None)

    # ========================================================================
    # Edit / Workflow Methods
    # ========================================================================

    def get_one_pager_status_row(self, one_pager_id: str) -> OnePagerStatusRow | None:
        row = self._status_rows.get(one_pager_id)
        return copy.copy(row) if row else None

    def update_authorized_users(self, users: list[AuthorizedUser]) -> None:
        for user in users:
            rows = self._authorized_users.get(user.one_pager_id, [])
            self._authorized_users[user.one_pager_id] = [
                copy.copy(user) if row.user_initials == user.user_initials else row
                for row in rows
            ]

    def delete_authorized_users(
        self, one_pager_id: str, user_initials: list[str]
    ) -> None:
        self._authorized_users[one_pager_id] = [
            row
            for row in self._authorized_users.get(one_pager_id, [])
            if row.user_initials not in user_initials
        ]

    def update_one_pager_status(
        self, row: OnePagerStatusRow, *, expected_version: str, expected_status: str
    ) -> bool:
        current = self._status_rows.get(row.one_pager_id)
        if (
            current is None
            or current.version != expected_version
            or current.one_pager_status != expected_status
        ):
            return False
        self._status_rows[row.one_pager_id] = replace(
            row,
            data_product=current.data_product,
            created_by=current.created_by,
            created_at=current.created_at,
        )
        return True

    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================

    def _with_reference_count(self, use_case: UseCase) -> UseCase:
        """Return a copy with reference_count derived from the references set."""
        count = sum(
            1 for _, uc_id in self._use_case_references if uc_id == use_case.use_case_id
        )
        return replace(use_case, reference_count=count)

    def _require_use_case(self, use_case_id: str) -> UseCase:
        use_case = self._use_cases.get(use_case_id)
        if use_case is None:
            msg = f"Use Case {use_case_id} not found"
            raise NotFoundError(msg)
        return use_case

    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
    ) -> UseCasePage:
        rows = sorted(self._use_cases.values(), key=lambda uc: uc.use_case_id)
        if not filter.include_deprecated:
            rows = [uc for uc in rows if not uc.deprecated]
        if filter.search:
            needle = filter.search.lower()
            rows = [
                uc
                for uc in rows
                if needle in uc.persona.lower() or needle in uc.goal.lower()
            ]
        if filter.priority:
            rows = [uc for uc in rows if uc.priority == filter.priority]

        offset = (page - 1) * page_size
        return UseCasePage(
            rows=[
                self._with_reference_count(uc)
                for uc in rows[offset : offset + page_size]
            ],
            total_rows=len(rows),
            page=page,
            page_size=page_size,
        )

    def get_use_case(self, use_case_id: str) -> UseCase | None:
        use_case = self._use_cases.get(use_case_id)
        return self._with_reference_count(use_case) if use_case else None

    def get_use_case_references(self, use_case_id: str) -> list[str]:
        return sorted(
            op_id for op_id, uc_id in self._use_case_references if uc_id == use_case_id
        )

    def create_use_case(self, data: UseCaseInput, user_initials: str) -> str:
        use_case_id = next_id(self, "UC")
        now = datetime.now()
        self._use_cases[use_case_id] = UseCase(
            use_case_id=use_case_id,
            persona=data.persona,
            goal=data.goal,
            scenario=data.scenario,
            decision_enabled=data.decision_enabled,
            priority=data.priority,
            deprecated=False,
            created_by=user_initials,
            created_at=now,
            last_updated_by=user_initials,
            last_updated_at=now,
        )
        return use_case_id

    def update_use_case(
        self, use_case_id: str, data: UseCaseInput, user_initials: str
    ) -> None:
        use_case = self._require_use_case(use_case_id)
        self._use_cases[use_case_id] = replace(
            use_case,
            persona=data.persona,
            goal=data.goal,
            scenario=data.scenario,
            decision_enabled=data.decision_enabled,
            priority=data.priority,
            last_updated_by=user_initials,
            last_updated_at=datetime.now(),
        )

    def set_use_case_deprecated(
        self, use_case_id: str, deprecated: bool, user_initials: str  # noqa: FBT001
    ) -> None:
        use_case = self._require_use_case(use_case_id)
        self._use_cases[use_case_id] = replace(
            use_case,
            deprecated=deprecated,
            last_updated_by=user_initials,
            last_updated_at=datetime.now(),
        )


# ============================================================================
# Seed data (matches tests/fixtures/sample_one_pagers/)
# ============================================================================


def _seed_status_rows() -> list[OnePagerStatusRow]:
    return [
        OnePagerStatusRow(
            one_pager_id="OP-0001",
            data_product="person",
            product_name="Person Master Data",
            business_domain="Customer",
            data_product_type="Foundational",
            one_pager_status="Approved",
            data_product_status="Ready for Development",
            version="1.0.0",
            owner_name="Alice Brown",
            owner_initials="AB",
            owner_email="alice.brown@company.com",
            owner_team="Data Platform",
            created_by="AB",
            created_at=datetime(2026, 8, 1, 9, 0),
            last_updated_at=datetime(2026, 9, 20, 14, 30),
            last_updated_by="AB",
            structure_definition=CURRENT_STRUCTURE_DEFINITION,
            reviewed_at=datetime(2026, 9, 20, 14, 30),
            reviewed_by="CJ",
        ),
        OnePagerStatusRow(
            one_pager_id="OP-0002",
            data_product="order",
            product_name="Order Master Data",
            business_domain="Sales",
            data_product_type="Foundational",
            one_pager_status="In Review",
            data_product_status="In Definition",
            version="0.3.0",
            owner_name="Bob Smith",
            owner_initials="BS",
            owner_email="bob.smith@company.com",
            owner_team="Sales Analytics",
            created_by="BS",
            created_at=datetime(2026, 9, 1, 9, 0),
            last_updated_at=datetime(2026, 9, 19, 10, 15),
            last_updated_by="BS",
            structure_definition=CURRENT_STRUCTURE_DEFINITION,
        ),
    ]


def _seed_authorized_users() -> dict[str, list[AuthorizedUser]]:
    return {
        "OP-0001": [
            AuthorizedUser(
                one_pager_id="OP-0001",
                user_initials="AB",
                user_name="Alice Brown",
                user_email="alice.brown@company.com",
                user_team="Data Platform",
                role="owner",
            ),
        ],
        "OP-0002": [
            AuthorizedUser(
                one_pager_id="OP-0002",
                user_initials="BS",
                user_name="Bob Smith",
                user_email="bob.smith@company.com",
                user_team="Sales Analytics",
                role="owner",
            ),
            AuthorizedUser(
                one_pager_id="OP-0002",
                user_initials="DP",
                user_name="Diana Prince",
                user_email="diana.prince@company.com",
                user_team="Finance",
                role="sme",
            ),
        ],
    }


def _seed_change_logs() -> dict[str, list[ChangeLogEntry]]:
    return {
        "OP-0001": [
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
        ],
    }


def _sample_use_cases() -> list[UseCase]:
    """Return sample Use Cases for local testing (mirrors seed_use_cases_dev.sql)."""
    samples = [
        (
            "UC-001", "Analytics Manager",
            "Understand customer lifetime value trends",
            "Aggregate spending, engagement, and product usage "
            "without duplicate records",
            "Identify high-value customer segments for targeted marketing",
            "Must Have", False, "JD", datetime(2026, 6, 1, 9, 0),
        ),
        (
            "UC-002", "Compliance Officer",
            "Fulfill GDPR data subject access requests quickly",
            "Query Person dataset with unique ID and get all attributes in one place",
            "Respond to GDPR requests within 30 days",
            "Must Have", False, "AB", datetime(2026, 6, 1, 9, 30),
        ),
        (
            "UC-003", "Finance Director",
            "Reconcile revenue across channels and time periods",
            "Query unified order data by date range, channel, product, and customer",
            "Close accounting books on time with full audit trail",
            "High", False, "BS", datetime(2026, 7, 2, 11, 0),
        ),
        (
            "UC-004", "Operations Manager",
            "Track fulfillment status and predict delivery dates",
            "See order status, warehouse inventory, and shipping progress in one view",
            "Proactively communicate delivery estimates to customers",
            "Medium", False, "BS", datetime(2026, 7, 2, 11, 15),
        ),
        (
            "UC-005", "Branch Advisor",
            "See a customer summary before meetings",
            "Open a printed customer summary prepared by the back office",
            "Prepare advice for scheduled customer meetings",
            "Low", True, "AB", datetime(2026, 5, 20, 8, 45),
        ),
    ]
    return [
        UseCase(
            use_case_id=uc_id,
            persona=persona,
            goal=goal,
            scenario=scenario,
            decision_enabled=decision,
            priority=priority,
            deprecated=deprecated,
            created_by=initials,
            created_at=created_at,
            last_updated_by=initials,
            last_updated_at=created_at,
        )
        for (
            uc_id, persona, goal, scenario, decision, priority, deprecated, initials,
            created_at,
        ) in samples
    ]
