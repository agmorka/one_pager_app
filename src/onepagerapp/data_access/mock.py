"""Mock data access for local development and unit tests.

The seed data lives in ``mock_seed``.
"""

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from onepagerapp.data_access.base import (
    DataAccess,
    NotFoundError,
    check_reference_table,
    check_status_table,
)
from onepagerapp.data_access.mock_seed import (
    sample_use_cases,
    seed_authorized_users,
    seed_change_logs,
    seed_dp_statuses,
    seed_op_statuses,
    seed_reference_data,
    seed_review_comments,
    seed_status_rows,
    seed_use_case_references,
)
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
    RegistrySort,
    ReviewComment,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)
from onepagerapp.timeutils import as_utc

# Signed-in user in local-mock mode unless ONE_PAGER_APP_MOCK_USER says
# otherwise. It uses the default domain and suffix, so it gets the initials LDU
# without any extra setting.
DEFAULT_MOCK_USER = "lduadm@becoc001.onmicrosoft.com"


def _actor_columns(user_initials: str) -> dict[str, object]:
    """``last_updated_by`` / ``last_updated_at`` of a changed reference row."""
    return {"last_updated_by": user_initials, "last_updated_at": datetime.now(UTC)}


class MockDataAccess(DataAccess):
    """In-memory fake for tabular data; documents come from the YAML store.

    Tabular state (status rows, change log, authorized users, ID sequences)
    lives in instance dicts so writes are visible to later reads within the
    same instance (one per Streamlit session). The seed data matches the
    fixtures in tests/fixtures/sample_one_pagers/.
    """

    def __init__(  # noqa: D107
        self,
        document_store: OnePagerDocumentStore,
        current_user: str = DEFAULT_MOCK_USER,
        groups: frozenset[str] = frozenset(),
    ) -> None:
        self._document_store = document_store
        self._current_user = current_user
        self._groups = groups
        self._status_rows: dict[str, OnePagerStatusRow] = {
            row.one_pager_id: row for row in seed_status_rows()
        }
        self._change_logs: dict[str, list[ChangeLogEntry]] = seed_change_logs()
        self._authorized_users: dict[str, list[AuthorizedUser]] = (
            seed_authorized_users()
        )
        self._use_cases: dict[str, UseCase] = {
            uc.use_case_id: uc for uc in sample_use_cases()
        }
        # (one_pager_id, use_case_id) rows of the use_case_references table.
        self._use_case_references: set[tuple[str, str]] = seed_use_case_references()
        self._review_comments: dict[str, list[ReviewComment]] = seed_review_comments()
        self._next_review_comment_id = (
            max(
                (c.id for comments in self._review_comments.values() for c in comments),
                default=0,
            )
            + 1
        )
        # Rows of the Admin-managed reference tables and status definitions.
        self._reference: dict[str, list[dict[str, Any]]] = seed_reference_data()
        self._status_definitions: dict[str, list[dict[str, Any]]] = {
            "ref_op_status": seed_op_statuses(),
            "ref_dp_status": seed_dp_statuses(),
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
        return self._current_user

    def get_group_memberships(self, groups: dict[str, str]) -> dict[str, bool]:
        """Membership from ONE_PAGER_APP_MOCK_GROUPS, ignoring case."""
        member_of = {g.casefold() for g in self._groups}
        return {key: name.casefold() in member_of for key, name in groups.items()}

    def read_table(self, table_name: str) -> pd.DataFrame:  # noqa: ARG002
        return pd.DataFrame()

    def get_ref_op_status(self) -> pd.DataFrame:
        return pd.DataFrame(copy.deepcopy(self._status_definitions["ref_op_status"]))

    def get_ref_dp_status(self) -> pd.DataFrame:
        return pd.DataFrame(copy.deepcopy(self._status_definitions["ref_dp_status"]))

    def get_ref_business_domains(self) -> pd.DataFrame:
        return self._reference_frame("ref_business_domains")

    def get_ref_data_product_types(self) -> pd.DataFrame:
        return self._reference_frame("ref_data_product_types")

    def get_ref_source_systems(self) -> pd.DataFrame:
        return self._reference_frame("ref_source_systems")

    def _reference_frame(self, table: str) -> pd.DataFrame:
        key = check_reference_table(table)
        rows = sorted(self._reference[table], key=lambda r: (r["sort_order"], r[key]))
        return pd.DataFrame(copy.deepcopy(rows), columns=[key, "sort_order", "active"])

    def _reference_row(self, table: str, value: str) -> dict[str, Any] | None:
        key = check_reference_table(table)
        return next((r for r in self._reference[table] if r[key] == value), None)

    def insert_reference_value(
        self,
        table: str,
        value: str,
        *,
        sort_order: int,
        active: bool,
        user_initials: str,
    ) -> bool:
        if self._reference_row(table, value) is not None:
            return False
        key = check_reference_table(table)
        self._reference[table].append(
            {
                key: value,
                "sort_order": sort_order,
                "active": active,
                **_actor_columns(user_initials),
            }
        )
        return True

    def update_reference_value(
        self,
        table: str,
        value: str,
        *,
        sort_order: int,
        active: bool,
        user_initials: str,
    ) -> bool:
        row = self._reference_row(table, value)
        if row is None:
            return False
        row.update(
            sort_order=sort_order, active=active, **_actor_columns(user_initials)
        )
        return True

    def delete_reference_value(self, table: str, value: str) -> bool:
        row = self._reference_row(table, value)
        if row is None:
            return False
        self._reference[table].remove(row)
        return True

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
        check_status_table(table)
        row = next(
            (r for r in self._status_definitions[table] if r["status"] == status),
            None,
        )
        if row is None:
            return False
        row.update(
            display_label=display_label,
            sort_order=sort_order,
            badge_color=badge_color,
            **_actor_columns(user_initials),
        )
        return True

    def _get_sample_registry_data(self) -> list[RegistryRow]:
        """Return the current in-memory One Pager rows as Registry rows."""
        return [row.to_registry_row() for row in self._status_rows.values()]

    def _filtered_registry_rows(self, filter: RegistryFilter) -> list[RegistryRow]:  # noqa: A002
        """Registry rows matching every non-None filter criterion (AND semantics)."""

        def contains(value: str, needle: str | None) -> bool:
            return not needle or needle.lower() in (value or "").lower()

        def equals(value: str, wanted: str | None) -> bool:
            return not wanted or value == wanted

        linked = {
            op_id
            for op_id, uc_id in self._use_case_references
            if uc_id == filter.use_case_id
        }
        return [
            r
            for r in self._get_sample_registry_data()
            if contains(r.product_name, filter.product_name)
            and equals(r.one_pager_status, filter.op_status)
            and equals(r.data_product_status, filter.dp_status)
            and (
                contains(r.owner_name, filter.owner)
                or contains(r.owner_email, filter.owner)
            )
            and equals(r.business_domain, filter.domain)
            and equals(r.data_product_type, filter.data_product_type)
            and (not filter.use_case_id or r.one_pager_id in linked)
        ]

    def get_registry(
        self,
        filter: RegistryFilter,  # noqa: A002 - matches DataAccess
        page: int,
        page_size: int,
        sort: RegistrySort | None = None,
    ) -> RegistryPage:
        """Query One Pagers with filtering, sorting and pagination.

        Applies all non-None filter criteria with AND semantics. Sorting is
        case-insensitive, with ties broken by one_pager_id (as in the lakehouse).
        """
        sort = sort or RegistrySort()
        rows = sorted(
            self._filtered_registry_rows(filter), key=lambda r: r.one_pager_id
        )
        # Stable sort (also with reverse=True): ties keep their one_pager_id order.
        rows.sort(
            key=lambda r: str(getattr(r, sort.column) or "").lower(),
            reverse=sort.descending,
        )

        offset = (page - 1) * page_size
        return RegistryPage(
            rows=rows[offset : offset + page_size],
            total_rows=len(rows),
            page=page,
            page_size=page_size,
        )

    def get_registry_status_counts(self, filter: RegistryFilter) -> dict[str, int]:  # noqa: A002
        """Aggregate count of One Pagers by one_pager_status.

        Applies the same filter as get_registry(), then groups by one_pager_status.
        """
        counts: dict[str, int] = {}
        for row in self._filtered_registry_rows(filter):
            counts[row.one_pager_status] = counts.get(row.one_pager_status, 0) + 1
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

    def read_document(
        self, one_pager_id: str, version: str | None = None
    ) -> OnePagerDocument | None:
        """Read the YAML document content for a One Pager from the document store."""
        return self._document_store.read(one_pager_id, version)

    def get_change_log(self, one_pager_id: str) -> list[ChangeLogEntry]:
        """Fetch the change log for a One Pager (mock data, newest-first)."""
        entries = self._change_logs.get(one_pager_id, [])
        # Seeded entries are naive, new ones UTC-aware: compare both as UTC.
        return sorted(
            entries,
            key=lambda e: (as_utc(e.created_at), e.id),
            reverse=True,
        )

    def get_review_comments(self, one_pager_id: str) -> list[ReviewComment]:
        """Fetch review comments for a One Pager (oldest first)."""
        comments = self._review_comments.get(one_pager_id, [])
        return sorted(
            (copy.copy(c) for c in comments),
            key=lambda c: (as_utc(c.created_at), c.id),
        )

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

    def append_change_log_entries(self, entries: list[ChangeLogEntry]) -> None:
        for entry in entries:
            self.append_change_log(entry)

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

    def get_one_pager_status_rows(
        self, one_pager_status: str
    ) -> list[OnePagerStatusRow]:
        rows = [
            copy.copy(row)
            for row in self._status_rows.values()
            if row.one_pager_status == one_pager_status
        ]
        return sorted(rows, key=lambda r: (as_utc(r.last_updated_at), r.one_pager_id))

    def get_pending_pr_rows(self) -> list[OnePagerStatusRow]:
        rows = [copy.copy(row) for row in self._status_rows.values() if row.pending_pr]

        def approved_at(row: OnePagerStatusRow) -> tuple[bool, datetime, str]:
            reviewed = row.reviewed_at
            return (
                reviewed is None,
                as_utc(reviewed or row.last_updated_at),
                row.one_pager_id,
            )

        return sorted(rows, key=approved_at)

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
    # Review Comment Methods
    # ========================================================================

    def add_review_comment(self, comment: ReviewComment) -> None:
        stored = copy.copy(comment)
        stored.id = self._next_review_comment_id
        self._next_review_comment_id += 1
        self._review_comments.setdefault(comment.one_pager_id, []).append(stored)

    def resolve_review_comment(
        self,
        one_pager_id: str,
        comment_id: int,
        *,
        resolved_by: str,
        resolved_at: datetime,
    ) -> bool:
        comments = self._review_comments.get(one_pager_id, [])
        for i, comment in enumerate(comments):
            if comment.id == comment_id and not comment.resolved:
                comments[i] = replace(
                    comment,
                    resolved=True,
                    resolved_by=resolved_by,
                    resolved_at=resolved_at,
                )
                return True
        return False

    def delete_review_comment(self, comment: ReviewComment) -> None:
        self._review_comments[comment.one_pager_id] = [
            c
            for c in self._review_comments.get(comment.one_pager_id, [])
            if not (
                c.reviewer_initials == comment.reviewer_initials
                and c.created_at == comment.created_at
            )
        ]

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
        self,
        filter: UseCaseFilter,  # noqa: A002 - matches DataAccess
        page: int,
        page_size: int,
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

    def get_linked_use_case_ids(self, one_pager_id: str) -> list[str]:
        return sorted(
            uc_id for op_id, uc_id in self._use_case_references if op_id == one_pager_id
        )

    def add_use_case_reference(self, one_pager_id: str, use_case_id: str) -> None:
        self._use_case_references.add((one_pager_id, use_case_id))

    def remove_use_case_reference(self, one_pager_id: str, use_case_id: str) -> None:
        self._use_case_references.discard((one_pager_id, use_case_id))

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
        self,
        use_case_id: str,
        deprecated: bool,  # noqa: FBT001 - matches DataAccess
        user_initials: str,
    ) -> None:
        use_case = self._require_use_case(use_case_id)
        self._use_cases[use_case_id] = replace(
            use_case,
            deprecated=deprecated,
            last_updated_by=user_initials,
            last_updated_at=datetime.now(),
        )
