"""Real data access implementation using the Databricks SQL Statement Execution API."""

import logging
from datetime import datetime
from typing import Any

import pandas as pd
from databricks.sdk.service.sql import StatementResponse

from onepagerapp.config import AppConfig
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.data_access.connection import (
    DatabricksConnection,
    SqlParameterValue,
    StatementFailedError,
)
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
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)
from onepagerapp.use_cases import format_use_case_id

logger = logging.getLogger(__name__)

# Attempts to allocate an ID from id_sequences before giving up (Data_Model.md §4)
_ID_ALLOCATION_ATTEMPTS = 5

_USE_CASE_COLUMNS = (
    "use_case_id, persona, goal, scenario, decision_enabled, priority, deprecated, "
    "created_by, created_at, last_updated_by, last_updated_at"
)


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
                created_at=row_dict["created_at"],
                last_updated_at=row_dict["last_updated_at"],
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
                    id=row_dict["id"],
                    one_pager_id=row_dict["one_pager_id"],
                    version=row_dict["version"],
                    event_type=row_dict["event_type"],
                    author_initials=row_dict["author_initials"],
                    author_name=row_dict["author_name"],
                    summary=row_dict["summary"],
                    created_at=row_dict["created_at"],
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
                    id=row_dict["id"],
                    one_pager_id=row_dict["one_pager_id"],
                    version=row_dict["version"],
                    section=row_dict.get("section"),
                    reviewer_initials=row_dict["reviewer_initials"],
                    reviewer_name=row_dict["reviewer_name"],
                    comment=row_dict["comment"],
                    resolved=bool(row_dict.get("resolved", False)),
                    created_at=row_dict["created_at"],
                    resolved_by=row_dict.get("resolved_by"),
                    resolved_at=row_dict.get("resolved_at"),
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
                acquired_at=row_dict["acquired_at"],
                last_heartbeat=row_dict["last_heartbeat"],
                expires_at=row_dict["expires_at"],
            )
        except Exception as e:
            logger.error(f"Failed to fetch lock for {one_pager_id}: {e}")
            raise RuntimeError(f"Failed to fetch lock: {e}") from e

    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================
    # Every user-supplied value is passed as a bound parameter (:name markers);
    # only table names and integers computed here are formatted into SQL.

    @staticmethod
    def _response_rows(response: StatementResponse) -> list[dict[str, Any]]:
        """Convert a StatementResponse into a list of column → value dicts."""
        schema = response.manifest.schema if response.manifest else None
        columns = [col.name or "" for col in (schema.columns if schema else None) or []]
        rows = (response.result.data_array if response.result else None) or []
        return [dict(zip(columns, row, strict=False)) for row in rows]

    @classmethod
    def _affected_rows(cls, response: StatementResponse) -> int:
        """Read num_affected_rows from an UPDATE/INSERT/DELETE response."""
        rows = cls._response_rows(response)
        if not rows:
            return 0
        value = rows[0].get("num_affected_rows", next(iter(rows[0].values()), 0))
        return int(value or 0)

    @staticmethod
    def _parse_bool(value: object) -> bool:
        """Parse a boolean; the Statement API returns "true"/"false" strings."""
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        """Parse a timestamp string from the Statement API into a datetime."""
        if isinstance(value, datetime):
            return value
        parsed: datetime = pd.Timestamp(value).to_pydatetime()
        return parsed

    @classmethod
    def _row_to_use_case(cls, row: dict[str, Any]) -> UseCase:
        return UseCase(
            use_case_id=str(row["use_case_id"]),
            persona=str(row["persona"]),
            goal=str(row["goal"]),
            scenario=str(row["scenario"]),
            decision_enabled=str(row["decision_enabled"]),
            priority=str(row["priority"]),
            deprecated=cls._parse_bool(row["deprecated"]),
            created_by=str(row["created_by"]),
            created_at=cls._parse_timestamp(row["created_at"]),
            last_updated_by=str(row["last_updated_by"]),
            last_updated_at=cls._parse_timestamp(row["last_updated_at"]),
            reference_count=int(row.get("reference_count") or 0),
        )

    @staticmethod
    def _like_pattern(text: str) -> str:
        """Build a LIKE "contains" pattern, escaping the LIKE wildcards.

        Backslash is the default LIKE escape character in Databricks SQL.
        """
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def _use_cases_where(
        self, filter: UseCaseFilter
    ) -> tuple[str, dict[str, SqlParameterValue]]:
        """Build the WHERE clause and its parameters for a UseCaseFilter."""
        clauses: list[str] = []
        parameters: dict[str, SqlParameterValue] = {}
        if not filter.include_deprecated:
            clauses.append("uc.deprecated = false")
        if filter.search:
            clauses.append(
                "(lower(uc.persona) LIKE lower(:search) "
                "OR lower(uc.goal) LIKE lower(:search))"
            )
            parameters["search"] = self._like_pattern(filter.search)
        if filter.priority:
            clauses.append("uc.priority = :priority")
            parameters["priority"] = filter.priority
        return (" AND ".join(clauses) if clauses else "1=1"), parameters

    def _use_cases_select(self, where_clause: str) -> str:
        """SELECT for use_cases rows with their One Pager reference counts."""
        uc_columns = ", ".join(f"uc.{c.strip()}" for c in _USE_CASE_COLUMNS.split(","))
        return (
            f"SELECT {uc_columns}, "  # noqa: S608
            f"COALESCE(ref.reference_count, 0) AS reference_count "
            f"FROM {self._fqn_prefix}.use_cases uc "
            f"LEFT JOIN ("
            f"SELECT use_case_id, COUNT(*) AS reference_count "
            f"FROM {self._fqn_prefix}.use_case_references GROUP BY use_case_id"
            f") ref ON uc.use_case_id = ref.use_case_id "
            f"WHERE {where_clause}"
        )

    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
    ) -> UseCasePage:
        """Query Use Cases with server-side filtering and pagination."""
        where_clause, parameters = self._use_cases_where(filter)
        count_query = (
            f"SELECT COUNT(*) AS total FROM {self._fqn_prefix}.use_cases uc "  # noqa: S608
            f"WHERE {where_clause}"
        )
        offset = (int(page) - 1) * int(page_size)
        page_query = (
            f"{self._use_cases_select(where_clause)} "
            f"ORDER BY uc.use_case_id LIMIT {int(page_size)} OFFSET {offset}"
        )
        try:
            count_rows = self._response_rows(
                self._connection.execute_statement(count_query, parameters)
            )
            total_rows = int(count_rows[0]["total"]) if count_rows else 0
            rows = self._response_rows(
                self._connection.execute_statement(page_query, parameters)
            )
        except Exception as e:
            logger.error(f"Failed to fetch use cases: {e}")
            msg = f"Failed to fetch Use Cases: {e}"
            raise RuntimeError(msg) from e
        return UseCasePage(
            rows=[self._row_to_use_case(row) for row in rows],
            total_rows=total_rows,
            page=page,
            page_size=page_size,
        )

    def get_use_case(self, use_case_id: str) -> UseCase | None:
        """Fetch a single Use Case with its reference count."""
        query = self._use_cases_select("uc.use_case_id = :use_case_id")
        try:
            rows = self._response_rows(
                self._connection.execute_statement(
                    query, {"use_case_id": use_case_id}
                )
            )
        except Exception as e:
            logger.error(f"Failed to fetch use case {use_case_id}: {e}")
            msg = f"Failed to fetch Use Case: {e}"
            raise RuntimeError(msg) from e
        return self._row_to_use_case(rows[0]) if rows else None

    def get_use_case_references(self, use_case_id: str) -> list[str]:
        """List the One Pager IDs referencing a Use Case."""
        query = (
            f"SELECT one_pager_id FROM {self._fqn_prefix}.use_case_references "  # noqa: S608
            f"WHERE use_case_id = :use_case_id ORDER BY one_pager_id"
        )
        try:
            rows = self._response_rows(
                self._connection.execute_statement(
                    query, {"use_case_id": use_case_id}
                )
            )
        except Exception as e:
            logger.error(f"Failed to fetch references for {use_case_id}: {e}")
            msg = f"Failed to fetch Use Case references: {e}"
            raise RuntimeError(msg) from e
        return [str(row["one_pager_id"]) for row in rows]

    def _next_id(self, id_type: str) -> int:
        """Atomically allocate the next value of an id_sequences counter.

        Compare-and-swap: the UPDATE only matches while last_value still holds
        the value read, and success is decided by its num_affected_rows. (Re-
        reading the counter afterwards cannot tell two racing writers apart —
        both would see the new value and hand out the same ID.) Delta write
        conflicts surface as failed statements and are retried as well.

        Args:
            id_type: Counter row to increment ("OP", "UC" or "BR").

        Returns:
            The newly allocated numeric value.

        Raises:
            RuntimeError: If the counter row is missing or every attempt lost.
        """
        fqn = f"{self._fqn_prefix}.id_sequences"
        read_query = f"SELECT last_value FROM {fqn} WHERE id_type = :id_type"  # noqa: S608
        update_query = (
            f"UPDATE {fqn} SET last_value = :new_value "  # noqa: S608
            f"WHERE id_type = :id_type AND last_value = :current_value"
        )
        for attempt in range(1, _ID_ALLOCATION_ATTEMPTS + 1):
            rows = self._response_rows(
                self._connection.execute_statement(read_query, {"id_type": id_type})
            )
            if not rows:
                msg = f"id_sequences has no row for id_type '{id_type}'"
                raise RuntimeError(msg)
            current = int(rows[0]["last_value"])
            try:
                response = self._connection.execute_statement(
                    update_query,
                    {
                        "id_type": id_type,
                        "new_value": current + 1,
                        "current_value": current,
                    },
                )
            except StatementFailedError as e:
                logger.warning(
                    f"ID allocation for {id_type} conflicted "
                    f"(attempt {attempt}/{_ID_ALLOCATION_ATTEMPTS}): {e}"
                )
                continue
            if self._affected_rows(response) == 1:
                return current + 1
            logger.warning(
                f"ID allocation for {id_type} lost a race "
                f"(attempt {attempt}/{_ID_ALLOCATION_ATTEMPTS}), retrying"
            )
        msg = (
            f"Could not allocate a new {id_type} ID after "
            f"{_ID_ALLOCATION_ATTEMPTS} attempts"
        )
        raise RuntimeError(msg)

    @staticmethod
    def _use_case_parameters(data: UseCaseInput) -> dict[str, SqlParameterValue]:
        return {
            "persona": data.persona,
            "goal": data.goal,
            "scenario": data.scenario,
            "decision_enabled": data.decision_enabled,
            "priority": data.priority,
        }

    def create_use_case(self, data: UseCaseInput, user_initials: str) -> str:
        """Allocate a UC-### ID and insert the Use Case.

        If the INSERT fails after the ID was allocated, that ID is skipped —
        a gap in the sequence is harmless (Data_Model.md §4).
        """
        use_case_id = format_use_case_id(self._next_id("UC"))
        query = (
            f"INSERT INTO {self._fqn_prefix}.use_cases ({_USE_CASE_COLUMNS}) "  # noqa: S608
            f"VALUES (:use_case_id, :persona, :goal, :scenario, :decision_enabled, "
            f":priority, false, :user_initials, current_timestamp(), "
            f":user_initials, current_timestamp())"
        )
        parameters = {
            **self._use_case_parameters(data),
            "use_case_id": use_case_id,
            "user_initials": user_initials,
        }
        try:
            self._connection.execute_statement(query, parameters)
        except Exception as e:
            logger.error(f"Failed to create use case {use_case_id}: {e}")
            msg = f"Failed to create Use Case: {e}"
            raise RuntimeError(msg) from e
        return use_case_id

    def _update_use_case_row(
        self,
        use_case_id: str,
        set_clause: str,
        parameters: dict[str, SqlParameterValue],
    ) -> None:
        """Run an UPDATE on one Use Case, stamping the audit columns."""
        query = (
            f"UPDATE {self._fqn_prefix}.use_cases SET {set_clause}, "  # noqa: S608
            f"last_updated_by = :user_initials, "
            f"last_updated_at = current_timestamp() "
            f"WHERE use_case_id = :use_case_id"
        )
        try:
            response = self._connection.execute_statement(
                query, {**parameters, "use_case_id": use_case_id}
            )
        except Exception as e:
            logger.error(f"Failed to update use case {use_case_id}: {e}")
            msg = f"Failed to update Use Case: {e}"
            raise RuntimeError(msg) from e
        if self._affected_rows(response) == 0:
            msg = f"Use Case {use_case_id} not found"
            raise NotFoundError(msg)

    def update_use_case(
        self, use_case_id: str, data: UseCaseInput, user_initials: str
    ) -> None:
        """Update a Use Case's editable fields."""
        self._update_use_case_row(
            use_case_id,
            "persona = :persona, goal = :goal, scenario = :scenario, "
            "decision_enabled = :decision_enabled, priority = :priority",
            {**self._use_case_parameters(data), "user_initials": user_initials},
        )

    def set_use_case_deprecated(
        self, use_case_id: str, deprecated: bool, user_initials: str  # noqa: FBT001
    ) -> None:
        """Deprecate or restore a Use Case."""
        self._update_use_case_row(
            use_case_id,
            "deprecated = :deprecated",
            {"deprecated": deprecated, "user_initials": user_initials},
        )
