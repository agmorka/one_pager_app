"""Real data access implementation using the Databricks SQL Statement Execution API."""

import logging
from datetime import datetime
from typing import Any

import pandas as pd
from databricks.sdk.service.sql import StatementResponse

from onepagerapp.config import AppConfig
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import DatabricksConnection
from onepagerapp.documents import OnePagerDocumentStore
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
)

logger = logging.getLogger(__name__)

_ONE_PAGER_STATUS_COLUMNS = (
    "one_pager_id",
    "data_product",
    "product_name",
    "business_domain",
    "data_product_type",
    "one_pager_status",
    "data_product_status",
    "version",
    "owner_name",
    "owner_initials",
    "owner_email",
    "owner_team",
    "created_by",
    "created_at",
    "last_updated_at",
    "last_updated_by",
    "reviewed_at",
    "reviewed_by",
    "structure_definition",
    "pending_pr",
)


def _to_datetime(value: object) -> datetime:
    """Parse a TIMESTAMP value returned by the Statement Execution API.

    The API returns timestamps as ISO-8601 strings (e.g. "2026-09-20T14:30:00Z").
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _rows(response: StatementResponse) -> list[dict[str, Any]]:
    """Convert a StatementResponse into a list of column -> value dicts."""
    schema = response.manifest.schema if response.manifest else None
    columns = [str(col.name) for col in (schema.columns if schema else None) or []]
    data = (response.result.data_array if response.result else None) or []
    return [dict(zip(columns, row, strict=False)) for row in data]


class LakehouseAccess(DataAccess):
    """Queries Delta tables via a SQL warehouse.

    Uses DatabricksConnection for connection management and SQL execution.
    Focuses on building queries and transforming results into business objects.
    Document content is delegated to OnePagerDocumentStore.

    On Databricks: uses the end-user's token (from x-forwarded-access-token)
    so that Unity Catalog permissions are enforced per user.
    Locally (local-integration): falls back to the Databricks CLI profile.
    """

    def __init__(self, config: AppConfig, document_store: OnePagerDocumentStore) -> None:  # noqa: D107
        self._config = config
        self._connection = DatabricksConnection(config)
        self._document_store = document_store

    @property
    def _fqn_prefix(self) -> str:
        """Fully qualified name prefix for tables (catalog.schema)."""
        catalog = self._config.ONE_PAGER_APP_DATABRICKS_CATALOG
        schema = self._config.ONE_PAGER_APP_DATABRICKS_SCHEMA
        return f"{catalog}.{schema}"

    def get_current_user(self) -> str:
        response = self._connection.execute_statement("SELECT current_user()")
        rows = (response.result.data_array if response.result else None) or []
        if not rows:
            msg = "Failed to retrieve current user"
            raise RuntimeError(msg)
        return str(rows[0][0])

    def read_table(self, table_name: str) -> pd.DataFrame:
        query = f"SELECT * FROM {self._fqn_prefix}.{table_name}"  # noqa: S608
        fqn = f"{self._fqn_prefix}.{table_name}"
        try:
            response = self._connection.execute_statement(query)
            
            # Extract schema/columns from response
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            logger.debug(
                f"Query {query} returned: "
                f"manifest={response.manifest is not None}, "
                f"schema={schema is not None}, "
                f"columns={columns}, "
                f"rows={len(rows)}"
            )
            
            # Validate we got columns
            if not columns and rows:
                msg = (
                    f"Table {fqn} returned data but no column names. "
                    f"SQL response missing schema information."
                )
                logger.error(msg)
                raise RuntimeError(msg)
            
            if not columns and not rows:
                msg = (
                    f"Table {fqn} returned no data and no column information. "
                    f"Table may not exist or is empty with unknown schema."
                )
                logger.error(msg)
                raise RuntimeError(msg)
            
            return pd.DataFrame(rows, columns=columns)
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Failed to read table {fqn}: {type(e).__name__}: {e}")
            msg = (
                f"Table {fqn} does not exist or is not accessible. "
                f"Please ensure it has been deployed via Liquibase migrations. "
                f"Error: {e}"
            )
            raise RuntimeError(msg) from e

    def get_ref_op_status(self) -> pd.DataFrame:
        return self.read_table("ref_op_status")

    def get_ref_dp_status(self) -> pd.DataFrame:
        return self.read_table("ref_dp_status")

    def get_ref_business_domains(self) -> pd.DataFrame:
        return self.read_table("ref_business_domains")

    def get_ref_data_product_types(self) -> pd.DataFrame:
        return self.read_table("ref_data_product_types")

    @staticmethod
    def _escape_sql_string(value: str) -> str:
        """Escape a string for SQL to prevent injection.
        
        Replaces single quotes with doubled quotes per SQL standard.
        """
        return value.replace("'", "''")

    def get_registry(
        self, filter: RegistryFilter, page: int, page_size: int
    ) -> RegistryPage:
        """Query One Pagers with server-side filtering and pagination.
        
        Builds a WHERE clause based on filter criteria, uses LIMIT/OFFSET for pagination,
        and converts results to RegistryRow objects.
        
        All filter values are escaped to prevent SQL injection.
        """
        fqn = f"{self._fqn_prefix}.one_pager_status"
        
        # Build WHERE clause with escaped values
        where_clauses = []
        
        if filter.product_name:
            escaped = self._escape_sql_string(filter.product_name)
            where_clauses.append(f"LOWER(product_name) LIKE LOWER('%{escaped}%')")
        
        if filter.op_status:
            escaped = self._escape_sql_string(filter.op_status)
            where_clauses.append(f"one_pager_status = '{escaped}'")
        
        if filter.dp_status:
            escaped = self._escape_sql_string(filter.dp_status)
            where_clauses.append(f"data_product_status = '{escaped}'")
        
        if filter.owner:
            escaped = self._escape_sql_string(filter.owner)
            where_clauses.append(
                f"(LOWER(owner_name) LIKE LOWER('%{escaped}%') "
                f"OR LOWER(owner_email) LIKE LOWER('%{escaped}%'))"
            )
        
        if filter.domain:
            escaped = self._escape_sql_string(filter.domain)
            where_clauses.append(f"business_domain = '{escaped}'")
        
        if filter.data_product_type:
            escaped = self._escape_sql_string(filter.data_product_type)
            where_clauses.append(f"data_product_type = '{escaped}'")
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # Count total rows matching filter
        count_query = f"SELECT COUNT(*) as total FROM {fqn} WHERE {where_clause}"
        try:
            count_response = self._connection.execute_statement(count_query)
            count_rows = (count_response.result.data_array if count_response.result else None) or []
            total_rows = int(count_rows[0][0]) if count_rows else 0
        except Exception as e:
            logger.error(f"Failed to count registry rows: {e}")
            raise RuntimeError(f"Failed to count One Pagers: {e}") from e
        
        # Query for page data with pagination
        offset = (page - 1) * page_size
        query = (
            f"SELECT one_pager_id, product_name, business_domain, data_product_type, "
            f"one_pager_status, data_product_status, owner_name, owner_email, version, "
            f"last_updated_at, last_updated_by "
            f"FROM {fqn} "
            f"WHERE {where_clause} "
            f"ORDER BY one_pager_id "
            f"LIMIT {page_size} OFFSET {offset}"
        )
        
        try:
            response = self._connection.execute_statement(query)
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame()
            
            page_rows = []
            for _, row in df.iterrows():
                page_rows.append(
                    RegistryRow(
                        one_pager_id=str(row["one_pager_id"]),
                        product_name=str(row["product_name"]),
                        business_domain=str(row["business_domain"]),
                        data_product_type=str(row["data_product_type"]),
                        one_pager_status=str(row["one_pager_status"]),
                        data_product_status=str(row["data_product_status"]),
                        owner_name=str(row["owner_name"]),
                        owner_email=str(row["owner_email"]),
                        version=str(row["version"]),
                        last_updated_at=row["last_updated_at"],
                        last_updated_by=str(row["last_updated_by"]),
                    )
                )
            
            return RegistryPage(
                rows=page_rows,
                total_rows=total_rows,
                page=page,
                page_size=page_size,
            )
        except Exception as e:
            logger.error(f"Failed to fetch registry page: {e}")
            raise RuntimeError(f"Failed to fetch One Pagers: {e}") from e

    def get_registry_status_counts(self, filter: RegistryFilter) -> dict[str, int]:
        """Aggregate count of One Pagers by one_pager_status.
        
        Applies the same filter as get_registry(), then groups by one_pager_status
        to produce status counts for metric cards.
        
        All filter values are escaped to prevent SQL injection.
        """
        fqn = f"{self._fqn_prefix}.one_pager_status"
        
        # Build WHERE clause (same as get_registry)
        where_clauses = []
        
        if filter.product_name:
            escaped = self._escape_sql_string(filter.product_name)
            where_clauses.append(f"LOWER(product_name) LIKE LOWER('%{escaped}%')")
        
        if filter.op_status:
            escaped = self._escape_sql_string(filter.op_status)
            where_clauses.append(f"one_pager_status = '{escaped}'")
        
        if filter.dp_status:
            escaped = self._escape_sql_string(filter.dp_status)
            where_clauses.append(f"data_product_status = '{escaped}'")
        
        if filter.owner:
            escaped = self._escape_sql_string(filter.owner)
            where_clauses.append(
                f"(LOWER(owner_name) LIKE LOWER('%{escaped}%') "
                f"OR LOWER(owner_email) LIKE LOWER('%{escaped}%'))"
            )
        
        if filter.domain:
            escaped = self._escape_sql_string(filter.domain)
            where_clauses.append(f"business_domain = '{escaped}'")
        
        if filter.data_product_type:
            escaped = self._escape_sql_string(filter.data_product_type)
            where_clauses.append(f"data_product_type = '{escaped}'")
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # Group by status and count
        query = (
            f"SELECT one_pager_status, COUNT(*) as count "
            f"FROM {fqn} "
            f"WHERE {where_clause} "
            f"GROUP BY one_pager_status"
        )
        
        try:
            response = self._connection.execute_statement(query)
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame()
            
            counts = {}
            for _, row in df.iterrows():
                status = str(row["one_pager_status"])
                count = int(row["count"])
                counts[status] = count
            
            return counts
        except Exception as e:
            logger.error(f"Failed to fetch status counts: {e}")
            raise RuntimeError(f"Failed to fetch status counts: {e}") from e

    # ========================================================================
    # Preview Page Methods
    # ========================================================================

    def get_one_pager(self, one_pager_id: str) -> PreviewData | None:
        """Fetch a complete One Pager for preview display.
        
        Composes data from multiple sources:
        - Header from one_pager_status table
        - Document from volume (pre-approval) or Git (post-approval)
        - Change log, review comments, lock from respective tables
        """
        # Fetch header
        header = self.get_one_pager_status(one_pager_id)
        if not header:
            return None

        # Fetch document
        document = self.read_document(one_pager_id, header.version)
        if not document:
            return None

        # Fetch operational data
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
        """Fetch header metadata for a single One Pager from one_pager_status table."""
        fqn = f"{self._fqn_prefix}.one_pager_status"
        
        # Use parameterized query to prevent SQL injection
        query = (
            f"SELECT "
            f"one_pager_id, product_name, owner_name, owner_initials, owner_email, "
            f"version, one_pager_status, data_product_status, "
            f"created_at, last_updated_at, last_updated_by "
            f"FROM {fqn} "
            f"WHERE one_pager_id = :one_pager_id"
        )
        
        try:
            response = self._connection.execute_statement(
                query, parameters={"one_pager_id": one_pager_id}
            )
            
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            if not rows:
                return None
            
            row = rows[0]
            row_dict = dict(zip(columns, row, strict=False))
            
            return OnePagerHeader(
                one_pager_id=row_dict["one_pager_id"],
                product_name=row_dict["product_name"],
                owner_name=row_dict["owner_name"],
                owner_initials=row_dict["owner_initials"],
                owner_email=row_dict["owner_email"],
                version=row_dict["version"],
                one_pager_status=row_dict["one_pager_status"],
                data_product_status=row_dict["data_product_status"],
                created_at=_to_datetime(row_dict["created_at"]),
                last_updated_at=_to_datetime(row_dict["last_updated_at"]),
                last_updated_by=row_dict["last_updated_by"],
            )
        except Exception as e:
            logger.error(f"Failed to fetch one_pager_status for {one_pager_id}: {e}")
            raise RuntimeError(f"Failed to fetch one_pager_status: {e}") from e

    def read_document(self, one_pager_id: str, version: str | None = None) -> OnePagerDocument | None:
        """Read the YAML document content for a One Pager from the document store."""
        return self._document_store.read(one_pager_id, version)

    def get_change_log(self, one_pager_id: str) -> list[ChangeLogEntry]:
        """Fetch the change log for a One Pager (newest-first)."""
        fqn = f"{self._fqn_prefix}.change_log"
        
        query = (
            f"SELECT "
            f"id, one_pager_id, version, event_type, author_initials, author_name, "
            f"summary, from_status, to_status, status_field, created_at "
            f"FROM {fqn} "
            f"WHERE one_pager_id = :one_pager_id "
            f"ORDER BY created_at DESC"
        )
        
        try:
            response = self._connection.execute_statement(
                query, parameters={"one_pager_id": one_pager_id}
            )
            
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            entries = []
            for row in rows:
                row_dict = dict(zip(columns, row, strict=False))
                entries.append(ChangeLogEntry(
                    id=int(row_dict["id"]),
                    one_pager_id=row_dict["one_pager_id"],
                    version=row_dict["version"],
                    event_type=row_dict["event_type"],
                    author_initials=row_dict["author_initials"],
                    author_name=row_dict["author_name"],
                    summary=row_dict["summary"],
                    created_at=_to_datetime(row_dict["created_at"]),
                    from_status=row_dict.get("from_status"),
                    to_status=row_dict.get("to_status"),
                    status_field=row_dict.get("status_field"),
                ))
            
            return entries
        except Exception as e:
            logger.error(f"Failed to fetch change_log for {one_pager_id}: {e}")
            raise RuntimeError(f"Failed to fetch change_log: {e}") from e

    def get_review_comments(self, one_pager_id: str) -> list[ReviewComment]:
        """Fetch review comments for a One Pager."""
        fqn = f"{self._fqn_prefix}.review_comments"
        
        query = (
            f"SELECT "
            f"id, one_pager_id, version, section, reviewer_initials, reviewer_name, "
            f"comment, resolved, resolved_by, created_at, resolved_at "
            f"FROM {fqn} "
            f"WHERE one_pager_id = :one_pager_id "
            f"ORDER BY created_at ASC"
        )
        
        try:
            response = self._connection.execute_statement(
                query, parameters={"one_pager_id": one_pager_id}
            )
            
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            comments = []
            for row in rows:
                row_dict = dict(zip(columns, row, strict=False))
                comments.append(ReviewComment(
                    id=int(row_dict["id"]),
                    one_pager_id=row_dict["one_pager_id"],
                    version=row_dict["version"],
                    section=row_dict.get("section"),
                    reviewer_initials=row_dict["reviewer_initials"],
                    reviewer_name=row_dict["reviewer_name"],
                    comment=row_dict["comment"],
                    resolved=bool(row_dict.get("resolved", False)),
                    created_at=_to_datetime(row_dict["created_at"]),
                    resolved_by=row_dict.get("resolved_by"),
                    resolved_at=(
                        _to_datetime(row_dict["resolved_at"])
                        if row_dict.get("resolved_at")
                        else None
                    ),
                ))
            
            return comments
        except Exception as e:
            logger.error(f"Failed to fetch review_comments for {one_pager_id}: {e}")
            raise RuntimeError(f"Failed to fetch review_comments: {e}") from e

    def get_lock(self, one_pager_id: str) -> LockInfo | None:
        """Check if a One Pager is currently locked for editing."""
        fqn = f"{self._fqn_prefix}.locks"
        
        query = (
            f"SELECT "
            f"one_pager_id, locked_by_initials, locked_by_name, session_id, "
            f"acquired_at, last_heartbeat, expires_at "
            f"FROM {fqn} "
            f"WHERE one_pager_id = :one_pager_id"
        )
        
        try:
            response = self._connection.execute_statement(
                query, parameters={"one_pager_id": one_pager_id}
            )
            
            schema = response.manifest.schema if response.manifest else None
            columns = [col.name for col in (schema.columns if schema else None) or []]
            rows = (response.result.data_array if response.result else None) or []
            
            if not rows:
                return None
            
            row = rows[0]
            row_dict = dict(zip(columns, row, strict=False))
            
            return LockInfo(
                one_pager_id=row_dict["one_pager_id"],
                locked_by_initials=row_dict["locked_by_initials"],
                locked_by_name=row_dict["locked_by_name"],
                session_id=row_dict["session_id"],
                acquired_at=_to_datetime(row_dict["acquired_at"]),
                last_heartbeat=_to_datetime(row_dict["last_heartbeat"]),
                expires_at=_to_datetime(row_dict["expires_at"]),
            )
        except Exception as e:
            logger.error(f"Failed to fetch lock for {one_pager_id}: {e}")
            raise RuntimeError(f"Failed to fetch lock: {e}") from e

    # ========================================================================
    # Create One Pager Methods
    # ========================================================================

    def get_sequence_value(self, id_type: str) -> int:
        """Read the last assigned value of an id_sequences counter."""
        fqn = f"{self._fqn_prefix}.id_sequences"
        response = self._connection.execute_statement(
            f"SELECT last_value FROM {fqn} WHERE id_type = :id_type",  # noqa: S608
            parameters={"id_type": id_type},
        )
        rows = _rows(response)
        if not rows:
            msg = (
                f"id_sequences has no row for {id_type}. "
                "Please ensure the Liquibase migrations have been applied."
            )
            raise RuntimeError(msg)
        return int(rows[0]["last_value"])

    def compare_and_set_sequence(self, id_type: str, expected: int, new: int) -> bool:
        """Advance the counter only if it still holds ``expected``.

        Success is decided by the UPDATE's ``num_affected_rows`` (exactly 1).
        A re-read of the counter is not sufficient: if another writer advanced
        it from the same ``expected`` value, a re-read would also show ``new``.
        A Delta concurrent-modification conflict is reported as False so the
        caller retries.
        """
        fqn = f"{self._fqn_prefix}.id_sequences"
        try:
            response = self._connection.execute_statement(
                f"UPDATE {fqn} SET last_value = :new "  # noqa: S608
                "WHERE id_type = :id_type AND last_value = :expected",
                parameters={"new": new, "id_type": id_type, "expected": expected},
            )
        except RuntimeError as e:
            if "concurrent" in str(e).lower():
                logger.info(f"Concurrent update on id_sequences ({id_type}): {e}")
                return False
            raise
        rows = _rows(response)
        if not rows or "num_affected_rows" not in rows[0]:
            msg = "UPDATE on id_sequences did not report num_affected_rows"
            raise RuntimeError(msg)
        return int(rows[0]["num_affected_rows"]) == 1

    def get_one_pager_ids_for_data_product(self, data_product: str) -> list[str]:
        fqn = f"{self._fqn_prefix}.one_pager_status"
        response = self._connection.execute_statement(
            f"SELECT one_pager_id FROM {fqn} "  # noqa: S608
            "WHERE data_product = :data_product ORDER BY one_pager_id",
            parameters={"data_product": data_product},
        )
        return [str(r["one_pager_id"]) for r in _rows(response)]

    def get_authorized_users(self, one_pager_id: str) -> list[AuthorizedUser]:
        fqn = f"{self._fqn_prefix}.one_pager_authorized_users"
        response = self._connection.execute_statement(
            f"SELECT one_pager_id, user_initials, user_name, user_email, "  # noqa: S608
            f"user_team, role FROM {fqn} WHERE one_pager_id = :one_pager_id "
            "ORDER BY role, user_initials",
            parameters={"one_pager_id": one_pager_id},
        )
        return [
            AuthorizedUser(
                one_pager_id=r["one_pager_id"],
                user_initials=r["user_initials"],
                user_name=r["user_name"],
                user_email=r["user_email"],
                user_team=r.get("user_team"),
                role=r["role"],
            )
            for r in _rows(response)
        ]

    def insert_authorized_users(self, users: list[AuthorizedUser]) -> None:
        if not users:
            return
        fqn = f"{self._fqn_prefix}.one_pager_authorized_users"
        values = []
        parameters: dict[str, object] = {}
        for i, user in enumerate(users):
            values.append(
                f"(:id_{i}, :initials_{i}, :name_{i}, :email_{i}, :team_{i}, :role_{i})"
            )
            parameters.update(
                {
                    f"id_{i}": user.one_pager_id,
                    f"initials_{i}": user.user_initials,
                    f"name_{i}": user.user_name,
                    f"email_{i}": user.user_email,
                    f"team_{i}": user.user_team,
                    f"role_{i}": user.role,
                }
            )
        self._connection.execute_statement(
            f"INSERT INTO {fqn} "  # noqa: S608
            "(one_pager_id, user_initials, user_name, user_email, user_team, role) "
            f"VALUES {', '.join(values)}",
            parameters=parameters,
        )

    def append_change_log(self, entry: ChangeLogEntry) -> None:
        fqn = f"{self._fqn_prefix}.change_log"
        self._connection.execute_statement(
            f"INSERT INTO {fqn} "  # noqa: S608
            "(one_pager_id, version, event_type, author_initials, author_name, "
            "summary, from_status, to_status, status_field, created_at) VALUES "
            "(:one_pager_id, :version, :event_type, :author_initials, :author_name, "
            ":summary, :from_status, :to_status, :status_field, :created_at)",
            parameters={
                "one_pager_id": entry.one_pager_id,
                "version": entry.version,
                "event_type": entry.event_type,
                "author_initials": entry.author_initials,
                "author_name": entry.author_name,
                "summary": entry.summary,
                "from_status": entry.from_status,
                "to_status": entry.to_status,
                "status_field": entry.status_field,
                "created_at": entry.created_at,
            },
        )

    def insert_one_pager_status(self, row: OnePagerStatusRow) -> None:
        fqn = f"{self._fqn_prefix}.one_pager_status"
        columns = ", ".join(_ONE_PAGER_STATUS_COLUMNS)
        markers = ", ".join(f":{c}" for c in _ONE_PAGER_STATUS_COLUMNS)
        # NULL parameters are untyped; cast the nullable TIMESTAMP explicitly.
        markers = markers.replace(
            ":reviewed_at", "CAST(:reviewed_at AS TIMESTAMP)"
        )
        self._connection.execute_statement(
            f"INSERT INTO {fqn} ({columns}) VALUES ({markers})",  # noqa: S608
            parameters={c: getattr(row, c) for c in _ONE_PAGER_STATUS_COLUMNS},
        )

    def delete_one_pager_records(self, one_pager_id: str) -> None:
        # Status row first so the One Pager disappears from the Registry even
        # if one of the later deletes fails.
        for table in ("one_pager_status", "one_pager_authorized_users", "change_log"):
            self._connection.execute_statement(
                f"DELETE FROM {self._fqn_prefix}.{table} "  # noqa: S608
                "WHERE one_pager_id = :one_pager_id",
                parameters={"one_pager_id": one_pager_id},
            )
