from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.connection import to_statement_parameters


@pytest.mark.unit
def test__to_statement_parameters__types() -> None:
    params = to_statement_parameters(
        {
            "s": "O'Brien; DROP TABLE x",
            "n": None,
            "b": False,
            "i": 7,
            "t": datetime(2026, 9, 29, 10, 0, tzinfo=UTC),
        }
    )
    by_name = {p.name: p for p in params}
    assert by_name["s"].value == "O'Brien; DROP TABLE x"
    assert by_name["s"].type is None
    assert by_name["n"].value is None
    assert (by_name["b"].value, by_name["b"].type) == ("false", "BOOLEAN")
    assert (by_name["i"].value, by_name["i"].type) == ("7", "BIGINT")
    assert by_name["t"].type == "TIMESTAMP"
    assert by_name["t"].value.startswith("2026-09-29T10:00:00")


@pytest.mark.unit
def test__to_statement_parameters__empty() -> None:
    assert to_statement_parameters(None) is None
    assert to_statement_parameters({}) is None
