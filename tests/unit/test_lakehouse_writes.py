"""LakehouseAccess create-flow SQL, verified against a recording fake connection."""

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from onepagerapp.config import AppConfig, AppMode
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import Identity, StatementFailedError
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    LockInfo,
    OnePagerStatusRow,
    ReviewComment,
    UseCaseInput,
)

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
        group_names: dict[str, list[str]] | None = None,
    ) -> None:
        self.group_names = group_names or {}
        self.calls: list[tuple[str, dict]] = []
        self.identities: list[Identity] = []
        self.responses = list(responses or [])
        self.error = error

    def execute_statement(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
        *,
        identity: Identity,
    ) -> SimpleNamespace:
        self.calls.append((statement, dict(parameters or {})))
        self.identities.append(identity)
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else _response([], [])

    def find_group_names(self, name: str) -> list[str]:
        return self.group_names.get(name, [])


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
    assert "last_value = :current_value" in statement
    assert params == {"id_type": "OP", "new_value": 7, "current_value": 6}

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert _access(conn).compare_and_set_sequence("OP", 6, 7) is False


@pytest.mark.unit
def test__compare_and_set__concurrent_conflict_is_retryable() -> None:
    conn = _FakeConnection(
        error=StatementFailedError("SQL statement FAILED: ConcurrentAppendException")
    )
    assert _access(conn).compare_and_set_sequence("OP", 6, 7) is False


@pytest.mark.unit
def test__get_sequence_value__missing_row_raises() -> None:
    conn = _FakeConnection([_response(["last_value"], [])])
    with pytest.raises(RuntimeError, match="no row for id_type 'OP'"):
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
        owner_initials="ABR",
        owner_email="a@b.dk",
        owner_team=None,
        created_by="ABR",
        created_at=NOW,
        last_updated_at=NOW,
        last_updated_by="ABR",
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
            AuthorizedUser("OP-0007", "ABR", NASTY, "a@b.dk", "owner"),
            AuthorizedUser("OP-0007", "CDA", "C D", "c@d.dk", "sme", "Team"),
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
            author_initials="ABR",
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
                        "ABR",
                        "a@b.dk",
                        "0.1.0",
                        "Draft",
                        "In Definition",
                        "2026-09-29T10:00:00Z",
                        "2026-09-29T10:00:00.000Z",
                        "ABR",
                    ]
                ],
            )
        ]
    )
    header = _access(conn).get_one_pager_status("OP-0007")
    assert header.created_at == NOW
    assert conn.calls[0][1] == {"one_pager_id": "OP-0007"}
    assert ":one_pager_id" in conn.calls[0][0]


def _lock(holder: str = NASTY) -> LockInfo:
    return LockInfo(
        one_pager_id="OP-0001",
        locked_by_initials=holder,
        locked_by_name=holder,
        session_id=holder,
        acquired_at=NOW,
        last_heartbeat=NOW,
        expires_at=NOW,
    )


@pytest.mark.unit
def test__write_lock__single_guarded_merge_with_bound_parameters() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).write_lock(_lock(), now=NOW) is True

    statement, params = conn.calls[0]
    assert len(conn.calls) == 1
    assert statement.startswith("MERGE INTO cat.sch.locks")
    assert "WHEN MATCHED AND (t.expires_at <= :now" in statement
    assert "t.session_id = :session_id" in statement
    assert "WHEN NOT MATCHED THEN INSERT" in statement
    _assert_not_interpolated(statement)
    assert params["locked_by_initials"] == NASTY
    assert params["now"] == NOW

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert _access(conn).write_lock(_lock("ABR"), now=NOW) is False


@pytest.mark.unit
def test__write_lock__concurrent_conflict_is_not_acquired() -> None:
    conn = _FakeConnection(
        error=StatementFailedError("SQL statement FAILED: ConcurrentAppendException")
    )
    assert _access(conn).write_lock(_lock("ABR"), now=NOW) is False


