"""Databricks connection and SQL execution layer."""

import logging
import time
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    StatementParameterListItem,
    StatementResponse,
    StatementState,
)
import streamlit as st

from onepagerapp.config import AppConfig

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5
_STATEMENT_WAIT_TIMEOUT = "30s"


class DatabricksConnection:
    """Manages connection to Databricks SQL warehouse and executes statements.

    Handles token management, retry logic, and SQL statement execution.
    Separates connection concerns from business logic/queries.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize connection with configuration.

        Args:
            config: Application configuration containing warehouse details.
        """
        self._config = config
        self._ws = WorkspaceClient()

    def _get_access_token(self) -> str:
        """Get OAuth token for current user.

        In Databricks Apps: retrieves the end-user's token from 
        x-forwarded-access-token header so that Unity Catalog permissions 
        are enforced per user.

        Locally (local-integration): falls back to the Databricks CLI profile.

        Returns:
            OAuth token string.
        """
        user_token = st.context.headers.get("x-forwarded-access-token")
        if user_token:
            return user_token
        
        headers = self._ws.config.authenticate()
        return headers["Authorization"].removeprefix("Bearer ")

    def execute_statement(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> StatementResponse:
        """Execute a SQL statement with retry logic.

        Values are bound server-side via named parameter markers, e.g.
        ``WHERE one_pager_id = :one_pager_id`` with
        ``parameters={"one_pager_id": "OP-0001"}``. Never interpolate
        user-supplied values into ``statement``.

        Only connection errors are retried. A statement that reaches the
        warehouse and fails is reported immediately (not retried), so
        non-idempotent writes are never executed twice.

        Args:
            statement: SQL statement to execute.
            parameters: Optional mapping of marker name to value.

        Returns:
            StatementResponse from Databricks.

        Raises:
            RuntimeError: If the statement fails or all retry attempts fail.
        """
        token = self._get_access_token()
        bound = to_statement_parameters(parameters)
        
        # auth_type is required: the Databricks Apps runtime sets OAuth env vars,
        # which conflict with this explicit token unless we pin the auth method
        client = WorkspaceClient(host=self._ws.config.host, token=token, auth_type="pat")

        last_err: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = client.statement_execution.execute_statement(
                    statement=statement,
                    warehouse_id=self._config.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout=_STATEMENT_WAIT_TIMEOUT,
                    parameters=bound,
                )
            except Exception as exc:
                last_err = exc
                if attempt == _MAX_RETRIES:
                    raise
                logger.warning(
                    "SQL warehouse connection attempt %d/%d failed, retrying in %ds",
                    attempt,
                    _MAX_RETRIES,
                    _RETRY_DELAY_SECONDS,
                )
                time.sleep(_RETRY_DELAY_SECONDS)
            else:
                _raise_if_failed(response)
                return response
        msg = "All connection attempts failed"
        raise RuntimeError(msg) from last_err


def to_statement_parameters(
    parameters: Mapping[str, Any] | None,
) -> list[StatementParameterListItem] | None:
    """Convert a name -> value mapping into Statement Execution API parameters.

    ``None`` is bound as SQL NULL. Booleans, integers and timestamps get an
    explicit type so they compare correctly with typed columns; everything
    else is bound as STRING.
    """
    if not parameters:
        return None
    items = []
    for name, value in parameters.items():
        if value is None:
            items.append(StatementParameterListItem(name=name, value=None))
        elif isinstance(value, bool):
            items.append(
                StatementParameterListItem(
                    name=name, value="true" if value else "false", type="BOOLEAN"
                )
            )
        elif isinstance(value, int):
            items.append(
                StatementParameterListItem(name=name, value=str(value), type="BIGINT")
            )
        elif isinstance(value, datetime):
            items.append(
                StatementParameterListItem(
                    name=name, value=value.isoformat(), type="TIMESTAMP"
                )
            )
        elif isinstance(value, date):
            items.append(
                StatementParameterListItem(
                    name=name, value=value.isoformat(), type="DATE"
                )
            )
        else:
            items.append(StatementParameterListItem(name=name, value=str(value)))
    return items


def _raise_if_failed(response: StatementResponse) -> None:
    """Raise if the warehouse reports the statement as failed/cancelled."""
    status = response.status
    if status is None or status.state is None:
        return
    if status.state in (
        StatementState.FAILED,
        StatementState.CANCELED,
        StatementState.CLOSED,
    ):
        detail = status.error.message if status.error else status.state.value
        msg = f"SQL statement {status.state.value}: {detail}"
        raise RuntimeError(msg)
