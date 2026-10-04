"""Databricks connection and SQL execution layer."""

import logging
import re
import time
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

import streamlit as st
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import PermissionDenied, Unauthenticated
from databricks.sdk.service.sql import (
    StatementParameterListItem,
    StatementResponse,
    StatementState,
)

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


def service_client(config: AppConfig) -> WorkspaceClient:
    """Workspace client of the service principal that does all writes.

    With ``ONE_PAGER_APP_SP_CLIENT_ID`` / ``ONE_PAGER_APP_SP_CLIENT_SECRET``
    set, OAuth machine-to-machine as that service principal (e.g.
    ``bp-spn-lhx-opa-dev-001``). Otherwise the default authentication: in
    Databricks Apps the app's own service principal (``DATABRICKS_CLIENT_ID``
    / ``DATABRICKS_CLIENT_SECRET`` set by the runtime), locally the CLI
    profile.
    """
    credentials = config.service_principal_credentials
    if credentials is None:
        if config.ONE_PAGER_APP_SP_CLIENT_ID.strip():
            logger.warning(
                "ONE_PAGER_APP_SP_CLIENT_ID is set without a secret; "
                "using the app's own service principal"
            )
        return WorkspaceClient()
    client_id, secret = credentials
    logger.info("Writes run as the service principal %s", client_id)
    # auth_type is required: the Databricks Apps runtime sets the app's own
    # OAuth env vars, which would be picked up otherwise.
    return WorkspaceClient(
        client_id=client_id, client_secret=secret, auth_type="oauth-m2m"
    )


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


class AccessDeniedError(StatementFailedError):
    """Unity Catalog or the warehouse refused a statement (missing grant)."""


READ_ACCESS_DENIED_MESSAGE = (
    "Your role does not have access to {target}. Contact the platform team."
)
WRITE_ACCESS_DENIED_MESSAGE = (
    "The One Pager App could not save the change because of a configuration "
    "problem. Contact the platform team."
)


class ReadAccessDeniedError(AccessDeniedError):
    """A read as the user was refused: their groups lack a grant.

    The message is meant for the user (``READ_ACCESS_DENIED_MESSAGE``).
    """


class WriteAccessDeniedError(AccessDeniedError):
    """A write as the service principal was refused: a deployment error.

    The details are logged; the message is generic
    (``WRITE_ACCESS_DENIED_MESSAGE``).
    """


# Unity Catalog / SQL warehouse wording for a missing privilege, e.g.
# "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have
# SELECT on Table 'cat.sch.t'." or "PERMISSION_DENIED: ...".
_PERMISSION_ERROR = re.compile(
    r"INSUFFICIENT_PERMISSIONS|PERMISSION_DENIED|insufficient privileges"
    r"|does not have \w+(?: \w+)? (?:privilege )?on",
    re.IGNORECASE,
)
_DENIED_OBJECT = re.compile(
    r"\bon (table|view|schema|catalog|volume|function)\s+[`'\"]?([\w.`-]+)",
    re.IGNORECASE,
)


def is_permission_error(message: str) -> bool:
    """Whether a statement error message means a missing privilege."""
    return bool(_PERMISSION_ERROR.search(message))


def access_denied_error(identity: "Identity", detail: str) -> AccessDeniedError:
    """Return the error to raise for a permission failure of a statement.

    A read as the user gets a message for the user, naming the object when
    the warehouse reports it. A write (or any statement as the service
    principal) is a deployment error: logged with the details, generic for
    the user.
    """
    if identity is Identity.APP:
        logger.error(
            "The app's service principal is missing a privilege (deployment error): %s",
            detail,
        )
        return WriteAccessDeniedError(WRITE_ACCESS_DENIED_MESSAGE)
    match = _DENIED_OBJECT.search(detail)
    target = (
        f"{match.group(1).lower()} {match.group(2).replace('`', '')}"
        if match
        else "this data"
    )
    logger.warning("Read refused by Unity Catalog for the user: %s", detail)
    return ReadAccessDeniedError(READ_ACCESS_DENIED_MESSAGE.format(target=target))


SESSION_EXPIRED_MESSAGE = "Your session has expired. Please reload the page."


class SessionExpiredError(RuntimeError):
    """The user's token was refused (expired) on a statement run as the user.

    Streamlit keeps the token of the first connection for the whole session;
    after it expires, reads fail until the page is reloaded, which starts a
    new session with a fresh token. Writes run as the service principal and
    are not affected. The message is meant for the user.
    """

    def __init__(self) -> None:  # noqa: D107
        super().__init__(SESSION_EXPIRED_MESSAGE)


# Wording of a refused (expired or revoked) token in a failed statement.
_EXPIRED_TOKEN_ERROR = re.compile(
    r"token (?:is |has )?expired|expired token|invalid access token"
    r"|UNAUTHENTICATED|credential was not sent",
    re.IGNORECASE,
)


def is_expired_token_error(message: str) -> bool:
    """Whether an error message means the token was refused (expired)."""
    return bool(_EXPIRED_TOKEN_ERROR.search(message))


def user_error_message(error: BaseException, default: str) -> str:
    """Return the message for a failed read: a message for the user, else ``default``.

    Pages never show exception text. ``ReadAccessDeniedError`` and
    ``SessionExpiredError`` are the exceptions, because their messages are
    written for the user.
    """
    if isinstance(error, (ReadAccessDeniedError, SessionExpiredError)):
        return str(error)
    return default


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

    - ``APP``: the service principal of ``service_client``: the configured
      one (``ONE_PAGER_APP_SP_CLIENT_ID``), else the app's own; one client
      is reused.
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
        self._ws = service_client(config)

    def find_group_names(self, name: str) -> list[str]:
        """Real names of the workspace groups called ``name``, ignoring case.

        Read with the service principal (users' tokens have no group scope);
        the result only gives the spelling, membership is still checked as
        the user. A failed lookup is logged and gives [].
        """
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        try:
            found = self._ws.groups.list(
                filter=f'displayName eq "{escaped}"', attributes="displayName"
            )
            names = [g.display_name for g in found if g.display_name]
        except Exception:  # noqa: BLE001 - the configured spelling is still checked
            logger.warning("Looking up the group %s failed", name, exc_info=True)
            return []
        return [n for n in names if n.casefold() == name.casefold()]

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
            ReadAccessDeniedError: A read as the user lacks a grant (the
                message is meant for the user).
            WriteAccessDeniedError: The service principal lacks a grant
                (logged; generic message).
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
            except PermissionDenied as exc:
                # A missing grant (e.g. CAN_USE on the warehouse) is not retried.
                raise access_denied_error(identity, str(exc)) from exc
            except Unauthenticated as exc:
                # Not retried: the same token is refused again.
                raise _refused_token_error(identity, str(exc)) from exc
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
                try:
                    _raise_if_failed(response)
                except StatementFailedError as exc:
                    if is_permission_error(str(exc)):
                        raise access_denied_error(identity, str(exc)) from exc
                    if is_expired_token_error(str(exc)):
                        raise _refused_token_error(identity, str(exc)) from exc
                    raise
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


def _refused_token_error(identity: Identity, detail: str) -> Exception:
    """Return the error for a refused token: reload (user) or deployment error."""
    if identity is Identity.USER:
        logger.info("The user's token was refused (expired?): %s", detail)
        return SessionExpiredError()
    logger.error("The app's service principal could not authenticate: %s", detail)
    return RuntimeError("The app could not authenticate to the SQL warehouse.")
