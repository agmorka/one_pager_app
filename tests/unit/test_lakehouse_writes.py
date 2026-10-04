"""LakehouseAccess SQL, verified against a recording fake connection."""

import pytest

from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import Identity, StatementFailedError
from onepagerapp.models import AuthorizedUser
from tests.helpers import (
    ACTOR,
    LAKEHOUSE_ACTOR_WRITES,
    LAKEHOUSE_OTHER_WRITES,
    NASTY,
    NOW,
    affected_rows,
    answer_every_statement,
    assert_not_interpolated,
    change_log_entry,
    fake_connection,
    is_write,
    lakehouse_access,
    make_lock,
    review_comment,
    statement_response,
    status_row,
)

CONFLICT = StatementFailedError("SQL statement FAILED: ConcurrentAppendException")

# A status row as the Statement API returns it: every value a string.
STATUS_ROW_VALUES = {
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
STATUS_ROW_RESPONSE = statement_response(
    list(STATUS_ROW_VALUES), [list(STATUS_ROW_VALUES.values())]
)


# ============================================================================
# ID sequences
# ============================================================================


@pytest.mark.unit
def test__one_row_affected__compare_and_set_sequence__true_with_bound_values() -> None:
    """The counter moves with a conditional UPDATE on the current value."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    moved = lakehouse_access(conn).compare_and_set_sequence("OP", 6, 7)

    # Then
    assert moved is True
    statement, params = conn.calls[0]
    assert "UPDATE cat.sch.id_sequences" in statement
    assert "last_value = :current_value" in statement
    assert params == {"id_type": "OP", "new_value": 7, "current_value": 6}


@pytest.mark.unit
def test__no_row_affected__compare_and_set_sequence__false() -> None:
    """Another writer moved the counter first."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    moved = lakehouse_access(conn).compare_and_set_sequence("OP", 6, 7)

    # Then
    assert moved is False


@pytest.mark.unit
def test__concurrent_append_conflict__compare_and_set_sequence__false() -> None:
    """A Delta write conflict is retryable, not an error."""
    # Given
    conn = fake_connection(error=CONFLICT)

    # When
    moved = lakehouse_access(conn).compare_and_set_sequence("OP", 6, 7)

    # Then
    assert moved is False


@pytest.mark.unit
def test__no_sequence_row__get_sequence_value__raises() -> None:
    """A missing counter row is a configuration error."""
    # Given
    conn = fake_connection([statement_response(["last_value"], [])])

    # When / Then
    with pytest.raises(RuntimeError, match="no row for id_type 'OP'"):
        lakehouse_access(conn).get_sequence_value("OP")


# ============================================================================
# Create flow
# ============================================================================


@pytest.mark.unit
def test__status_row__insert_one_pager_status__binds_every_column() -> None:
    """Every column is a bound parameter."""
    # Given
    conn = fake_connection()
    row = status_row(
        product_name=NASTY,
        version="0.1.0",
        structure_definition="structure_one_pager_v_1.json",
    )

    # When
    lakehouse_access(conn).insert_one_pager_status(row)

    # Then
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert "INSERT INTO cat.sch.one_pager_status" in statement
    assert "CAST(:reviewed_at AS TIMESTAMP)" in statement
    assert params["product_name"] == NASTY
    assert (params["pending_pr"], params["reviewed_at"]) == (False, None)
    assert len(params) == 20


@pytest.mark.unit
def test__two_users__insert_authorized_users__one_multi_row_insert() -> None:
    """Each row gets numbered parameters."""
    # Given
    conn = fake_connection()
    users = [
        AuthorizedUser("OP-0007", "ABR", NASTY, "a@b.dk", "owner"),
        AuthorizedUser("OP-0007", "CDA", "C D", "c@d.dk", "sme", "Team"),
    ]

    # When
    lakehouse_access(conn).insert_authorized_users(users)

    # Then
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert ":role_1" in statement
    assert (params["name_0"], params["role_1"]) == (NASTY, "sme")


@pytest.mark.unit
def test__no_users__insert_authorized_users__runs_no_statement() -> None:
    """An empty list is a no-op."""
    # Given
    conn = fake_connection()

    # When
    lakehouse_access(conn).insert_authorized_users([])

    # Then
    assert conn.calls == []


@pytest.mark.unit
def test__entry__append_change_log__omits_identity_column() -> None:
    """The id column is generated by Delta and not inserted."""
    # Given
    conn = fake_connection()

    # When
    lakehouse_access(conn).append_change_log(change_log_entry(summary=NASTY))

    # Then
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert "(one_pager_id, version" in statement
    assert "(id," not in statement
    assert (params["summary"], params["from_status"]) == (NASTY, None)


@pytest.mark.unit
def test__one_pager__delete_one_pager_records__status_row_first() -> None:
    """Compensation removes the status row before the dependent rows."""
    # Given
    conn = fake_connection()

    # When
    lakehouse_access(conn).delete_one_pager_records("OP-0007")

    # Then
    tables = [call[0].split("FROM ")[1].split(" ")[0] for call in conn.calls]
    assert tables == [
        "cat.sch.one_pager_status",
        "cat.sch.one_pager_authorized_users",
        "cat.sch.change_log",
    ]
    assert all(call[1] == {"one_pager_id": "OP-0007"} for call in conn.calls)


@pytest.mark.unit
def test__iso_timestamps__get_one_pager_status__parsed_as_utc() -> None:
    """Timestamps with and without milliseconds are parsed."""
    # Given
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
    values = [
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
    conn = fake_connection([statement_response(columns, [values])])

    # When
    header = lakehouse_access(conn).get_one_pager_status("OP-0007")

    # Then
    assert header.created_at == NOW
    assert ":one_pager_id" in conn.calls[0][0]
    assert conn.calls[0][1] == {"one_pager_id": "OP-0007"}


# ============================================================================
# Locks
# ============================================================================


@pytest.mark.unit
def test__lock__write_lock__single_guarded_merge_with_bound_parameters() -> None:
    """The lock is written by one MERGE that only replaces an expired/own lock."""
    # Given
    conn = fake_connection([affected_rows(1)])
    lock = make_lock(holder=NASTY, session_id=NASTY)

    # When
    written = lakehouse_access(conn).write_lock(lock, now=NOW)

    # Then
    assert written is True
    [(statement, params)] = conn.calls
    assert statement.startswith("MERGE INTO cat.sch.locks")
    assert "WHEN MATCHED AND (t.expires_at <= :now" in statement
    assert "t.session_id = :session_id" in statement
    assert "WHEN NOT MATCHED THEN INSERT" in statement
    assert_not_interpolated(statement)
    assert (params["locked_by_initials"], params["now"]) == (NASTY, NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    "conn_kwargs",
    [{"responses": [affected_rows(0)]}, {"error": CONFLICT}],
    ids=["no-row-affected", "write-conflict"],
)
def test__lock_held_or_conflict__write_lock__not_acquired(conn_kwargs: dict) -> None:
    """Losing to another writer reports the lock as not written."""
    # Given
    conn = fake_connection(**conn_kwargs)

    # When
    written = lakehouse_access(conn).write_lock(make_lock(), now=NOW)

    # Then
    assert written is False


@pytest.mark.unit
def test__holders_session__refresh_lock__conditional_update() -> None:
    """Only the holder's session row is refreshed."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    refreshed = lakehouse_access(conn).refresh_lock(
        "OP-0001",
        locked_by_initials=NASTY,
        session_id=NASTY,
        last_heartbeat=NOW,
        expires_at=NOW,
    )

    # Then
    assert refreshed
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.locks SET last_heartbeat")
    assert "locked_by_initials = :locked_by_initials" in statement
    assert "session_id = :session_id" in statement
    assert_not_interpolated(statement)
    assert params["session_id"] == NASTY


@pytest.mark.unit
def test__no_row_affected__refresh_lock__false() -> None:
    """A lock that is no longer held is not refreshed."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    refreshed = lakehouse_access(conn).refresh_lock(
        "OP-0001",
        locked_by_initials="ABR",
        session_id="s1",
        last_heartbeat=NOW,
        expires_at=NOW,
    )

    # Then
    assert not refreshed


@pytest.mark.unit
def test__holder__delete_lock__deletes_only_the_holders_row() -> None:
    """The DELETE is conditional on the holder."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    deleted = lakehouse_access(conn).delete_lock("OP-0001", locked_by_initials=NASTY)

    # Then
    assert deleted
    statement, params = conn.calls[0]
    assert statement.startswith("DELETE FROM cat.sch.locks WHERE one_pager_id")
    assert "locked_by_initials = :locked_by_initials" in statement
    assert_not_interpolated(statement)
    assert params == {"one_pager_id": "OP-0001", "locked_by_initials": NASTY}


@pytest.mark.unit
def test__two_ids__get_locks__one_parameterized_in_query() -> None:
    """The locks of a page are read in one query with an IN list."""
    # Given
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
    row = ["OP-0001", "ABR", "Alice", "s1", ts, ts, ts]
    conn = fake_connection([statement_response(columns, [row])])

    # When
    locks = lakehouse_access(conn).get_locks(["OP-0001", NASTY])

    # Then
    statement, params = conn.calls[0]
    assert "FROM cat.sch.locks WHERE one_pager_id IN (:id_0, :id_1)" in statement
    assert_not_interpolated(statement)
    assert params == {"id_0": "OP-0001", "id_1": NASTY}
    assert [lock.locked_by_initials for lock in locks] == ["ABR"]
    assert locks[0].expires_at == NOW


@pytest.mark.unit
def test__no_ids__get_locks__runs_no_query() -> None:
    """An empty page needs no lock query."""
    # Given
    conn = fake_connection()

    # When
    locks = lakehouse_access(conn).get_locks([])

    # Then
    assert locks == []
    assert conn.calls == []


# ============================================================================
# Status rows and editing
# ============================================================================


@pytest.mark.unit
def test__stored_row__get_one_pager_status_row__parses_every_column() -> None:
    """String values are parsed into types; the ID is bound."""
    # Given
    conn = fake_connection([STATUS_ROW_RESPONSE])

    # When
    row = lakehouse_access(conn).get_one_pager_status_row(NASTY)

    # Then
    assert (row.version, row.created_at) == ("0.2.0", NOW)
    assert (row.reviewed_at, row.pending_pr) == (None, False)
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert params == {"one_pager_id": NASTY}


@pytest.mark.unit
def test__no_row__get_one_pager_status_row__none() -> None:
    """An unknown ID reads as None."""
    # Given
    conn = fake_connection()

    # When
    row = lakehouse_access(conn).get_one_pager_status_row("OP-1")

    # Then
    assert row is None


@pytest.mark.unit
def test__row__update_one_pager_status__conditional_update_of_mutable_columns() -> None:
    """The UPDATE checks version and status and never touches immutable columns."""
    # Given
    conn = fake_connection([affected_rows(1)])
    row = status_row(product_name=NASTY)

    # When
    updated = lakehouse_access(conn).update_one_pager_status(
        row, expected_version="0.1.0", expected_status="Draft"
    )

    # Then
    assert updated
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.one_pager_status SET ")
    assert "version = :expected_version" in statement
    assert "one_pager_status = :expected_status" in statement
    assert "reviewed_at = CAST(:reviewed_at AS TIMESTAMP)" in statement
    for immutable in ("data_product =", "created_by =", "created_at ="):
        assert immutable not in statement
    assert_not_interpolated(statement)
    assert (params["product_name"], params["expected_version"]) == (NASTY, "0.1.0")


@pytest.mark.unit
def test__row_changed_meanwhile__update_one_pager_status__false() -> None:
    """No row matched the expected version and status."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    updated = lakehouse_access(conn).update_one_pager_status(
        status_row(), expected_version="0.1.0", expected_status="Draft"
    )

    # Then
    assert not updated


