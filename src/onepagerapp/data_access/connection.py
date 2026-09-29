"""Databricks connection and SQL execution layer."""

import logging
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementResponse
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

    def execute_statement(self, statement: str) -> StatementResponse:
        """Execute a SQL statement with retry logic.

        Args:
            statement: SQL statement to execute.

        Returns:
            StatementResponse from Databricks.

        Raises:
            RuntimeError: If all retry attempts fail.
        """
        token = self._get_access_token()
        
        # auth_type is required: the Databricks Apps runtime sets OAuth env vars,
        # which conflict with this explicit token unless we pin the auth method
        client = WorkspaceClient(host=self._ws.config.host, token=token, auth_type="pat")

        last_err: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return client.statement_execution.execute_statement(
                    statement=statement,
                    warehouse_id=self._config.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout=_STATEMENT_WAIT_TIMEOUT,
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
        msg = "All connection attempts failed"
        raise RuntimeError(msg) from last_err
