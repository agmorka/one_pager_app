"""Unit tests for SQL parameter binding and failure detection in the connection."""

from types import SimpleNamespace

import pytest
import streamlit as st
from databricks.sdk.service.sql import StatementState

from onepagerapp.config import AppConfig
from onepagerapp.data_access import connection as connection_module
from onepagerapp.data_access.connection import (
    USER_TOKEN_HEADER,
    DatabricksConnection,
    Identity,
    MissingUserTokenError,
    StatementFailedError,
    _raise_if_failed,
    to_statement_parameters,
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