@pytest.mark.unit
def test__changed_users__update_authorized_users__bound_parameters() -> None:
    """User rows are updated with bound values."""
    # Given
    conn = fake_connection()
    user = AuthorizedUser("OP-0001", NASTY, NASTY, "a@b.dk", "sme")

    # When
    lakehouse_access(conn).update_authorized_users([user])

    # Then
    [(statement, params)] = conn.calls
    assert statement.startswith("UPDATE cat.sch.one_pager_authorized_users SET")
    assert_not_interpolated(statement)
    assert params["user_initials"] == NASTY


@pytest.mark.unit
def test__removed_users__delete_authorized_users__one_in_list_delete() -> None:
    """Removed users are deleted in one statement; none means no statement."""
    # Given
    conn = fake_connection()
    access = lakehouse_access(conn)

    # When
    access.delete_authorized_users("OP-0001", ["ABR", NASTY])
    access.delete_authorized_users("OP-0001", [])

    # Then
    [(statement, params)] = conn.calls
    assert "user_initials IN (:initials_0, :initials_1)" in statement
    assert_not_interpolated(statement)
    assert params == {
        "initials_0": "ABR",
        "initials_1": NASTY,
        "one_pager_id": "OP-0001",
    }


@pytest.mark.unit
def test__references__read_add_remove_use_case_links__bound_parameters() -> None:
    """Use Case links are read, merged and deleted with bound values."""
    # Given
    conn = fake_connection(
        [statement_response(["use_case_id"], [["UC-001"], ["UC-002"]])]
    )
    access = lakehouse_access(conn)

    # When
    linked = access.get_linked_use_case_ids(NASTY)
    access.add_use_case_reference(NASTY, "UC-003")
    access.remove_use_case_reference(NASTY, "UC-001")

    # Then
    assert linked == ["UC-001", "UC-002"]
    merge, params = conn.calls[1]
    assert merge.startswith("MERGE INTO cat.sch.use_case_references t")
    assert "WHEN NOT MATCHED THEN INSERT" in merge
    assert params == {"one_pager_id": NASTY, "use_case_id": "UC-003"}
    assert conn.calls[2][0].startswith("DELETE FROM cat.sch.use_case_references")
    for statement, _ in conn.calls:
        assert_not_interpolated(statement)


