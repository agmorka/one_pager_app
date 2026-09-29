"""Databricks connection and SQL execution layer."""

import logging
import time
from collections.abc import Mapping

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
_FAILED_STATES = (StatementState.FAILED, StatementState.CANCELED, StatementState.CLOSED)

SqlParameterValue = str | int | bool | None


class StatementFailedError(RuntimeError):
    """The SQL warehouse accepted a statement but reported it as failed."""


def to_statement_parameters(
    parameters: Mapping[str, SqlParameterValue],
) -> list[StatementParameterListItem]:
    """Convert a name → value mapping into SQL Statement API parameters.

    Values are bound server-side and referenced in SQL as ``:name`` markers,
    so user input never becomes part of the SQL text.

    Args:
        parameters: Parameter values keyed by marker name. ``None`` binds NULL.

    Returns:
        Parameter list for ``statement_execution.execute_statement``.
    """
    items = []
    for name, value in parameters.items():
        if isinstance(value, bool):
            items.append(
                StatementParameterListItem(
                    name=name, value=str(value).lower(), type="BOOLEAN"
                )
            )
        elif isinstance(value, int):
            items.append(
                StatementParameterListItem(name=name, value=str(value), type="INT")
            )
        elif value is None:
            items.append(StatementParameterListItem(name=name, type="STRING"))
        else:
            items.append(
                StatementParameterListItem(name=name, value=value, type="STRING")
            )
    return items


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
        parameters: Mapping[str, SqlParameterValue] | None = None,
    ) -> StatementResponse:
        """Execute a SQL statement with retry logic.

        Args:
            statement: SQL statement to execute. User-supplied values must be
                referenced as named markers (``:name``) and passed in
                ``parameters`` — never formatted into the statement text.
            parameters: Values for the named markers in ``statement``.

        Returns:
            StatementResponse from Databricks.

        Raises:
            StatementFailedError: If the warehouse reports the statement as
                failed, canceled, or closed.
            RuntimeError: If all retry attempts fail.
        """
        bound = to_statement_parameters(parameters) if parameters else None
        token = self._get_access_token()
        
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


def _raise_if_failed(response: StatementResponse) -> None:
    """Raise StatementFailedError when the warehouse reports a failed statement.

    Without this check a failed statement looks like an empty result, which
    would silently swallow failed writes.
    """
    status = response.status
    if status is None or status.state not in _FAILED_STATES:
        return
    detail = status.error.message if status.error else "no error details"
    msg = f"SQL statement {status.state.value}: {detail}"
    raise StatementFailedError(msg)
