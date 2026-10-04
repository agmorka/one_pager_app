"""Unit tests for SQL parameter binding and failure detection in the connection."""

import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
import streamlit as st
from databricks.sdk.errors import PermissionDenied, Unauthenticated
from databricks.sdk.service.sql import StatementState

from onepagerapp.config import AppConfig
from onepagerapp.data_access import connection as connection_module
from onepagerapp.data_access.connection import (
    USER_TOKEN_HEADER,
    WRITE_ACCESS_DENIED_MESSAGE,
    AccessDeniedError,
    DatabricksConnection,
    Identity,
    MissingUserTokenError,
    ReadAccessDeniedError,
    SessionExpiredError,
    StatementFailedError,
    WriteAccessDeniedError,
    _raise_if_failed,
    is_permission_error,
    to_statement_parameters,
    user_error_message,
)
from tests.helpers import failing

UC_DENIED = (
    "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have "
    "SELECT on Table 'cat.sch.one_pager_status'."
)
SCHEMA_DENIED = "PERMISSION_DENIED: User does not have USE SCHEMA on Schema `cat`.`sch`"
DIRECTORY_SPELLING = "BEC_BECOC001_LHX_dev_DataPlatEng"


def _status(state: StatementState, message: str | None = None) -> SimpleNamespace:
    """Return a statement response in ``state`` with an optional error message."""
    error = SimpleNamespace(message=message) if message else None
    return SimpleNamespace(status=SimpleNamespace(state=state, error=error))


def _client(**kwargs: object) -> SimpleNamespace:
    """Stand in for ``WorkspaceClient``, keeping the arguments it was built with."""
    return SimpleNamespace(
        kwargs=kwargs, config=SimpleNamespace(host="https://adb.example")
    )


def _connection(
    monkeypatch: pytest.MonkeyPatch, mode: str, headers: dict[str, str]
) -> DatabricksConnection:
    """Return a connection in ``mode`` whose request carries ``headers``."""
    monkeypatch.setattr(connection_module, "WorkspaceClient", _client)
    monkeypatch.setattr(st, "context", SimpleNamespace(headers=headers))
    return DatabricksConnection(
        AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH="/Volumes/x")
    )