@pytest.mark.unit
def test__two_entries__append_change_log_entries__single_insert() -> None:
    """Several entries go in one INSERT; none means no statement."""
    # Given
    conn = fake_connection()
    entries = [
        change_log_entry(event_type="status_transition", summary=NASTY),
        change_log_entry(event_type="status_transition"),
    ]

    # When
    lakehouse_access(conn).append_change_log_entries(entries)
    lakehouse_access(conn).append_change_log_entries([])

    # Then
    [(statement, params)] = conn.calls
    assert statement.startswith("INSERT INTO cat.sch.change_log")
    assert ":summary_0" in statement
    assert ":summary_1" in statement
    assert params["summary_0"] == NASTY
    assert_not_interpolated(statement)


@pytest.mark.unit
def test__status__get_one_pager_status_rows__filtered_oldest_first() -> None:
    """The review queue reads one status, oldest change first."""
    # Given
    conn = fake_connection([STATUS_ROW_RESPONSE])

    # When
    rows = lakehouse_access(conn).get_one_pager_status_rows(NASTY)

    # Then
    assert [r.one_pager_id for r in rows] == ["OP-0007"]
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert "WHERE one_pager_status = :one_pager_status" in statement
    assert "ORDER BY last_updated_at ASC" in statement
    assert params == {"one_pager_status": NASTY}


