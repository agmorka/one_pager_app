"""Unit tests for SQL parameter binding and failure detection in the connection."""

import logging
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


@pytest.mark.unit
def test_to_statement_parameters_types() -> None:
    items = to_statement_parameters(
        {"text": "O'Brien", "number": 7, "flag": True, "off": False, "empty": None}
    )
    by_name = {item.name: (item.type, item.value) for item in items}
    assert by_name == {
        "text": ("STRING", "O'Brien"),
        "number": ("INT", "7"),
        "flag": ("BOOLEAN", "true"),
        "off": ("BOOLEAN", "false"),
        "empty": ("STRING", None),
    }


def _status(state: StatementState, message: str | None = None) -> SimpleNamespace:
    error = SimpleNamespace(message=message) if message else None
    return SimpleNamespace(status=SimpleNamespace(state=state, error=error))


@pytest.mark.unit
@pytest.mark.parametrize(
    "state",
    [StatementState.FAILED, StatementState.CANCELED, StatementState.CLOSED],
)
def test_failed_statements_raise(state: StatementState) -> None:
    with pytest.raises(StatementFailedError, match="boom"):
        _raise_if_failed(_status(state, "boom"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "state", [StatementState.SUCCEEDED, StatementState.PENDING, StatementState.RUNNING]
)
def test_other_states_pass(state: StatementState) -> None:
    _raise_if_failed(_status(state))
    _raise_if_failed(SimpleNamespace(status=None))


# ============================================================================
# Identities (identity plan Phase 4)
# ============================================================================


class _Client:
    """Stand-in for WorkspaceClient that records how it was built."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.config = SimpleNamespace(host="https://adb.example")


def _connection(
    monkeypatch: pytest.MonkeyPatch, mode: str, headers: dict[str, str]
) -> DatabricksConnection:
    monkeypatch.setattr(connection_module, "WorkspaceClient", _Client)
    monkeypatch.setattr(st, "context", SimpleNamespace(headers=headers))
    return DatabricksConnection(
        AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH="/Volumes/x")
    )


@pytest.mark.unit
def test__app_identity__reuses_the_default_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connection(monkeypatch, "databricks", {USER_TOKEN_HEADER: "user-token"})

    assert conn._client(Identity.APP) is conn._ws
    assert conn._client(Identity.APP) is conn._client(Identity.APP)
    assert conn._ws.kwargs == {}  # default auth: the service principal


@pytest.mark.unit
def test__user_identity__deployed_uses_the_forwarded_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connection(monkeypatch, "databricks", {USER_TOKEN_HEADER: "user-token"})

    client = conn._client(Identity.USER)

    assert client is not conn._ws
    assert client.kwargs == {
        "host": "https://adb.example",
        "token": "user-token",
        "auth_type": "pat",
    }


@pytest.mark.unit
def test__user_identity__deployed_without_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connection(monkeypatch, "databricks", {})

    with pytest.raises(MissingUserTokenError):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__user_identity__local_integration_uses_the_cli_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connection(monkeypatch, "local-integration", {})

    assert conn._client(Identity.USER) is conn._ws


# ============================================================================
# Permission errors (identity plan Phase 4, step 3)
# ============================================================================

UC_DENIED = (
    "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have "
    "SELECT on Table 'cat.sch.one_pager_status'."
)


class _StatementApi:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls = 0

    def execute_statement(self, **_: object) -> object:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _connection_answering(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> tuple[DatabricksConnection, _StatementApi]:
    conn = _connection(monkeypatch, "local-integration", {})
    api = _StatementApi(outcome)
    conn._ws.statement_execution = api
    return conn, api


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        UC_DENIED,
        "PERMISSION_DENIED: User does not have USE SCHEMA on Schema `cat`.`sch`",
        "User does not have MODIFY on Table cat.sch.locks",
    ],
)
def test__is_permission_error__recognises_uc_messages(message: str) -> None:
    assert is_permission_error(message)


@pytest.mark.unit
@pytest.mark.parametrize("message", ["Table or view not found", "Syntax error"])
def test__is_permission_error__ignores_other_failures(message: str) -> None:
    assert not is_permission_error(message)


@pytest.mark.unit
def test__read_denied__message_for_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, _ = _connection_answering(
        monkeypatch, _status(StatementState.FAILED, UC_DENIED)
    )

    with pytest.raises(ReadAccessDeniedError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)

    assert str(raised.value) == (
        "Your role does not have access to table cat.sch.one_pager_status. "
        "Contact the platform team."
    )
    assert user_error_message(raised.value, "default") == str(raised.value)


@pytest.mark.unit
def test__read_denied__object_quoted_with_backticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = "PERMISSION_DENIED: User does not have USE SCHEMA on Schema `cat`.`sch`"
    conn, _ = _connection_answering(monkeypatch, _status(StatementState.FAILED, detail))

    with pytest.raises(ReadAccessDeniedError, match="access to schema cat.sch\\."):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__write_denied__logged_and_generic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    conn, _ = _connection_answering(
        monkeypatch, _status(StatementState.FAILED, UC_DENIED)
    )

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
def test__sdk_permission_denied__mapped_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, api = _connection_answering(
        monkeypatch, PermissionDenied("User does not have CAN_USE on warehouse")
    )

    with pytest.raises(ReadAccessDeniedError, match="access to this data"):
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert api.calls == 1


@pytest.mark.unit
def test__other_failures_stay_statement_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _ = _connection_answering(
        monkeypatch, _status(StatementState.FAILED, "Table or view not found")
    )

    with pytest.raises(StatementFailedError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)
    assert not isinstance(raised.value, AccessDeniedError)
    assert user_error_message(raised.value, "default") == "default"


# ============================================================================
# Expired user token (identity plan Phase 7, step 3)
# ============================================================================


@pytest.mark.unit
def test__expired_user_token__asks_to_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, api = _connection_answering(monkeypatch, Unauthenticated("Token is expired"))

    with pytest.raises(SessionExpiredError) as raised:
        conn.execute_statement("SELECT 1", identity=Identity.USER)

    assert str(raised.value) == "Your session has expired. Please reload the page."
    assert user_error_message(raised.value, "default") == str(raised.value)
    assert api.calls == 1  # not retried


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    ["Invalid access token.", "UNAUTHENTICATED: token has expired"],
)
def test__expired_token_in_a_failed_statement(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    conn, _ = _connection_answering(
        monkeypatch, _status(StatementState.FAILED, message)
    )

    with pytest.raises(SessionExpiredError):
        conn.execute_statement("SELECT 1", identity=Identity.USER)


@pytest.mark.unit
def test__service_principal_auth_failure_is_not_a_session_expiry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    conn, _ = _connection_answering(monkeypatch, Unauthenticated("invalid client"))

    with (
        caplog.at_level(logging.ERROR, logger=connection_module.__name__),
        pytest.raises(RuntimeError) as raised,
    ):
        conn.execute_statement("UPDATE t SET a = 1", identity=Identity.APP)

    assert not isinstance(raised.value, SessionExpiredError)
    assert "could not authenticate" in caplog.text
