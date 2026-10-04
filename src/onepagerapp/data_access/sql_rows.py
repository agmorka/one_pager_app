"""Reading Statement Execution API responses into Python values.

Pure helpers used by ``LakehouseAccess``: they turn a ``StatementResponse``
into rows and parse the string values the API returns (booleans,
timestamps) into Python types. No SQL is executed here.
"""

from datetime import datetime
from typing import Any

import pandas as pd
from databricks.sdk.service.sql import StatementResponse

from onepagerapp.models import OnePagerStatusRow, UseCase


def statement_table(
    response: StatementResponse,
) -> tuple[list[str | None], list[list[Any]]]:
    """Column names and raw rows of a statement result (empty when absent)."""
    schema = response.manifest.schema if response.manifest else None
    columns = [col.name for col in (schema.columns if schema else None) or []]
    rows = (response.result.data_array if response.result else None) or []
    return columns, rows


def response_rows(response: StatementResponse) -> list[dict[str, Any]]:
    """Convert a StatementResponse into a list of column → value dicts."""
    columns, rows = statement_table(response)
    keys = [column or "" for column in columns]
    return [dict(zip(keys, row, strict=False)) for row in rows]


def affected_rows(response: StatementResponse) -> int:
    """Read num_affected_rows from an UPDATE/INSERT/DELETE response."""
    rows = response_rows(response)
    if not rows:
        return 0
    value = rows[0].get("num_affected_rows", next(iter(rows[0].values()), 0))
    return int(value or 0)


def parse_bool(value: object) -> bool:
    """Parse a boolean; the Statement API returns "true"/"false" strings."""
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def parse_timestamp(value: object) -> datetime:
    """Parse a timestamp string from the Statement API into a datetime."""
    if isinstance(value, datetime):
        return value
    parsed: datetime = pd.Timestamp(value).to_pydatetime()
    return parsed


def optional_timestamp(value: object) -> datetime | None:
    """Parse a nullable timestamp ("" and None are NULL)."""
    return None if value in (None, "") else parse_timestamp(value)


def escape_sql_string(value: str) -> str:
    """Escape a string for a SQL string literal to prevent injection.

    Replaces single quotes with doubled quotes per SQL standard.
    """
    return value.replace("'", "''")


def like_pattern(text: str) -> str:
    """Build a LIKE "contains" pattern, escaping the LIKE wildcards.

    Backslash is the default LIKE escape character in Databricks SQL.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def row_to_status_row(row: dict[str, Any]) -> OnePagerStatusRow:
    """Build a ``one_pager_status`` row from a result row."""
    return OnePagerStatusRow(
        one_pager_id=str(row["one_pager_id"]),
        data_product=str(row["data_product"]),
        product_name=str(row["product_name"]),
        business_domain=str(row["business_domain"]),
        data_product_type=str(row["data_product_type"]),
        one_pager_status=str(row["one_pager_status"]),
        data_product_status=str(row["data_product_status"]),
        version=str(row["version"]),
        owner_name=str(row["owner_name"]),
        owner_initials=str(row["owner_initials"]),
        owner_email=str(row["owner_email"]),
        owner_team=row.get("owner_team"),
        created_by=str(row["created_by"]),
        created_at=parse_timestamp(row["created_at"]),
        last_updated_at=parse_timestamp(row["last_updated_at"]),
        last_updated_by=str(row["last_updated_by"]),
        reviewed_at=optional_timestamp(row.get("reviewed_at")),
        reviewed_by=row.get("reviewed_by"),
        structure_definition=str(row["structure_definition"]),
        pending_pr=parse_bool(row.get("pending_pr")),
    )


def row_to_use_case(row: dict[str, Any]) -> UseCase:
    """Build a Use Case (with its reference count) from a result row."""
    return UseCase(
        use_case_id=str(row["use_case_id"]),
        persona=str(row["persona"]),
        goal=str(row["goal"]),
        scenario=str(row["scenario"]),
        decision_enabled=str(row["decision_enabled"]),
        priority=str(row["priority"]),
        deprecated=parse_bool(row["deprecated"]),
        created_by=str(row["created_by"]),
        created_at=parse_timestamp(row["created_at"]),
        last_updated_by=str(row["last_updated_by"]),
        last_updated_at=parse_timestamp(row["last_updated_at"]),
        reference_count=int(row.get("reference_count") or 0),
    )