# ============================================================================
# Review comments
# ============================================================================


@pytest.mark.unit
def test__comment__add_review_comment__insert_without_identity_column() -> None:
    """A comment is inserted with bound values; Delta generates the id."""
    # Given
    conn = fake_connection()

    # When
    lakehouse_access(conn).add_review_comment(review_comment(comment=NASTY))

    # Then
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert "INSERT INTO cat.sch.review_comments" in statement
    assert statement.split("(", 1)[1].startswith("one_pager_id,")
    assert "CAST(:resolved_at AS TIMESTAMP)" in statement
    assert (params["comment"], params["resolved"]) == (NASTY, False)


@pytest.mark.unit
def test__comment__delete_review_comment__by_one_pager_reviewer_and_time() -> None:
    """Compensation finds the comment by its natural key."""
    # Given
    conn = fake_connection()

    # When
    lakehouse_access(conn).delete_review_comment(review_comment())

    # Then
    statement, params = conn.calls[0]
    assert "DELETE FROM cat.sch.review_comments" in statement
    assert params == {
        "one_pager_id": "OP-0002",
        "reviewer_initials": "CJO",
        "created_at": NOW,
    }


@pytest.mark.unit
def test__string_booleans__get_review_comments__parsed() -> None:
    """``"false"`` reads as False (``bool("false")`` would be True)."""
    # Given
    columns = [
        "id", "one_pager_id", "version", "section", "reviewer_initials",
        "reviewer_name", "comment", "resolved", "resolved_by", "created_at",
        "resolved_at",
    ]  # fmt: skip
    rows = [
        ["1", "OP-1", "0.1.0", None, "CJ", "C", "x", "false", None,
         "2026-09-29T10:00:00Z", None],
        ["2", "OP-1", "0.1.0", "dataSources", "CJ", "C", "y", "true", "ABR",
         "2026-09-29T10:00:00Z", "2026-09-29T10:00:00Z"],
    ]  # fmt: skip
    conn = fake_connection([statement_response(columns, rows)])

    # When
    comments = lakehouse_access(conn).get_review_comments("OP-1")

    # Then
    assert [c.resolved for c in comments] == [False, True]
    assert comments[1].resolved_at == NOW


@pytest.mark.unit
def test__open_comment__resolve_review_comment__conditional_update() -> None:
    """Only an unresolved comment of that One Pager is resolved."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    resolved = lakehouse_access(conn).resolve_review_comment(
        NASTY, 7, resolved_by="BSM", resolved_at=NOW
    )

    # Then
    assert resolved is True
    statement, params = conn.calls[0]
    assert_not_interpolated(statement)
    assert "UPDATE cat.sch.review_comments SET resolved = true" in statement
    assert "AND resolved = false" in statement
    assert params == {
        "id": 7,
        "one_pager_id": NASTY,
        "resolved_by": "BSM",
        "resolved_at": NOW,
    }


@pytest.mark.unit
def test__resolved_comment__resolve_review_comment__false() -> None:
    """Resolving twice changes nothing."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    resolved = lakehouse_access(conn).resolve_review_comment(
        "OP-1", 7, resolved_by="BSM", resolved_at=NOW
    )

    # Then
    assert resolved is False


