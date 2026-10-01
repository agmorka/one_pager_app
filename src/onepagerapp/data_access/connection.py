"""Databricks connection and SQL execution layer."""

import logging
import time
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    StatementParameterListItem,
    StatementResponse,
    StatementState,
)
import streamlit as st

from onepagerapp.config import AppConfig, AppMode

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5
_STATEMENT_WAIT_TIMEOUT = "30s"
_FAILED_STATES = (StatementState.FAILED, StatementState.CANCELED, StatementState.CLOSED)

SqlParameterValue = str | int | bool | datetime | date | None


# Header in which the Databricks Apps proxy forwards the signed-in user's
# OAuth token (user authorization, scope ``sql``).
USER_TOKEN_HEADER = "x-forwarded-access-token"  # noqa: S105 - a header name


class Identity(str, Enum):
    """Who a SQL statement runs as (Architecture.md §8, Decision_Log §19).

    - ``USER``: the signed-in user, so Unity Catalog grants of their groups
      apply. Used for reads.
    - ``APP``: the app's service principal. Used for writes and for reads that
      are part of a write.
    """

    USER = "user"
    APP = "app"


class StatementFailedError(RuntimeError):
    """The SQL warehouse accepted a statement but reported it as failed."""


class MissingUserTokenError(RuntimeError):
    """A statement should run as the user, but the user's token is missing.

    Deployed, the token comes from the Databricks Apps proxy; without it the
    statement is refused instead of running as the service principal.
    """


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
        elif value is None:
            items.append(StatementParameterListItem(name=name, type="STRING"))
        else:
            items.append(
                StatementParameterListItem(name=name, value=value, type="STRING")
            )
    return items


class DatabricksConnection:
    """Manages connection to Databricks SQL warehouse and executes statements.

    Every statement runs as an explicit ``Identity``:

    - ``APP``: the client built from the default auth. In Databricks Apps that
      is the app's service principal (``DATABRICKS_CLIENT_ID`` /
      ``DATABRICKS_CLIENT_SECRET``); one client is reused.
    - ``USER``: deployed, a client with the user's token from the
      ``x-forwarded-access-token`` header. A missing token raises
      ``MissingUserTokenError``; there is never a fallback to the service
      principal.

    In ``local-integration`` both identities use the Databricks CLI profile,
    i.e. the same person, so local runs cannot show permission differences
    between the user and the service principal.

    Also handles retry logic and SQL statement execution, separate from the
    queries themselves.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize connection with configuration.

        Args:
            config: Application configuration containing warehouse details.
        """
        self._config = config
        self._ws = WorkspaceClient()

    def _client(self, identity: Identity) -> WorkspaceClient:
        """Return the workspace client that runs statements as ``identity``.

        Raises:
            MissingUserTokenError: ``USER`` in deployed mode without the
                user's token.

        """
        if identity is Identity.APP or self._config.APP_MODE is not AppMode.DATABRICKS:
            return self._ws
        token = st.context.headers.get(USER_TOKEN_HEADER)
        if not token:
            msg = (
                "The user's access token is missing; the statement is not run. "
                "Check that user authorization (scope sql) is enabled for the app."
            )
            raise MissingUserTokenError(msg)
        # auth_type is required: the Databricks Apps runtime sets OAuth env vars,
        # which conflict with this explicit token unless we pin the auth method
        return WorkspaceClient(host=self._ws.config.host, token=token, auth_type="pat")

    def execute_statement(
        self,
        statement: str,
        parameters: Mapping[str, SqlParameterValue] | None = None,
        *,
        identity: Identity,
    ) -> StatementResponse:
        """Execute a SQL statement with retry logic.

        Args:
            statement: SQL statement to execute. User-supplied values must be
                referenced as named markers (``:name``) and passed in
                ``parameters`` — never formatted into the statement text.
            parameters: Values for the named markers in ``statement``.
            identity: Who the statement runs as. No default, so every call
                site chooses on purpose: reads as ``USER``, writes (and reads
                that are part of a write) as ``APP``.

        Returns:
            StatementResponse from Databricks.

        Raises:
            StatementFailedError: If the warehouse reports the statement as
                failed, canceled, or closed.
            MissingUserTokenError: ``USER`` in deployed mode without a token.
            RuntimeError: If all retry attempts fail.
        """
        bound = to_statement_parameters(parameters) if parameters else None
        client = self._client(identity)

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