@pytest.mark.unit
def test__refresh_lock__updates_only_the_holders_session() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).refresh_lock(
        "OP-0001",
        locked_by_initials=NASTY,
        session_id=NASTY,
        last_heartbeat=NOW,
        expires_at=NOW,
    )
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.locks SET last_heartbeat")
    assert "locked_by_initials = :locked_by_initials" in statement
    assert "session_id = :session_id" in statement
    _assert_not_interpolated(statement)
    assert params["session_id"] == NASTY

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert not _access(conn).refresh_lock(
        "OP-0001",
        locked_by_initials="ABR",
        session_id="s1",
        last_heartbeat=NOW,
        expires_at=NOW,
    )


@pytest.mark.unit
def test__delete_lock__only_the_holders_row() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).delete_lock("OP-0001", locked_by_initials=NASTY)
    statement, params = conn.calls[0]
    assert statement.startswith("DELETE FROM cat.sch.locks WHERE one_pager_id")
    assert "locked_by_initials = :locked_by_initials" in statement
    _assert_not_interpolated(statement)
    assert params == {"one_pager_id": "OP-0001", "locked_by_initials": NASTY}


@pytest.mark.unit
def test__get_locks__one_parameterized_in_query() -> None:
    columns = [
        "one_pager_id",
        "locked_by_initials",
        "locked_by_name",
        "session_id",
        "acquired_at",
        "last_heartbeat",
        "expires_at",
    ]
    ts = "2026-09-29T10:00:00.000Z"
    conn = _FakeConnection(
        [_response(columns, [["OP-0001", "ABR", "Alice", "s1", ts, ts, ts]])]
    )

    locks = _access(conn).get_locks(["OP-0001", NASTY])

    statement, params = conn.calls[0]
    assert "FROM cat.sch.locks WHERE one_pager_id IN (:id_0, :id_1)" in statement
    _assert_not_interpolated(statement)
    assert params == {"id_0": "OP-0001", "id_1": NASTY}
    assert [lock.locked_by_initials for lock in locks] == ["ABR"]
    assert locks[0].expires_at == NOW


@pytest.mark.unit
def test__get_locks__empty_page_runs_no_query() -> None:
    conn = _FakeConnection()
    assert _access(conn).get_locks([]) == []
    assert conn.calls == []


_STATUS_ROW_VALUES = {
    "one_pager_id": "OP-0007",
    "data_product": "p",
    "product_name": "P",
    "business_domain": "Customer",
    "data_product_type": "Foundational",
    "one_pager_status": "Draft",
    "data_product_status": "In Definition",
    "version": "0.2.0",
    "owner_name": "A",
    "owner_initials": "ABR",
    "owner_email": "a@b.dk",
    "owner_team": None,
    "created_by": "ABR",
    "created_at": "2026-09-29T10:00:00Z",
    "last_updated_at": "2026-09-29T10:00:00Z",
    "last_updated_by": "ABR",
    "reviewed_at": None,
    "reviewed_by": None,
    "structure_definition": "structure_one_pager_v_2.json",
    "pending_pr": "false",
}


@pytest.mark.unit
def test__get_one_pager_status_row__parses_full_row() -> None:
    conn = _FakeConnection(
        [_response(list(_STATUS_ROW_VALUES), [list(_STATUS_ROW_VALUES.values())])]
    )
    row = _access(conn).get_one_pager_status_row(NASTY)

    assert row.version == "0.2.0"
    assert row.created_at == NOW
    assert row.reviewed_at is None
    assert row.pending_pr is False
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert params == {"one_pager_id": NASTY}

    assert _access(_FakeConnection()).get_one_pager_status_row("OP-1") is None