# ============================================================================
# Actor on every write (identity plan Phase 3, Data_Model.md §5)
# ============================================================================
# Writes run as the service principal, so Delta history cannot show who made
# a change. Every write statement must therefore bind the acting user's
# initials, unless the table is documented as covered another way.


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(LAKEHOUSE_ACTOR_WRITES))
def test__write_on_behalf_of_user__call__actor_bound_as_parameter(method: str) -> None:
    """Every write binds the actor and runs as the service principal."""
    # Given
    conn = fake_connection(handler=answer_every_statement)

    # When
    LAKEHOUSE_ACTOR_WRITES[method](lakehouse_access(conn))

    # Then
    writes = [
        (statement, params)
        for statement, params in conn.calls
        if is_write(statement) and ".id_sequences" not in statement
    ]
    assert writes, f"{method} sent no write statement"
    # The ID-sequence read that is part of a write runs as the app as well.
    assert set(conn.identities) == {Identity.APP}, f"{method} ran as the user"
    for statement, params in writes:
        assert ACTOR in params.values(), f"{method}: actor not bound in {statement}"
        assert ACTOR not in statement, f"{method}: actor interpolated into SQL"


@pytest.mark.unit
def test__data_access_interface__write_methods__each_binds_actor_or_has_reason() -> (
    None
):
    """A new write method must bind the actor or be listed with a reason."""
    # When
    write_methods = {
        name
        for name in DataAccess.__abstractmethods__
        if not name.startswith(("get_", "read_"))
    }

    # Then
    assert write_methods == set(LAKEHOUSE_ACTOR_WRITES) | set(LAKEHOUSE_OTHER_WRITES)
    assert not set(LAKEHOUSE_ACTOR_WRITES) & set(LAKEHOUSE_OTHER_WRITES)


# ============================================================================
# Group membership (identity plan Phase 6)
# ============================================================================


@pytest.mark.unit
def test__three_groups__get_group_memberships__one_statement_as_the_user() -> None:
    """All groups are checked in one bound statement, run as the user."""
    # Given
    conn = fake_connection(
        [
            statement_response(
                ["member_0", "member_1", "member_2"], [["true", "false", "true"]]
            )
        ]
    )
    groups = {"owner_sme": NASTY, "approver": "OPA-Approver", "admin": "OPA-Admin"}

    # When
    result = lakehouse_access(conn).get_group_memberships(groups)

    # Then
    assert result == {"owner_sme": True, "approver": False, "admin": True}
    [(statement, params)] = conn.calls
    assert_not_interpolated(statement)
    assert "is_member(:group_0) OR is_account_group_member(:group_0)" in statement
    assert params == {
        "group_0": NASTY,
        "group_1": "OPA-Approver",
        "group_2": "OPA-Admin",
    }
    assert conn.identities == [Identity.USER]


@pytest.mark.unit
def test__group_with_other_spelling__get_group_memberships__real_spelling_checked() -> (
    None
):
    """The directory's spelling of a group name is checked too, once each."""
    # Given
    group = "PAG-BEC-LHX-DEV-DataPlatEng-Base"
    conn = fake_connection(
        [statement_response(["member_0", "member_1"], [["false", "true"]])],
        group_names={group: ["PAG-BEC-LHX-dev-DataPlatEng-Base"]},
    )

    # When
    result = lakehouse_access(conn).get_group_memberships(
        {"owner_sme": group, "approver": group, "admin": group}
    )

    # Then
    assert result == {"owner_sme": True, "approver": True, "admin": True}
    [(_, params)] = conn.calls
    assert params == {
        "group_0": "PAG-BEC-LHX-DEV-DataPlatEng-Base",
        "group_1": "PAG-BEC-LHX-dev-DataPlatEng-Base",
    }


@pytest.mark.unit
def test__no_result_row__get_group_memberships__raises() -> None:
    """A missing result row is an error, not "member of nothing"."""
    # When / Then
    with pytest.raises(RuntimeError, match="no row"):
        lakehouse_access(fake_connection()).get_group_memberships({"admin": "G"})


@pytest.mark.unit
def test__no_groups__get_group_memberships__runs_no_query() -> None:
    """Nothing to check, no statement."""
    # Given
    conn = fake_connection()

    # When
    result = lakehouse_access(conn).get_group_memberships({})

    # Then
    assert result == {}
    assert conn.calls == []
