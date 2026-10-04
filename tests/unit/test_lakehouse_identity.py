"""Which identity each LakehouseAccess statement runs as (identity plan Phase 4).

Reads run as the signed-in user so Unity Catalog group grants apply; writes,
and the ID-sequence read that is part of a write, run as the service
principal (Architecture.md §8, Decision_Log §19).
"""

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import streamlit as st

from onepagerapp.config import AppConfig
from onepagerapp.data_access import connection as connection_module
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.connection import (
    USER_TOKEN_HEADER,
    Identity,
    MissingUserTokenError,
)
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import AuthorizedUser, RegistryFilter, UseCaseFilter
from tests.conftest import FIXTURES_DIR
from tests.unit.test_lakehouse_writes import (
    ACTOR_WRITES,
    WRITES_WITHOUT_ACTOR,
    _access,
    _actor_comment,
    _response,
)

_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "MERGE")


class _UserMayNotWrite:
    """Fake connection that fails if a write statement is sent as the user."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Identity]] = []

    def execute_statement(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,  # noqa: ARG002
        *,
        identity: Identity,
    ) -> SimpleNamespace:
        is_write = statement.lstrip().upper().startswith(_WRITE_KEYWORDS)
        if is_write and identity is Identity.USER:
            msg = f"write sent as the user: {statement}"
            raise AssertionError(msg)
        self.calls.append((statement, identity))
        if statement.startswith("SELECT last_value"):
            return _response(["last_value"], [["4"]])
        if is_write:
            return _response(["num_affected_rows"], [["1"]])
        return _response([], [])

    def find_group_names(self, name: str) -> list[str]:  # noqa: ARG002
        return []


# Read method -> call. read_document is not here: it reads the YAML file
# through the volume mount, which is always the service principal (§8).
READS: dict[str, Callable[[LakehouseAccess], object]] = {
    "get_current_user": lambda a: a.get_current_user(),
    "get_group_memberships": lambda a: a.get_group_memberships({"admin": "G"}),
    "read_table": lambda a: a.read_table("ref_op_status"),
    "get_ref_op_status": lambda a: a.get_ref_op_status(),
    "get_ref_dp_status": lambda a: a.get_ref_dp_status(),
    "get_ref_business_domains": lambda a: a.get_ref_business_domains(),
    "get_ref_data_product_types": lambda a: a.get_ref_data_product_types(),
    "get_ref_source_systems": lambda a: a.get_ref_source_systems(),
    "get_registry": lambda a: a.get_registry(RegistryFilter(), 1, 20),
    "get_registry_status_counts": lambda a: a.get_registry_status_counts(
        RegistryFilter()
    ),
    "get_one_pager": lambda a: a.get_one_pager("OP-0001"),
    "get_one_pager_status": lambda a: a.get_one_pager_status("OP-0001"),
    "get_change_log": lambda a: a.get_change_log("OP-0001"),
    "get_review_comments": lambda a: a.get_review_comments("OP-0001"),
    "get_lock": lambda a: a.get_lock("OP-0001"),
    "get_locks": lambda a: a.get_locks(["OP-0001", "OP-0002"]),
    "get_one_pager_ids_for_data_product": lambda a: (
        a.get_one_pager_ids_for_data_product("customer_master")
    ),
    "get_authorized_users": lambda a: a.get_authorized_users("OP-0001"),
    "get_one_pager_status_row": lambda a: a.get_one_pager_status_row("OP-0001"),
    "get_one_pager_status_rows": lambda a: a.get_one_pager_status_rows("Draft"),
    "get_pending_pr_rows": lambda a: a.get_pending_pr_rows(),
    "get_use_cases": lambda a: a.get_use_cases(UseCaseFilter(), 1, 20),
    "get_use_case": lambda a: a.get_use_case("UC-001"),
    "get_use_case_references": lambda a: a.get_use_case_references("UC-001"),
    "get_linked_use_case_ids": lambda a: a.get_linked_use_case_ids("OP-0001"),
}

# Reads that are part of a write: must see what the writer sees.
READS_AS_APP: dict[str, Callable[[LakehouseAccess], object]] = {
    "get_sequence_value": lambda a: a.get_sequence_value("OP"),
}

# Writes that record no actor (reasons in test_lakehouse_writes).
_USER = AuthorizedUser("OP-0001", "ABR", "A", "a@b.dk", "owner")
OTHER_WRITES: dict[str, Callable[[LakehouseAccess], object]] = {
    "insert_authorized_users": lambda a: a.insert_authorized_users([_USER]),
    "update_authorized_users": lambda a: a.update_authorized_users([_USER]),
    "delete_authorized_users": lambda a: a.delete_authorized_users("OP-1", ["ABR"]),
    "add_use_case_reference": lambda a: a.add_use_case_reference("OP-1", "UC-001"),
    "remove_use_case_reference": lambda a: a.remove_use_case_reference(
        "OP-1", "UC-001"
    ),
    "delete_reference_value": lambda a: a.delete_reference_value(
        "ref_business_domains", "HR"
    ),
    "delete_one_pager_records": lambda a: a.delete_one_pager_records("OP-1"),
    "delete_review_comment": lambda a: a.delete_review_comment(_actor_comment()),
    "compare_and_set_sequence": lambda a: a.compare_and_set_sequence("OP", 4, 5),
}
WRITES = {**ACTOR_WRITES, **OTHER_WRITES}


def _run(call: Callable[[LakehouseAccess], object]) -> list[tuple[str, Identity]]:
    conn = _UserMayNotWrite()
    access = _access(conn)
    access._document_store = OnePagerDocumentStore(FIXTURES_DIR)
    # The fake's empty results may not parse; the statements ran regardless.
    with suppress(RuntimeError, KeyError, ValueError, AttributeError):
        call(access)
    return conn.calls


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(READS))
def test__read__runs_as_the_user(method: str) -> None:
    calls = _run(READS[method])

    assert calls, f"{method} ran no statement"
    assert {identity for _, identity in calls} == {Identity.USER}


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(READS_AS_APP))
def test__read_part_of_a_write__runs_as_the_service_principal(method: str) -> None:
    calls = _run(READS_AS_APP[method])

    assert calls
    assert {identity for _, identity in calls} == {Identity.APP}


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(WRITES))
def test__write__runs_as_the_service_principal(method: str) -> None:
    calls = _run(WRITES[method])

    assert calls, f"{method} ran no statement"
    assert {identity for _, identity in calls} == {Identity.APP}


@pytest.mark.unit
def test__fake_refuses_a_write_sent_as_the_user() -> None:
    with pytest.raises(AssertionError, match="write sent as the user"):
        _UserMayNotWrite().execute_statement(
            "UPDATE cat.sch.locks SET x = 1", identity=Identity.USER
        )


@pytest.mark.unit
def test__every_statement_method_has_an_expected_identity() -> None:
    """A new public LakehouseAccess method must be added to one of the lists."""
    public = {
        name
        for name in vars(LakehouseAccess)
        if not name.startswith("_") and callable(getattr(LakehouseAccess, name))
    }
    classified = set(READS) | set(READS_AS_APP) | set(WRITES) | {"read_document"}

    assert public == classified
    assert set(WRITES) == set(ACTOR_WRITES) | set(WRITES_WITHOUT_ACTOR)


@pytest.mark.unit
def test__deployed_read_without_user_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No fallback to the service principal when the token header is missing."""
    service_principal = SimpleNamespace(
        config=SimpleNamespace(host="https://adb.example"),
        statement_execution=SimpleNamespace(execute_statement=_never_called),
    )
    monkeypatch.setattr(connection_module, "WorkspaceClient", lambda: service_principal)
    monkeypatch.setattr(st, "context", SimpleNamespace(headers={}))
    access = lakehouse.LakehouseAccess(
        AppConfig(APP_MODE="databricks", ONE_PAGER_APP_VOLUME_PATH=str(tmp_path)),
        OnePagerDocumentStore(tmp_path),
    )

    with pytest.raises(MissingUserTokenError):
        access.get_one_pager_status_row("OP-0001")


@pytest.mark.unit
def test__deployed_read_uses_the_forwarded_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built: list[dict[str, object]] = []

    def workspace_client(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        return SimpleNamespace(
            config=SimpleNamespace(host="https://adb.example"),
            statement_execution=SimpleNamespace(
                execute_statement=lambda **_: SimpleNamespace(
                    status=None, manifest=None, result=None
                )
            ),
        )

    monkeypatch.setattr(connection_module, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(
        st, "context", SimpleNamespace(headers={USER_TOKEN_HEADER: "user-token"})
    )
    access = lakehouse.LakehouseAccess(
        AppConfig(APP_MODE="databricks", ONE_PAGER_APP_VOLUME_PATH=str(tmp_path)),
        OnePagerDocumentStore(tmp_path),
    )

    assert access.get_one_pager_status_row("OP-0001") is None
    assert built[-1]["token"] == "user-token"  # noqa: S105 - a test value


def _never_called(**_: object) -> None:
    msg = "the read must not run as the service principal"
    raise AssertionError(msg)
