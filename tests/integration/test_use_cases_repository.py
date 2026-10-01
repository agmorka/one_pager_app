"""Integration tests for the Use Case registry against a live SQL warehouse.

Requires the Liquibase-deployed use_cases, use_case_references and id_sequences
tables, and the usual environment (APP_MODE=local-integration,
DATABRICKS_WAREHOUSE_ID, ONE_PAGER_APP_VOLUME_PATH, catalog/schema variables).
Skipped when no warehouse is configured.

Every Use Case created here is deleted again on teardown. Allocated IDs are not
returned to the sequence — gaps are harmless (Data_Model.md §4).
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from onepagerapp.config import AppConfig
from onepagerapp.data_access.connection import Identity
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import UseCaseFilter, UseCaseInput

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABRICKS_WAREHOUSE_ID"),
        reason="DATABRICKS_WAREHOUSE_ID is not set; no SQL warehouse available",
    ),
]

TRICKY = "O'Brien; DROP TABLE use_cases; -- 100% _done_ \\ back"


class _Tracked:
    """LakehouseAccess plus the IDs created through it, for cleanup."""

    def __init__(self, access: LakehouseAccess) -> None:
        self.access = access
        self.created: list[str] = []

    def create(self, data: UseCaseInput) -> str:
        use_case_id = self.access.create_use_case(data, "ITS")
        self.created.append(use_case_id)
        return use_case_id


@pytest.fixture
def tracked() -> Iterator[_Tracked]:
    config = AppConfig.from_env()
    access = LakehouseAccess(
        config, OnePagerDocumentStore(config.ONE_PAGER_APP_VOLUME_PATH)
    )
    tracked = _Tracked(access)
    yield tracked
    for use_case_id in tracked.created:
        access._connection.execute_statement(
            f"DELETE FROM {access._fqn_prefix}.use_cases "  # noqa: S608
            f"WHERE use_case_id = :use_case_id",
            {"use_case_id": use_case_id},
            identity=Identity.APP,
        )


def _input(**overrides: str) -> UseCaseInput:
    values = {
        "persona": "Integration Tester",
        "goal": TRICKY,
        "scenario": "Runs the integration suite",
        "decision_enabled": "Trust the Use Case registry",
        "priority": "Low",
    }
    values.update(overrides)
    return UseCaseInput(**values)


def test_create_edit_deprecate_restore_round_trip(tracked: _Tracked) -> None:
    access = tracked.access
    use_case_id = tracked.create(_input())

    created = access.get_use_case(use_case_id)
    assert created is not None
    assert created.goal == TRICKY  # stored verbatim, no injection
    assert created.deprecated is False
    assert created.created_by == "ITS"
    assert created.reference_count == 0
    assert access.get_use_case_references(use_case_id) == []

    access.update_use_case(use_case_id, _input(persona=TRICKY, priority="High"), "IT2")
    updated = access.get_use_case(use_case_id)
    assert updated.persona == TRICKY
    assert updated.priority == "High"
    assert updated.last_updated_by == "IT2"
    assert updated.created_by == "ITS"

    access.set_use_case_deprecated(use_case_id, True, "IT3")
    assert access.get_use_case(use_case_id).deprecated is True
    active = access.get_use_cases(UseCaseFilter(search=TRICKY), 1, 50)
    assert use_case_id not in [uc.use_case_id for uc in active.rows]
    everything = access.get_use_cases(
        UseCaseFilter(search=TRICKY, include_deprecated=True), 1, 50
    )
    assert use_case_id in [uc.use_case_id for uc in everything.rows]

    access.set_use_case_deprecated(use_case_id, False, "IT3")
    assert access.get_use_case(use_case_id).deprecated is False


def test_search_treats_like_wildcards_literally(tracked: _Tracked) -> None:
    use_case_id = tracked.create(_input(goal="Integration 100% literal"))
    access = tracked.access
    found = access.get_use_cases(UseCaseFilter(search="100% literal"), 1, 50)
    assert use_case_id in [uc.use_case_id for uc in found.rows]
    none = access.get_use_cases(UseCaseFilter(search="Integration 1_0%"), 1, 50)
    assert use_case_id not in [uc.use_case_id for uc in none.rows]


def test_concurrent_creates_get_distinct_ids(tracked: _Tracked) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: tracked.create(_input()), range(2)))
    assert len(set(ids)) == 2
