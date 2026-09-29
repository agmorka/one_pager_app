"""Unit tests for the Use Case SQL in LakehouseAccess, using a fake connection.

No Databricks connection is made: DatabricksConnection is replaced by a fake
that records every statement and its bound parameters and replays canned
Statement API responses.
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from onepagerapp.config import AppConfig
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.connection import StatementFailedError
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import UseCaseFilter, UseCaseInput

EVIL = "x'); DROP TABLE use_cases; --"


def _response(columns: list[str], rows: list[list]) -> SimpleNamespace:
    """Build an object shaped like a Statement API StatementResponse."""
    return SimpleNamespace(
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns])
        ),
        result=SimpleNamespace(data_array=rows),
    )


def _affected(count: int) -> SimpleNamespace:
    return _response(["num_affected_rows"], [[str(count)]])


def _use_case_row(use_case_id: str = "UC-001", deprecated: str = "false") -> list:
    # The Statement API returns every value as a string
    return [
        use_case_id,
        "Persona",
        "Goal",
        "Scenario",
        "Decision",
        "High",
        deprecated,
        "AB",
        "2026-09-01T10:00:00.000Z",
        "CD",
        "2026-09-02T11:30:00.000Z",
        "3",
    ]


_USE_CASE_ROW_COLUMNS = [
    "use_case_id",
    "persona",
    "goal",
    "scenario",
    "decision_enabled",
    "priority",
    "deprecated",
    "created_by",
    "created_at",
    "last_updated_by",
    "last_updated_at",
    "reference_count",
]


class FakeConnection:
    """Records statements; answers with a handler chosen by the test."""

    def __init__(self) -> None:  # noqa: D107
        self.calls: list[tuple[str, dict]] = []
        self.handler: Callable[[str, dict], object] = lambda _s, _p: _response([], [])

    def execute_statement(
        self, statement: str, parameters: dict | None = None
    ) -> object:
        params = dict(parameters or {})
        self.calls.append((statement, params))
        return self.handler(statement, params)


Fake = tuple[lakehouse.LakehouseAccess, FakeConnection]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Fake:
    connection = FakeConnection()
    monkeypatch.setattr(lakehouse, "DatabricksConnection", lambda _config: connection)
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=str(tmp_path))
    access = lakehouse.LakehouseAccess(config, OnePagerDocumentStore(tmp_path))
    return access, connection


def _input(**overrides: str) -> UseCaseInput:
    values = {
        "persona": EVIL,
        "goal": EVIL,
        "scenario": EVIL,
        "decision_enabled": EVIL,
        "priority": "High",
    }
    values.update(overrides)
    return UseCaseInput(**values)


def _assert_no_user_text_in_sql(connection: FakeConnection) -> None:
    for statement, _ in connection.calls:
        assert "DROP TABLE" not in statement
        assert "'x'" not in statement


@pytest.mark.unit
def test_get_use_cases_binds_filters_and_escapes_like(fake: Fake) -> None:
    access, connection = fake

    def handler(statement: str, _params: dict) -> SimpleNamespace:
        if "COUNT(*) AS total" in statement:
            return _response(["total"], [["21"]])
        return _response(_USE_CASE_ROW_COLUMNS, [_use_case_row()])

    connection.handler = handler
    result = access.get_use_cases(
        UseCaseFilter(search="50%_off\\" + EVIL, priority="Must Have"), 2, 20
    )

    assert result.total_rows == 21
    assert result.page == 2
    _assert_no_user_text_in_sql(connection)
    count_sql, count_params = connection.calls[0]
    page_sql, page_params = connection.calls[1]
    assert count_params == page_params
    escaped_evil = EVIL.replace("_", "\\_")
    assert page_params["search"] == "%50\\%\\_off\\\\" + escaped_evil + "%"
    assert page_params["priority"] == "Must Have"
    assert "uc.deprecated = false" in count_sql
    assert "uc.deprecated = false" in page_sql
    assert "LIMIT 20 OFFSET 20" in page_sql
    assert "dev_bia_meta.onepager_app.use_case_references" in page_sql


@pytest.mark.unit
def test_include_deprecated_drops_the_deprecated_clause(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda s, _p: (
        _response(["total"], [["0"]]) if "COUNT(*) AS total" in s else _response([], [])
    )
    access.get_use_cases(UseCaseFilter(include_deprecated=True), 1, 20)
    assert all("deprecated = false" not in sql for sql, _ in connection.calls)
    assert all(params == {} for _, params in connection.calls)


@pytest.mark.unit
def test_rows_are_parsed_from_statement_api_strings(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _response(
        _USE_CASE_ROW_COLUMNS, [_use_case_row(deprecated="false")]
    )
    use_case = access.get_use_case("UC-001")
    assert use_case.deprecated is False  # bool("false") would be True
    assert use_case.reference_count == 3
    assert use_case.created_at.year == 2026
    assert use_case.last_updated_at.hour == 11

    connection.handler = lambda _s, _p: _response(
        _USE_CASE_ROW_COLUMNS, [_use_case_row(deprecated="true")]
    )
    assert access.get_use_case("UC-001").deprecated is True


@pytest.mark.unit
def test_get_use_case_not_found_and_references(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _response(_USE_CASE_ROW_COLUMNS, [])
    assert access.get_use_case(EVIL) is None
    assert connection.calls[-1][1] == {"use_case_id": EVIL}

    connection.handler = lambda _s, _p: _response(
        ["one_pager_id"], [["OP-0001"], ["OP-0003"]]
    )
    assert access.get_use_case_references("UC-001") == ["OP-0001", "OP-0003"]
    _assert_no_user_text_in_sql(connection)


def _sequence_handler(outcomes: list) -> Callable[[str, dict], SimpleNamespace]:
    """Build a create_use_case handler: read counter → UPDATE outcome → INSERT."""
    state = {"value": 4, "outcomes": list(outcomes)}

    def handler(statement: str, params: dict) -> SimpleNamespace:
        if statement.startswith("SELECT last_value"):
            return _response(["last_value"], [[str(state["value"])]])
        if statement.startswith("UPDATE") and "id_sequences" in statement:
            outcome = state["outcomes"].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome == 1:
                state["value"] = params["new_value"]
            else:  # another writer won the race and moved the counter
                state["value"] += 1
            return _affected(outcome)
        if statement.startswith("INSERT"):
            return _affected(1)
        msg = f"unexpected statement: {statement}"
        raise AssertionError(msg)

    return handler


@pytest.mark.unit
def test_create_use_case_allocates_id_with_compare_and_swap(fake: Fake) -> None:
    access, connection = fake
    connection.handler = _sequence_handler([1])

    assert access.create_use_case(_input(), "MJO") == "UC-005"
    _assert_no_user_text_in_sql(connection)

    update_sql, update_params = connection.calls[1]
    assert "WHERE id_type = :id_type AND last_value = :current_value" in update_sql
    assert update_params == {"id_type": "UC", "new_value": 5, "current_value": 4}

    insert_sql, insert_params = connection.calls[2]
    assert insert_sql.startswith("INSERT INTO dev_bia_meta.onepager_app.use_cases")
    assert "false" in insert_sql
    assert insert_params["use_case_id"] == "UC-005"
    assert insert_params["persona"] == EVIL
    assert insert_params["user_initials"] == "MJO"


@pytest.mark.unit
def test_id_allocation_retries_after_losing_a_race_or_conflict(fake: Fake) -> None:
    access, connection = fake
    connection.handler = _sequence_handler(
        [0, StatementFailedError("SQL statement FAILED: ConcurrentAppendException"), 1]
    )
    # Lost race moves the counter 4 → 5, the conflict leaves it, then 5 → 6 wins
    assert access.create_use_case(_input(), "MJO") == "UC-006"


@pytest.mark.unit
def test_id_allocation_gives_up_after_max_attempts(fake: Fake) -> None:
    access, connection = fake
    connection.handler = _sequence_handler([0] * lakehouse._ID_ALLOCATION_ATTEMPTS)
    with pytest.raises(RuntimeError, match="Could not allocate"):
        access.create_use_case(_input(), "MJO")
    assert not any(sql.startswith("INSERT") for sql, _ in connection.calls)


@pytest.mark.unit
def test_id_allocation_fails_without_sequence_row(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _response(["last_value"], [])
    with pytest.raises(RuntimeError, match="no row for id_type 'UC'"):
        access.create_use_case(_input(), "MJO")


@pytest.mark.unit
def test_update_use_case_binds_all_values(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _affected(1)
    access.update_use_case("UC-001", _input(priority="Low"), "XY")

    sql, params = connection.calls[0]
    _assert_no_user_text_in_sql(connection)
    assert "last_updated_at = current_timestamp()" in sql
    assert params == {
        "persona": EVIL,
        "goal": EVIL,
        "scenario": EVIL,
        "decision_enabled": EVIL,
        "priority": "Low",
        "user_initials": "XY",
        "use_case_id": "UC-001",
    }


@pytest.mark.unit
@pytest.mark.parametrize("deprecated", [True, False])
def test_set_use_case_deprecated(fake: Fake, deprecated: bool) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _affected(1)
    access.set_use_case_deprecated("UC-001", deprecated, "XY")
    sql, params = connection.calls[0]
    assert "deprecated = :deprecated" in sql
    assert params == {
        "deprecated": deprecated,
        "user_initials": "XY",
        "use_case_id": "UC-001",
    }


@pytest.mark.unit
def test_updates_raise_not_found_when_no_row_matches(fake: Fake) -> None:
    access, connection = fake
    connection.handler = lambda _s, _p: _affected(0)
    with pytest.raises(NotFoundError):
        access.update_use_case("UC-404", _input(), "XY")
    with pytest.raises(NotFoundError):
        access.set_use_case_deprecated("UC-404", True, "XY")
