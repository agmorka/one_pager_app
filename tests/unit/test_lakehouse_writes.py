"""LakehouseAccess create-flow SQL, verified against a recording fake connection."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from onepagerapp.config import AppConfig, AppMode
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.models import AuthorizedUser, ChangeLogEntry, OnePagerStatusRow

NASTY = "x'); DROP TABLE one_pager_status; --"
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _response(columns: list[str], rows: list[list]) -> SimpleNamespace:
    return SimpleNamespace(
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns])
        ),
        result=SimpleNamespace(data_array=rows),
    )


class _FakeConnection:
    def __init__(
        self,
        responses: list[SimpleNamespace] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses = list(responses or [])
        self.error = error

    def execute_statement(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> SimpleNamespace:
        self.calls.append((statement, dict(parameters or {})))
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else _response([], [])


def _access(connection: _FakeConnection) -> LakehouseAccess:
    access = LakehouseAccess.__new__(LakehouseAccess)
    access._config = AppConfig(
        APP_MODE=AppMode.LOCAL_INTEGRATION,
        ONE_PAGER_APP_VOLUME_PATH="/Volumes/x",
        ONE_PAGER_APP_DATABRICKS_CATALOG="cat",
        ONE_PAGER_APP_DATABRICKS_SCHEMA="sch",
    )
    access._connection = connection
    access._document_store = None
    return access


def _assert_not_interpolated(statement: str) -> None:
    assert NASTY not in statement
    assert "DROP TABLE" not in statement


@pytest.mark.unit
def test__compare_and_set__uses_num_affected_rows() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).compare_and_set_sequence("OP", 6, 7) is True
    statement, params = conn.calls[0]
    assert "UPDATE cat.sch.id_sequences" in statement
    assert "last_value = :expected" in statement
    assert params == {"new": 7, "id_type": "OP", "expected": 6}

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert _access(conn).compare_and_set_sequence("OP", 6, 7) is False


@pytest.mark.unit
def test__compare_and_set__concurrent_conflict_is_retryable() -> None:
    conn = _FakeConnection(
        error=RuntimeError("SQL statement FAILED: ConcurrentAppendException")
    )
    assert _access(conn).compare_and_set_sequence("OP", 6, 7) is False


@pytest.mark.unit
def test__get_sequence_value__missing_row_raises() -> None:
    conn = _FakeConnection([_response(["last_value"], [])])
    with pytest.raises(RuntimeError, match="no row for OP"):
        _access(conn).get_sequence_value("OP")


@pytest.mark.unit
def test__insert_one_pager_status__binds_every_column() -> None:
    conn = _FakeConnection()
    row = OnePagerStatusRow(
        one_pager_id="OP-0007",
        data_product="customer_master",
        product_name=NASTY,
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.1.0",
        owner_name="A",
        owner_initials="AB",
        owner_email="a@b.dk",
        owner_team=None,
        created_by="AB",
        created_at=NOW,
        last_updated_at=NOW,
        last_updated_by="AB",
        structure_definition="structure_one_pager_v_1.json",
    )
    _access(conn).insert_one_pager_status(row)
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert "INSERT INTO cat.sch.one_pager_status" in statement
    assert "CAST(:reviewed_at AS TIMESTAMP)" in statement
    assert params["product_name"] == NASTY
    assert params["pending_pr"] is False
    assert params["reviewed_at"] is None
    assert len(params) == 20


@pytest.mark.unit
def test__insert_authorized_users__multi_row_parameters() -> None:
    conn = _FakeConnection()
    _access(conn).insert_authorized_users(
        [
            AuthorizedUser("OP-0007", "AB", NASTY, "a@b.dk", "owner"),
            AuthorizedUser("OP-0007", "CD", "C D", "c@d.dk", "sme", "Team"),
        ]
    )
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert ":role_1" in statement
    assert params["name_0"] == NASTY
    assert params["role_1"] == "sme"


@pytest.mark.unit
def test__insert_authorized_users__empty_is_noop() -> None:
    conn = _FakeConnection()
    _access(conn).insert_authorized_users([])
    assert conn.calls == []


@pytest.mark.unit
def test__append_change_log__omits_identity_column() -> None:
    conn = _FakeConnection()
    _access(conn).append_change_log(
        ChangeLogEntry(
            id=0,
            one_pager_id="OP-0007",
            version="0.1.0",
            event_type="creation",
            author_initials="AB",
            author_name="A B",
            summary=NASTY,
            created_at=NOW,
        )
    )
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert "(one_pager_id, version" in statement
    assert "(id," not in statement
    assert params["summary"] == NASTY
    assert params["from_status"] is None


@pytest.mark.unit
def test__delete_one_pager_records__status_row_first() -> None:
    conn = _FakeConnection()
    _access(conn).delete_one_pager_records("OP-0007")
    tables = [call[0].split("FROM ")[1].split(" ")[0] for call in conn.calls]
    assert tables == [
        "cat.sch.one_pager_status",
        "cat.sch.one_pager_authorized_users",
        "cat.sch.change_log",
    ]
    assert all(call[1] == {"one_pager_id": "OP-0007"} for call in conn.calls)


@pytest.mark.unit
def test__get_one_pager_status__parses_timestamps() -> None:
    columns = [
        "one_pager_id",
        "product_name",
        "owner_name",
        "owner_initials",
        "owner_email",
        "version",
        "one_pager_status",
        "data_product_status",
        "created_at",
        "last_updated_at",
        "last_updated_by",
    ]
    conn = _FakeConnection(
        [
            _response(
                columns,
                [
                    [
                        "OP-0007",
                        "P",
                        "A",
                        "AB",
                        "a@b.dk",
                        "0.1.0",
                        "Draft",
                        "In Definition",
                        "2026-09-29T10:00:00Z",
                        "2026-09-29T10:00:00.000Z",
                        "AB",
                    ]
                ],
            )
        ]
    )
    header = _access(conn).get_one_pager_status("OP-0007")
    assert header.created_at == NOW
    assert conn.calls[0][1] == {"one_pager_id": "OP-0007"}
    assert ":one_pager_id" in conn.calls[0][0]
