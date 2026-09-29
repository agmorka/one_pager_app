"""Unit tests for SQL parameter binding and failure detection in the connection."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.sql import StatementState

from onepagerapp.data_access.connection import (
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