def _answering(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> tuple[DatabricksConnection, list[dict]]:
    """Return a local connection whose Statement API returns or raises ``outcome``.

    The list collects the arguments of every Statement API call.
    """
    conn = _connection(monkeypatch, "local-integration", {})
    calls: list[dict] = []

    def execute_statement(**kwargs: object) -> object:
        calls.append(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    conn._ws.statement_execution = SimpleNamespace(execute_statement=execute_statement)
    return conn, calls


def _groups_connection(list_groups: object) -> DatabricksConnection:
    """Return a connection whose SCIM groups API is ``list_groups``."""
    conn = object.__new__(DatabricksConnection)
    conn._config = AppConfig(ONE_PAGER_APP_VOLUME_PATH="/Volumes/x")
    conn._ws = SimpleNamespace(groups=SimpleNamespace(list=list_groups))
    return conn


# ============================================================================
# Parameters and statement states
# ============================================================================


@pytest.mark.unit
def test__python_values__to_statement_parameters__typed_strings() -> None:
    """Each Python type maps to a SQL type and a string value."""
    # When
    items = to_statement_parameters(
        {"text": "O'Brien", "number": 7, "flag": True, "off": False, "empty": None}
    )

    # Then
    assert {item.name: (item.type, item.value) for item in items} == {
        "text": ("STRING", "O'Brien"),
        "number": ("INT", "7"),
        "flag": ("BOOLEAN", "true"),
        "off": ("BOOLEAN", "false"),
        "empty": ("STRING", None),
    }


@pytest.mark.unit
def test__timestamp_and_date__to_statement_parameters__iso_strings() -> None:
    """Timestamps and dates are sent in ISO format."""
    # When
    params = to_statement_parameters(
        {"t": datetime(2026, 9, 29, 10, 0, tzinfo=UTC), "d": date(2026, 9, 29)}
    )

    # Then
    by_name = {p.name: (p.type, p.value) for p in params}
    assert by_name["t"] == ("TIMESTAMP", "2026-09-29T10:00:00+00:00")
    assert by_name["d"] == ("DATE", "2026-09-29")


@pytest.mark.unit
def test__sql_text__to_statement_parameters__bound_verbatim() -> None:
    """SQL in a value is passed on unchanged, as data."""
    # When
    [param] = to_statement_parameters({"s": "O'Brien'); DROP TABLE x; --"})

    # Then
    assert (param.type, param.value) == ("STRING", "O'Brien'); DROP TABLE x; --")


@pytest.mark.unit
@pytest.mark.parametrize(
    "state",
    [StatementState.FAILED, StatementState.CANCELED, StatementState.CLOSED],
)
def test__failed_state__raise_if_failed__raises_with_message(
    state: StatementState,
) -> None:
    """A failed, cancelled or closed statement raises with the server message."""
    # When / Then
    with pytest.raises(StatementFailedError, match="boom"):
        _raise_if_failed(_status(state, "boom"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "response",
    [
        _status(StatementState.SUCCEEDED),
        _status(StatementState.PENDING),
        _status(StatementState.RUNNING),
        SimpleNamespace(status=None),
    ],
    ids=["succeeded", "pending", "running", "no-status"],
)
def test__other_state__raise_if_failed__passes(response: SimpleNamespace) -> None:
    """Other states and a missing status are not failures."""
    # When / Then
    _raise_if_failed(response)


# ============================================================================
# Identities (identity plan Phase 4)
# ============================================================================


@pytest.mark.unit
def test__deployed__client_for_app__reuses_default_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app identity uses the default (service principal) client."""
    # Given
    conn = _connection(monkeypatch, "databricks", {USER_TOKEN_HEADER: "user-token"})

    # When
    client = conn._client(Identity.APP)

    # Then
    assert client is conn._ws
    assert conn._client(Identity.APP) is client
    assert conn._ws.kwargs == {}


@pytest.mark.unit
def test__deployed_with_token__client_for_user__built_from_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user identity uses the forwarded user token."""
    # Given
    conn = _connection(monkeypatch, "databricks", {USER_TOKEN_HEADER: "user-token"})

    # When
    client = conn._client(Identity.USER)

    # Then
    assert client is not conn._ws
    assert client.kwargs == {
        "host": "https://adb.example",
        "token": "user-token",
        "auth_type": "pat",
    }


@pytest.mark.unit
def test__deployed_without_token__execute_as_user__raises_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a forwarded token there is no user identity."""
    # Given
    conn = _connection(monkeypatch, "databricks", {})

    # When / Then
    with pytest.raises(MissingUserTokenError):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__local_integration__client_for_user__cli_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locally the CLI profile is the user."""
    # Given
    conn = _connection(monkeypatch, "local-integration", {})

    # When
    client = conn._client(Identity.USER)

    # Then
    assert client is conn._ws


# ============================================================================
# Permission errors (identity plan Phase 4, step 3)
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [UC_DENIED, SCHEMA_DENIED, "User does not have MODIFY on Table cat.sch.locks"],
)
def test__unity_catalog_denial__is_permission_error__true(message: str) -> None:
    """Unity Catalog permission messages are recognised."""
    # When / Then
    assert is_permission_error(message)


@pytest.mark.unit
@pytest.mark.parametrize("message", ["Table or view not found", "Syntax error"])
def test__other_failure__is_permission_error__false(message: str) -> None:
    """Other failures are not permission errors."""
    # When / Then
    assert not is_permission_error(message)


@pytest.mark.unit
def test__read_refused_on_table__execute_as_user__names_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused read tells the user which table they cannot access."""
    # Given
    conn, _ = _answering(monkeypatch, _status(StatementState.FAILED, UC_DENIED))

    # When / Then
    with pytest.raises(ReadAccessDeniedError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert str(raised.value) == (
        "Your role does not have access to table cat.sch.one_pager_status. "
        "Contact the platform team."
    )
    assert user_error_message(raised.value, "default") == str(raised.value)


@pytest.mark.unit
def test__read_refused_on_quoted_schema__execute_as_user__names_the_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backtick-quoted object names are shown without quotes."""
    # Given
    conn, _ = _answering(monkeypatch, _status(StatementState.FAILED, SCHEMA_DENIED))

    # When / Then
    with pytest.raises(ReadAccessDeniedError, match="access to schema cat.sch\\."):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__write_refused__execute_as_app__generic_message_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused write is a deployment error: logged, generic for the user."""
    # Given
    conn, _ = _answering(monkeypatch, _status(StatementState.FAILED, UC_DENIED))

    # When / Then
    with (
        caplog.at_level(logging.ERROR, logger=connection_module.__name__),
        pytest.raises(WriteAccessDeniedError) as raised,
    ):
        conn.execute_statement("UPDATE t SET a = 1", identity=Identity.APP)
    assert str(raised.value) == WRITE_ACCESS_DENIED_MESSAGE
    assert "cat.sch.one_pager_status" not in str(raised.value)
    assert any("deployment error" in m and UC_DENIED in m for m in caplog.messages)
    assert user_error_message(raised.value, "default") == "default"


@pytest.mark.unit
def test__sdk_permission_denied__execute_as_user__mapped_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SDK PermissionDenied is a read denial and is not retried."""
    # Given
    conn, calls = _answering(
        monkeypatch, PermissionDenied("User does not have CAN_USE on warehouse")
    )

    # When / Then
    with pytest.raises(ReadAccessDeniedError, match="access to this data"):
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert len(calls) == 1


@pytest.mark.unit
def test__table_not_found__execute_as_user__stays_statement_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other failures are not access errors and show the default message."""
    # Given
    conn, _ = _answering(
        monkeypatch, _status(StatementState.FAILED, "Table or view not found")
    )

    # When / Then
    with pytest.raises(StatementFailedError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert not isinstance(raised.value, AccessDeniedError)
    assert user_error_message(raised.value, "default") == "default"


# ============================================================================
# Expired user token (identity plan Phase 7, step 3)
# ============================================================================


@pytest.mark.unit
def test__expired_user_token__execute_as_user__asks_to_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired token asks the user to reload and is not retried."""
    # Given
    conn, calls = _answering(monkeypatch, Unauthenticated("Token is expired"))

    # When / Then
    with pytest.raises(SessionExpiredError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert str(raised.value) == "Your session has expired. Please reload the page."
    assert user_error_message(raised.value, "default") == str(raised.value)
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "message", ["Invalid access token.", "UNAUTHENTICATED: token has expired"]
)
def test__token_failure_in_failed_statement__execute_as_user__session_expired(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    """Token messages in a failed statement also mean an expired session."""
    # Given
    conn, _ = _answering(monkeypatch, _status(StatementState.FAILED, message))

    # When / Then
    with pytest.raises(SessionExpiredError):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__service_principal_auth_failure__execute_as_app__not_session_expiry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The app failing to authenticate is logged, not blamed on the session."""
    # Given
    conn, _ = _answering(monkeypatch, Unauthenticated("invalid client"))

    # When / Then
    with (
        caplog.at_level(logging.ERROR, logger=connection_module.__name__),
        pytest.raises(RuntimeError) as raised,
    ):
        conn.execute_statement("UPDATE t SET a = 1", identity=Identity.APP)
    assert not isinstance(raised.value, SessionExpiredError)
    assert "could not authenticate" in caplog.text


# ============================================================================
# Group names
# ============================================================================


@pytest.mark.unit
def test__group_in_other_case__find_group_names__real_spelling() -> None:
    """The directory's spelling is found ignoring case; others are dropped."""
    # Given
    conn = _groups_connection(
        lambda **_: [
            SimpleNamespace(display_name=DIRECTORY_SPELLING),
            SimpleNamespace(display_name="Other"),
        ]
    )

    # When
    names = conn.find_group_names("BEC_BECOC001_LHX_DEV_DataPlatEng")

    # Then
    assert names == [DIRECTORY_SPELLING]


@pytest.mark.unit
def test__name_with_quote__find_group_names__escaped_in_filter_no_match() -> None:
    """A quote is escaped in the SCIM filter and stays part of the name."""
    # Given
    asked: list[str] = []

    def list_groups(*, filter: str, attributes: str) -> list[SimpleNamespace]:  # noqa: A002
        asked.append(filter)
        return [SimpleNamespace(display_name=DIRECTORY_SPELLING)]

    conn = _groups_connection(list_groups)

    # When
    names = conn.find_group_names('BEC_BECOC001_LHX_DEV_DataPlatEng"')

    # Then
    assert names == []
    assert asked == ['displayName eq "BEC_BECOC001_LHX_DEV_DataPlatEng\\""']


@pytest.mark.unit
def test__groups_api_fails__find_group_names__no_names() -> None:
    """A directory failure gives no extra spellings."""
    # Given
    conn = _groups_connection(failing("forbidden"))

    # When
    names = conn.find_group_names("G")

    # Then
    assert names == []