@pytest.mark.unit
def test__update_one_pager_status__conditional_update_with_bound_parameters() -> None:
    conn = _FakeConnection(
        [_response(list(_STATUS_ROW_VALUES), [list(_STATUS_ROW_VALUES.values())])]
    )
    row = _access(conn).get_one_pager_status_row("OP-0007")
    row.product_name = NASTY

    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).update_one_pager_status(
        row, expected_version="0.1.0", expected_status="Draft"
    )
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.one_pager_status SET ")
    assert "version = :expected_version" in statement
    assert "one_pager_status = :expected_status" in statement
    assert "reviewed_at = CAST(:reviewed_at AS TIMESTAMP)" in statement
    for immutable in ("data_product =", "created_by =", "created_at ="):
        assert immutable not in statement
    _assert_not_interpolated(statement)
    assert params["product_name"] == NASTY
    assert params["expected_version"] == "0.1.0"

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert not _access(conn).update_one_pager_status(
        row, expected_version="0.1.0", expected_status="Draft"
    )


@pytest.mark.unit
def test__authorized_users_update_and_delete__bound_parameters() -> None:
    conn = _FakeConnection()
    access = _access(conn)
    access.update_authorized_users(
        [AuthorizedUser("OP-0001", NASTY, NASTY, "a@b.dk", "sme")]
    )
    access.delete_authorized_users("OP-0001", ["ABR", NASTY])
    access.delete_authorized_users("OP-0001", [])

    assert len(conn.calls) == 2
    update, params = conn.calls[0]
    assert update.startswith("UPDATE cat.sch.one_pager_authorized_users SET")
    assert params["user_initials"] == NASTY
    delete, params = conn.calls[1]
    assert "user_initials IN (:initials_0, :initials_1)" in delete
    assert params == {
        "initials_0": "ABR",
        "initials_1": NASTY,
        "one_pager_id": "OP-0001",
    }
    for statement, _ in conn.calls:
        _assert_not_interpolated(statement)


@pytest.mark.unit
def test__use_case_reference_writes__bound_parameters() -> None:
    conn = _FakeConnection([_response(["use_case_id"], [["UC-001"], ["UC-002"]])])
    access = _access(conn)
    assert access.get_linked_use_case_ids(NASTY) == ["UC-001", "UC-002"]
    access.add_use_case_reference(NASTY, "UC-003")
    access.remove_use_case_reference(NASTY, "UC-001")

    merge, params = conn.calls[1]
    assert merge.startswith("MERGE INTO cat.sch.use_case_references t")
    assert "WHEN NOT MATCHED THEN INSERT" in merge
    assert params == {"one_pager_id": NASTY, "use_case_id": "UC-003"}
    delete, _ = conn.calls[2]
    assert delete.startswith("DELETE FROM cat.sch.use_case_references")
    for statement, _ in conn.calls:
        _assert_not_interpolated(statement)


@pytest.mark.unit
def test__append_change_log_entries__single_insert() -> None:
    conn = _FakeConnection()
    entries = [
        ChangeLogEntry(
            0,
            "OP-1",
            "0.1.0",
            "status_transition",
            "ABR",
            "A",
            NASTY,
            NOW,
            "Draft",
            "Ready for Review",
            "one_pager_status",
        ),
        ChangeLogEntry(
            0,
            "OP-1",
            "0.1.0",
            "status_transition",
            "ABR",
            "A",
            "s",
            NOW,
            "Ready for Review",
            "In Review",
            "one_pager_status",
        ),
    ]
    _access(conn).append_change_log_entries(entries)
    _access(conn).append_change_log_entries([])

    [(statement, params)] = conn.calls
    assert statement.startswith("INSERT INTO cat.sch.change_log")
    assert ":summary_0" in statement
    assert ":summary_1" in statement
    assert params["summary_0"] == NASTY
    _assert_not_interpolated(statement)


