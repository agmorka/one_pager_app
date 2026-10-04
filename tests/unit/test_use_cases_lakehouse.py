"""Unit tests for the Use Case SQL in LakehouseAccess, using a fake connection.

No Databricks connection is made: DatabricksConnection is replaced by a fake
that records every statement and its bound parameters and replays canned
Statement API responses.
"""

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.connection import StatementFailedError
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.id_generator import MAX_ATTEMPTS
from onepagerapp.models import UseCaseFilter, UseCaseInput
from tests.helpers import affected_rows, statement_response

EVIL = "x'); DROP TABLE use_cases; --"
USE_CASE_COLUMNS = [
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


def _use_case_row(deprecated: str = "false") -> list:
    """Return a use_cases row as the Statement API sends it: all strings."""
    return [
        "UC-001",
        "Persona",
        "Goal",
        "Scenario",
        "Decision",
        "High",
        deprecated,
        "ABR",
        "2026-09-01T10:00:00.000Z",
        "CDA",
        "2026-09-02T11:30:00.000Z",
        "3",
    ]


def _input(**overrides: str) -> UseCaseInput:
    """Return Use Case input whose text fields are an injection probe."""
    values = {
        "persona": EVIL,
        "goal": EVIL,
        "scenario": EVIL,
        "decision_enabled": EVIL,
        "priority": "High",
    }
    return UseCaseInput(**{**values, **overrides})


def _answer(response: SimpleNamespace) -> Callable[[str, dict], SimpleNamespace]:
    """Return a handler that answers every statement with ``response``."""
    return lambda _statement, _params: response


def _assert_no_user_text_in_sql(connection: SimpleNamespace) -> None:
    """Assert that no statement contains the injection probe."""
    for statement, _ in connection.calls:
        assert "DROP TABLE" not in statement
        assert "'x'" not in statement


def _sequence_handler(outcomes: list) -> Callable[[str, dict], SimpleNamespace]:
    """Answer create_use_case: read counter → UPDATE outcome → INSERT.

    Each outcome is 1 (this writer wins), 0 (another writer moved the
    counter first) or an exception to raise.
    """
    state = {"value": 4, "outcomes": list(outcomes)}

    def handler(statement: str, params: dict) -> SimpleNamespace:
        if statement.startswith("SELECT last_value"):
            return statement_response(["last_value"], [[str(state["value"])]])
        if statement.startswith("UPDATE") and "id_sequences" in statement:
            outcome = state["outcomes"].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            state["value"] = params["new_value"] if outcome == 1 else state["value"] + 1
            return affected_rows(outcome)
        if statement.startswith("INSERT"):
            return affected_rows(1)
        msg = f"unexpected statement: {statement}"
        raise AssertionError(msg)

    return handler


@pytest.mark.unit
def test__search_and_priority_filter__get_use_cases__bound_with_escaped_like(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """Filters are bound; LIKE wildcards in the search are escaped."""

    # Given
    def handler(statement: str, _params: dict) -> SimpleNamespace:
        if "COUNT(*) AS total" in statement:
            return statement_response(["total"], [["21"]])
        return statement_response(USE_CASE_COLUMNS, [_use_case_row()])

    connection.handler = handler
    search = "50%_off\\" + EVIL

    # When
    result = patched_lakehouse.get_use_cases(
        UseCaseFilter(search=search, priority="Must Have"), 2, 20
    )

    # Then
    assert (result.total_rows, result.page) == (21, 2)
    _assert_no_user_text_in_sql(connection)
    (count_sql, count_params), (page_sql, page_params) = connection.calls
    assert count_params == page_params
    escaped_evil = EVIL.replace("_", "\\_")
    assert page_params["search"] == "%50\\%\\_off\\\\" + escaped_evil + "%"
    assert page_params["priority"] == "Must Have"
    assert "uc.deprecated = false" in count_sql
    assert "uc.deprecated = false" in page_sql
    assert "LIMIT 20 OFFSET 20" in page_sql
    assert "dev_bia_meta.onepager_app.use_case_references" in page_sql


@pytest.mark.unit
def test__include_deprecated__get_use_cases__no_deprecated_clause(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """Showing deprecated Use Cases drops the filter and binds nothing."""
    # Given
    connection.handler = lambda s, _p: (
        statement_response(["total"], [["0"]])
        if "COUNT(*) AS total" in s
        else statement_response([], [])
    )

    # When
    patched_lakehouse.get_use_cases(UseCaseFilter(include_deprecated=True), 1, 20)

    # Then
    assert all("deprecated = false" not in sql for sql, _ in connection.calls)
    assert all(params == {} for _, params in connection.calls)


@pytest.mark.unit
def test__string_values__get_use_case__parsed_into_types(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """Booleans, integers and timestamps are parsed from strings."""
    # Given
    connection.handler = _answer(
        statement_response(USE_CASE_COLUMNS, [_use_case_row("false")])
    )

    # When
    use_case = patched_lakehouse.get_use_case("UC-001")

    # Then
    assert use_case.deprecated is False  # bool("false") would be True
    assert use_case.reference_count == 3
    assert use_case.created_at.year == 2026
    assert use_case.last_updated_at.hour == 11


@pytest.mark.unit
def test__deprecated_true_string__get_use_case__deprecated(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """``"true"`` reads as True."""
    # Given
    connection.handler = _answer(
        statement_response(USE_CASE_COLUMNS, [_use_case_row("true")])
    )

    # When
    use_case = patched_lakehouse.get_use_case("UC-001")

    # Then
    assert use_case.deprecated is True


@pytest.mark.unit
def test__no_row__get_use_case__none_with_bound_id(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """An unknown ID reads as None; the ID is a bound parameter."""
    # Given
    connection.handler = _answer(statement_response(USE_CASE_COLUMNS, []))

    # When
    use_case = patched_lakehouse.get_use_case(EVIL)

    # Then
    assert use_case is None
    assert connection.calls[-1][1] == {"use_case_id": EVIL}
    _assert_no_user_text_in_sql(connection)


@pytest.mark.unit
def test__referencing_rows__get_use_case_references__one_pager_ids(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """The referencing One Pager IDs are returned in order."""
    # Given
    connection.handler = _answer(
        statement_response(["one_pager_id"], [["OP-0001"], ["OP-0003"]])
    )

    # When
    references = patched_lakehouse.get_use_case_references("UC-001")

    # Then
    assert references == ["OP-0001", "OP-0003"]


@pytest.mark.unit
def test__counter_at_4__create_use_case__compare_and_swap_then_insert(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """The next ID is claimed with a conditional UPDATE, then inserted."""
    # Given
    connection.handler = _sequence_handler([1])

    # When
    use_case_id = patched_lakehouse.create_use_case(_input(), "MJO")

    # Then
    assert use_case_id == "UC-005"
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
def test__lost_race_then_conflict__create_use_case__retries_to_next_free_id(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """A lost race moves the counter 4 → 5, a conflict leaves it, 5 → 6 wins."""
    # Given
    conflict = StatementFailedError("SQL statement FAILED: ConcurrentAppendException")
    connection.handler = _sequence_handler([0, conflict, 1])

    # When
    use_case_id = patched_lakehouse.create_use_case(_input(), "MJO")

    # Then
    assert use_case_id == "UC-006"


@pytest.mark.unit
def test__every_attempt_lost__create_use_case__gives_up_without_insert(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """After MAX_ATTEMPTS lost races no row is inserted."""
    # Given
    connection.handler = _sequence_handler([0] * MAX_ATTEMPTS)

    # When / Then
    with pytest.raises(RuntimeError, match="Could not allocate"):
        patched_lakehouse.create_use_case(_input(), "MJO")
    assert not any(sql.startswith("INSERT") for sql, _ in connection.calls)


@pytest.mark.unit
def test__no_sequence_row__create_use_case__raises(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """A missing counter row is a configuration error."""
    # Given
    connection.handler = _answer(statement_response(["last_value"], []))

    # When / Then
    with pytest.raises(RuntimeError, match="no row for id_type 'UC'"):
        patched_lakehouse.create_use_case(_input(), "MJO")


@pytest.mark.unit
def test__input__update_use_case__binds_all_values(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace
) -> None:
    """Every value of the update is bound."""
    # Given
    connection.handler = _answer(affected_rows(1))

    # When
    patched_lakehouse.update_use_case("UC-001", _input(priority="Low"), "XY")

    # Then
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
def test__flag__set_use_case_deprecated__bound_flag_and_actor(
    patched_lakehouse: LakehouseAccess, connection: SimpleNamespace, deprecated: bool
) -> None:
    """Deprecating and restoring bind the flag and the actor."""
    # Given
    connection.handler = _answer(affected_rows(1))

    # When
    patched_lakehouse.set_use_case_deprecated("UC-001", deprecated, "XY")

    # Then
    sql, params = connection.calls[0]
    assert "deprecated = :deprecated" in sql
    assert params == {
        "deprecated": deprecated,
        "user_initials": "XY",
        "use_case_id": "UC-001",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "write",
    [
        lambda access: access.update_use_case("UC-404", _input(), "XY"),
        lambda access: access.set_use_case_deprecated("UC-404", True, "XY"),
    ],
    ids=["update", "deprecate"],
)
def test__no_row_matches__use_case_write__raises_not_found(
    patched_lakehouse: LakehouseAccess,
    connection: SimpleNamespace,
    write: Callable[[LakehouseAccess], object],
) -> None:
    """Writing an unknown Use Case raises NotFoundError."""
    # Given
    connection.handler = _answer(affected_rows(0))

    # When / Then
    with pytest.raises(NotFoundError):
        write(patched_lakehouse)
