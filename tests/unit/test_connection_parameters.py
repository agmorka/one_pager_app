from datetime import UTC, date, datetime

import pytest

from onepagerapp.data_access.connection import to_statement_parameters


@pytest.mark.unit
def test__to_statement_parameters__timestamps_and_dates() -> None:
    params = to_statement_parameters(
        {
            "t": datetime(2026, 9, 29, 10, 0, tzinfo=UTC),
            "d": date(2026, 9, 29),
        }
    )
    by_name = {p.name: (p.type, p.value) for p in params}
    assert by_name["t"] == ("TIMESTAMP", "2026-09-29T10:00:00+00:00")
    assert by_name["d"] == ("DATE", "2026-09-29")


@pytest.mark.unit
def test__to_statement_parameters__sql_text_is_bound_verbatim() -> None:
    [param] = to_statement_parameters({"s": "O'Brien'); DROP TABLE x; --"})
    assert (param.type, param.value) == ("STRING", "O'Brien'); DROP TABLE x; --")