@pytest.mark.unit
def test__get_one_pager_status_rows__filters_by_status_oldest_first() -> None:
    conn = _FakeConnection(
        [_response(list(_STATUS_ROW_VALUES), [list(_STATUS_ROW_VALUES.values())])]
    )
    rows = _access(conn).get_one_pager_status_rows(NASTY)

    assert [r.one_pager_id for r in rows] == ["OP-0007"]
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert "WHERE one_pager_status = :one_pager_status" in statement
    assert "ORDER BY last_updated_at ASC" in statement
    assert params == {"one_pager_status": NASTY}


@pytest.mark.unit
def test__review_comment_writes__bound_parameters() -> None:
    from onepagerapp.models import ReviewComment  # noqa: PLC0415

    comment = ReviewComment(
        id=0,
        one_pager_id="OP-0002",
        version="0.3.0",
        section=None,
        reviewer_initials="CJO",
        reviewer_name="Cjo",
        comment=NASTY,
        resolved=False,
        created_at=NOW,
    )
    conn = _FakeConnection()
    access = _access(conn)
    access.add_review_comment(comment)
    access.delete_review_comment(comment)

    insert, params = conn.calls[0]
    _assert_not_interpolated(insert)
    assert "INSERT INTO cat.sch.review_comments" in insert
    assert insert.split("(", 1)[1].startswith("one_pager_id,")
    assert "CAST(:resolved_at AS TIMESTAMP)" in insert
    assert params["comment"] == NASTY
    assert params["resolved"] is False
    delete, params = conn.calls[1]
    assert "DELETE FROM cat.sch.review_comments" in delete
    assert params == {
        "one_pager_id": "OP-0002",
        "reviewer_initials": "CJO",
        "created_at": NOW,
    }


@pytest.mark.unit
def test__get_review_comments__parses_string_booleans() -> None:
    columns = [
        "id", "one_pager_id", "version", "section", "reviewer_initials",
        "reviewer_name", "comment", "resolved", "resolved_by", "created_at",
        "resolved_at",
    ]
    rows = [
        ["1", "OP-1", "0.1.0", None, "CJ", "C", "x", "false", None,
         "2026-09-29T10:00:00Z", None],
        ["2", "OP-1", "0.1.0", "dataSources", "CJ", "C", "y", "true", "ABR",
         "2026-09-29T10:00:00Z", "2026-09-29T10:00:00Z"],
    ]
    conn = _FakeConnection([_response(columns, rows)])

    comments = _access(conn).get_review_comments("OP-1")

    assert [c.resolved for c in comments] == [False, True]
    assert comments[1].resolved_at == NOW


@pytest.mark.unit
def test__resolve_review_comment__conditional_update() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    resolved = _access(conn).resolve_review_comment(
        NASTY, 7, resolved_by="BSM", resolved_at=NOW
    )

    assert resolved is True
    statement, params = conn.calls[0]
    _assert_not_interpolated(statement)
    assert "UPDATE cat.sch.review_comments SET resolved = true" in statement
    assert "AND resolved = false" in statement
    assert params == {
        "id": 7,
        "one_pager_id": NASTY,
        "resolved_by": "BSM",
        "resolved_at": NOW,
    }

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert (
        _access(conn).resolve_review_comment(
            "OP-1", 7, resolved_by="BSM", resolved_at=NOW
        )
        is False
    )


# ============================================================================
# Actor on every write (identity plan Phase 3, Data_Model.md §5)
# ============================================================================
# Writes run as the service principal, so Delta history cannot show who made
# a change. Every write statement must therefore bind the acting user's
# initials, unless the table is documented as covered another way.

ACTOR = "Q9Z"
_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "MERGE")


class _ActorConnection(_FakeConnection):
    """Answers every statement: sequence reads with 4, writes with 1 row."""

    def execute_statement(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
        *,
        identity: Identity,
    ) -> SimpleNamespace:
        self.calls.append((statement, dict(parameters or {})))
        self.identities.append(identity)
        if statement.startswith("SELECT last_value"):
            return _response(["last_value"], [["4"]])
        return _response(["num_affected_rows"], [["1"]])


