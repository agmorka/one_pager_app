"""Integration tests for the create flow against a live SQL warehouse.

Run after the Liquibase migrations (id_sequences, one_pager_authorized_users)
in INT, with APP_MODE=local-integration (or databricks) and
DATABRICKS_WAREHOUSE_ID / ONE_PAGER_APP_* set. Skipped otherwise.

Test data uses the reserved OP-99xx range and a temporary id_sequences row,
and is removed in teardown (Testing_Strategy.md §4).
"""

import os
import shutil
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from onepagerapp.config import AppConfig
from onepagerapp.data_access.connection import Identity
from onepagerapp.data_access.factory import create_data_access, create_document_store
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    NewOnePagerInput,
    OnePagerStatusRow,
    PersonRef,
)
from onepagerapp.workflow import create_one_pager
from tests.users import CREATOR_ROLES, make_user

TEST_OP_ID = "OP-9990"
TEST_SEQUENCE = "T9"
NASTY_TEXT = "O'Brien'); DROP TABLE one_pager_status; --"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def config() -> AppConfig:
    if not os.environ.get("DATABRICKS_WAREHOUSE_ID") or not os.environ.get(
        "ONE_PAGER_APP_VOLUME_PATH"
    ):
        pytest.skip("Live Databricks configuration not available")
    cfg = AppConfig.from_env()
    if not cfg.uses_databricks:
        pytest.skip("APP_MODE is not a Databricks mode")
    return cfg


@pytest.fixture(scope="module")
def data_access(config: AppConfig) -> Iterator[LakehouseAccess]:
    access = create_data_access(config)
    assert isinstance(access, LakehouseAccess)
    access.delete_one_pager_records(TEST_OP_ID)  # pre-suite sweep
    yield access
    access.delete_one_pager_records(TEST_OP_ID)


def _status_row(now: datetime) -> OnePagerStatusRow:
    return OnePagerStatusRow(
        one_pager_id=TEST_OP_ID,
        data_product="it_test_data_product_9990",
        product_name=NASTY_TEXT,
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.1.0",
        owner_name="Integration Test",
        owner_initials="ITT",
        owner_email="it@bec.dk",
        owner_team=None,
        created_by="ITT",
        created_at=now,
        last_updated_at=now,
        last_updated_by="ITT",
        structure_definition="structure_one_pager_v_1.json",
    )


def test__insert_and_read_back_all_tables(data_access: LakehouseAccess) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    data_access.insert_authorized_users(
        [
            AuthorizedUser(TEST_OP_ID, "ITT", "Integration Test", "it@bec.dk", "owner"),
            AuthorizedUser(TEST_OP_ID, "ITS", NASTY_TEXT, "its@bec.dk", "sme", "QA"),
        ]
    )
    data_access.append_change_log(
        ChangeLogEntry(
            id=0,
            one_pager_id=TEST_OP_ID,
            version="0.1.0",
            event_type="creation",
            author_initials="ITT",
            author_name="Integration Test",
            summary=NASTY_TEXT,
            created_at=now,
            to_status="Draft",
            status_field="one_pager_status",
        )
    )
    data_access.insert_one_pager_status(_status_row(now))

    header = data_access.get_one_pager_status(TEST_OP_ID)
    assert header.product_name == NASTY_TEXT  # stored verbatim, not executed
    assert header.one_pager_status == "Draft"
    [entry] = data_access.get_change_log(TEST_OP_ID)
    assert entry.id > 0  # GENERATED ALWAYS AS IDENTITY
    assert entry.summary == NASTY_TEXT
    users = data_access.get_authorized_users(TEST_OP_ID)
    assert {(u.user_initials, u.role) for u in users} == {
        ("ITT", "owner"),
        ("ITS", "sme"),
    }
    assert data_access.get_one_pager_ids_for_data_product(
        "it_test_data_product_9990"
    ) == [TEST_OP_ID]

    data_access.delete_one_pager_records(TEST_OP_ID)
    assert data_access.get_one_pager_status(TEST_OP_ID) is None
    assert data_access.get_change_log(TEST_OP_ID) == []
    assert data_access.get_authorized_users(TEST_OP_ID) == []


@pytest.fixture
def test_sequence(data_access: LakehouseAccess) -> Iterator[str]:
    fqn = f"{data_access._fqn_prefix}.id_sequences"
    conn = data_access._connection
    delete = f"DELETE FROM {fqn} WHERE id_type = :t"  # noqa: S608
    insert = f"INSERT INTO {fqn} (id_type, last_value) VALUES (:t, 0)"  # noqa: S608
    conn.execute_statement(delete, {"t": TEST_SEQUENCE}, identity=Identity.APP)
    conn.execute_statement(insert, {"t": TEST_SEQUENCE}, identity=Identity.APP)
    yield TEST_SEQUENCE
    conn.execute_statement(delete, {"t": TEST_SEQUENCE}, identity=Identity.APP)


def test__compare_and_set__concurrent_writers_do_not_collide(
    data_access: LakehouseAccess, test_sequence: str
) -> None:
    current = data_access.get_sequence_value(test_sequence)
    results: list[bool] = []

    def attempt() -> None:
        results.append(
            data_access.compare_and_set_sequence(test_sequence, current, current + 1)
        )

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert data_access.get_sequence_value(test_sequence) == current + 1


def test__create_one_pager__end_to_end(
    config: AppConfig, data_access: LakehouseAccess
) -> None:
    """Full create through the workflow, including the volume write."""
    if not Path(config.ONE_PAGER_APP_VOLUME_PATH).exists():
        pytest.skip("Volume path not mounted in this environment")
    store = create_document_store(config)
    data_access_with_store = create_data_access(config, store)
    user = make_user("ITT")
    data = NewOnePagerInput(
        data_product=f"it_test_{datetime.now(UTC):%Y%m%d%H%M%S}",
        product_name="Integration Test Product",
        business_domain="Customer",
        data_product_type="Foundational",
        description="Created by tests/integration/test_create_one_pager.py",
        owner=PersonRef("Integration Test", "ITT", "it@bec.dk"),
    )

    result = create_one_pager(
        data, user, data_access_with_store, store, roles=CREATOR_ROLES
    )
    try:
        assert result.ok, result.errors
        header = data_access_with_store.get_one_pager_status(result.one_pager_id)
        assert header.version == "0.1.0"
        assert store.exists(result.one_pager_id, "0.1.0")
    finally:
        if result.one_pager_id:
            data_access_with_store.delete_one_pager_records(result.one_pager_id)
            shutil.rmtree(
                Path(config.ONE_PAGER_APP_VOLUME_PATH) / result.one_pager_id,
                ignore_errors=True,
            )