def _actor_status_row() -> OnePagerStatusRow:
    return OnePagerStatusRow(
        one_pager_id="OP-0007",
        data_product="p",
        product_name="P",
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.2.0",
        owner_name="A",
        owner_initials="ABR",
        owner_email="a@b.dk",
        owner_team=None,
        created_by="ABR",
        created_at=NOW,
        last_updated_at=NOW,
        last_updated_by=ACTOR,
        structure_definition="structure_one_pager_v_2.json",
    )


def _actor_entry() -> ChangeLogEntry:
    return ChangeLogEntry(
        id=0,
        one_pager_id="OP-0007",
        version="0.2.0",
        event_type="edit",
        author_initials=ACTOR,
        author_name="Q",
        summary="s",
        created_at=NOW,
    )


def _actor_comment() -> ReviewComment:
    return ReviewComment(
        id=0,
        one_pager_id="OP-0002",
        version="0.3.0",
        section=None,
        reviewer_initials=ACTOR,
        reviewer_name="Q",
        comment="c",
        resolved=False,
        created_at=NOW,
    )


_USE_CASE = UseCaseInput("p", "g", "s", "d", "High")

# Write method -> call that makes it write on behalf of ACTOR.
ACTOR_WRITES: dict[str, Callable[[LakehouseAccess], object]] = {
    "insert_reference_value": lambda a: a.insert_reference_value(
        "ref_business_domains", "HR", sort_order=1, active=True, user_initials=ACTOR
    ),
    "update_reference_value": lambda a: a.update_reference_value(
        "ref_business_domains", "HR", sort_order=1, active=True, user_initials=ACTOR
    ),
    "update_status_definition": lambda a: a.update_status_definition(
        "ref_op_status",
        "Draft",
        display_label="Draft",
        sort_order=1,
        badge_color="#808080",
        user_initials=ACTOR,
    ),
    "write_lock": lambda a: a.write_lock(_lock(ACTOR), now=NOW),
    "refresh_lock": lambda a: a.refresh_lock(
        "OP-0001",
        locked_by_initials=ACTOR,
        session_id="s1",
        last_heartbeat=NOW,
        expires_at=NOW,
    ),
    "delete_lock": lambda a: a.delete_lock("OP-0001", locked_by_initials=ACTOR),
    "append_change_log": lambda a: a.append_change_log(_actor_entry()),
    "append_change_log_entries": lambda a: a.append_change_log_entries(
        [_actor_entry(), _actor_entry()]
    ),
    "insert_one_pager_status": lambda a: a.insert_one_pager_status(
        _actor_status_row()
    ),
    "update_one_pager_status": lambda a: a.update_one_pager_status(
        _actor_status_row(), expected_version="0.1.0", expected_status="Draft"
    ),
    "add_review_comment": lambda a: a.add_review_comment(_actor_comment()),
    "resolve_review_comment": lambda a: a.resolve_review_comment(
        "OP-0002", 1, resolved_by=ACTOR, resolved_at=NOW
    ),
    "create_use_case": lambda a: a.create_use_case(_USE_CASE, ACTOR),
    "update_use_case": lambda a: a.update_use_case("UC-001", _USE_CASE, ACTOR),
    "set_use_case_deprecated": lambda a: a.set_use_case_deprecated(
        "UC-001", deprecated=True, user_initials=ACTOR
    ),
}

# Writes without an actor column, and why (Data_Model.md §5).
WRITES_WITHOUT_ACTOR = {
    # Changed only by create and save; the change_log entry of that operation
    # records the author.
    "insert_authorized_users": "covered by change_log",
    "update_authorized_users": "covered by change_log",
    "delete_authorized_users": "covered by change_log",
    "add_use_case_reference": "covered by change_log",
    "remove_use_case_reference": "covered by change_log",
    # No row is left; the Admin service logs a security event.
    "delete_reference_value": "security event",
    # Compensation removing the app's own partial writes (logged as failure).
    "delete_one_pager_records": "compensation",
    "delete_review_comment": "compensation",
    # System counter.
    "compare_and_set_sequence": "system",
}


def _write_statements(conn: _FakeConnection) -> list[tuple[str, dict]]:
    """Write statements, without the id_sequences counter (a system table)."""
    return [
        (statement, params)
        for statement, params in conn.calls
        if statement.lstrip().upper().startswith(_WRITE_KEYWORDS)
        and ".id_sequences" not in statement
    ]


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(ACTOR_WRITES))
def test__write__binds_the_actor(method: str) -> None:
    conn = _ActorConnection()

    ACTOR_WRITES[method](_access(conn))

    writes = _write_statements(conn)
    assert writes, f"{method} sent no write statement"
    # Writes, and the ID-sequence read that is part of a write, run as the
    # service principal (identity plan Phase 4).
    assert set(conn.identities) == {Identity.APP}, f"{method} ran as the user"
    for statement, params in writes:
        assert ACTOR in params.values(), f"{method}: actor not bound in {statement}"
        assert ACTOR not in statement, f"{method}: actor interpolated into SQL"


@pytest.mark.unit
def test__every_write_method_is_covered() -> None:
    """A new write method must bind the actor or be listed with a reason."""
    write_methods = {
        name
        for name in DataAccess.__abstractmethods__
        if not name.startswith(("get_", "read_"))
    }

    assert write_methods == set(ACTOR_WRITES) | set(WRITES_WITHOUT_ACTOR)
    assert not set(ACTOR_WRITES) & set(WRITES_WITHOUT_ACTOR)



# ============================================================================
# Group membership (identity plan Phase 6)
# ============================================================================


@pytest.mark.unit
def test__get_group_memberships__one_statement_with_bound_group_names() -> None:
    conn = _FakeConnection(
        [_response(["member_0", "member_1", "member_2"], [["true", "false", "true"]])]
    )
    groups = {"owner_sme": NASTY, "approver": "OPA-Approver", "admin": "OPA-Admin"}

    result = _access(conn).get_group_memberships(groups)

    assert result == {"owner_sme": True, "approver": False, "admin": True}
    [(statement, params)] = conn.calls
    _assert_not_interpolated(statement)
    assert "is_member(:group_0) OR is_account_group_member(:group_0)" in statement
    assert params == {
        "group_0": NASTY,
        "group_1": "OPA-Approver",
        "group_2": "OPA-Admin",
    }
    assert conn.identities == [Identity.USER]


@pytest.mark.unit
def test__get_group_memberships__checks_the_real_spelling_too() -> None:
    conn = _FakeConnection(
        [_response(["member_0", "member_1"], [["false", "true"]])],
        group_names={
            "BEC_BECOC001_LHX_DEV_DataPlatEng": ["BEC_BECOC001_LHX_dev_DataPlatEng"]
        },
    )
    group = "BEC_BECOC001_LHX_DEV_DataPlatEng"

    result = _access(conn).get_group_memberships(
        {"owner_sme": group, "approver": group, "admin": group}
    )

    assert result == {"owner_sme": True, "approver": True, "admin": True}
    [(_, params)] = conn.calls  # each distinct spelling once
    assert params == {
        "group_0": "BEC_BECOC001_LHX_DEV_DataPlatEng",
        "group_1": "BEC_BECOC001_LHX_dev_DataPlatEng",
    }


@pytest.mark.unit
def test__get_group_memberships__no_row_raises() -> None:
    with pytest.raises(RuntimeError, match="no row"):
        _access(_FakeConnection()).get_group_memberships({"admin": "G"})


@pytest.mark.unit
def test__get_group_memberships__nothing_to_check_runs_no_query() -> None:
    conn = _FakeConnection()
    assert _access(conn).get_group_memberships({}) == {}
    assert conn.calls == []
