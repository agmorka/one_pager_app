"""Registry and Use Case list caching (UI_Design.md §6)."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.config import AppConfig
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    RegistryFilter,
    RegistryPage,
    RegistrySort,
    UseCaseFilter,
    UseCasePage,
)
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"
sys.path.insert(0, str(APP_DIR))

from adapters import cache  # noqa: E402


class _CountingDataAccess(MockDataAccess):
    """Mock data access that counts the list queries."""

    def __init__(self, store: OnePagerDocumentStore) -> None:
        super().__init__(store)
        self.calls: dict[str, int] = {"registry": 0, "counts": 0, "use_cases": 0}

    def get_registry(
        self,
        filter: RegistryFilter,  # noqa: A002
        page: int,
        page_size: int,
        sort: RegistrySort | None = None,
    ) -> RegistryPage:
        self.calls["registry"] += 1
        return super().get_registry(filter, page, page_size, sort)

    def get_registry_status_counts(
        self,
        filter: RegistryFilter,  # noqa: A002
    ) -> dict[str, int]:
        self.calls["counts"] += 1
        return super().get_registry_status_counts(filter)

    def get_use_cases(
        self,
        filter: UseCaseFilter,  # noqa: A002
        page: int,
        page_size: int,
    ) -> UseCasePage:
        self.calls["use_cases"] += 1
        return super().get_use_cases(filter, page, page_size)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    cache.invalidate_list_caches()
    yield
    cache.invalidate_list_caches()


@pytest.fixture
def data_access(tmp_path: Path) -> _CountingDataAccess:
    return _CountingDataAccess(OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path))


@pytest.mark.unit
def test__registry__repeated_reads_hit_the_cache(
    data_access: _CountingDataAccess,
) -> None:
    first = cache.get_registry(data_access, RegistryFilter(), 1, 20)
    second = cache.get_registry(data_access, RegistryFilter(), 1, 20)

    assert data_access.calls["registry"] == 1
    assert [r.one_pager_id for r in second.rows] == [r.one_pager_id for r in first.rows]


@pytest.mark.unit
def test__registry__filter_sort_and_page_are_part_of_the_key(
    data_access: _CountingDataAccess,
) -> None:
    cache.get_registry(data_access, RegistryFilter(), 1, 20)
    cache.get_registry(data_access, RegistryFilter(op_status="Draft"), 1, 20)
    cache.get_registry(data_access, RegistryFilter(use_case_id="UC-001"), 1, 20)
    cache.get_registry(data_access, RegistryFilter(), 2, 20)
    cache.get_registry(
        data_access, RegistryFilter(), 1, 20, RegistrySort("product_name")
    )
    cache.get_registry(
        data_access, RegistryFilter(), 1, 20, RegistrySort("product_name", True)
    )

    assert data_access.calls["registry"] == 6

    by_draft = cache.get_registry(data_access, RegistryFilter(op_status="Draft"), 1, 20)
    assert data_access.calls["registry"] == 6
    assert all(r.one_pager_status == "Draft" for r in by_draft.rows)


@pytest.mark.unit
def test__status_counts_and_use_cases_are_cached(
    data_access: _CountingDataAccess,
) -> None:
    for _ in range(2):
        cache.get_registry_status_counts(data_access, RegistryFilter())
        cache.get_use_cases(data_access, UseCaseFilter(), 1, 20)
    cache.get_use_cases(data_access, UseCaseFilter(include_deprecated=True), 1, 20)

    assert data_access.calls["counts"] == 1
    assert data_access.calls["use_cases"] == 2


@pytest.mark.unit
def test__writes_data__clears_every_list_cache(
    data_access: _CountingDataAccess,
) -> None:
    cache.get_registry(data_access, RegistryFilter(), 1, 20)
    cache.get_registry_status_counts(data_access, RegistryFilter())
    cache.get_use_cases(data_access, UseCaseFilter(), 1, 20)

    with cache.writes_data():
        data_access._status_rows["OP-0002"].product_name = "Renamed Product"

    page = cache.get_registry(data_access, RegistryFilter(), 1, 20)
    cache.get_registry_status_counts(data_access, RegistryFilter())
    cache.get_use_cases(data_access, UseCaseFilter(), 1, 20)

    assert data_access.calls == {"registry": 2, "counts": 2, "use_cases": 2}
    assert "Renamed Product" in [r.product_name for r in page.rows]


def _failing_write() -> None:
    message = "write failed half way"
    raise RuntimeError(message)


@pytest.mark.unit
def test__writes_data__clears_the_caches_when_the_write_fails(
    data_access: _CountingDataAccess,
) -> None:
    cache.get_registry(data_access, RegistryFilter(), 1, 20)

    with pytest.raises(RuntimeError), cache.writes_data():
        _failing_write()

    cache.get_registry(data_access, RegistryFilter(), 1, 20)
    assert data_access.calls["registry"] == 2


@pytest.mark.unit
def test__separate_mock_instances_do_not_share_cached_lists(tmp_path: Path) -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    one, other = _CountingDataAccess(store), _CountingDataAccess(store)
    other._status_rows.pop("OP-0002")

    assert one.cache_scope != other.cache_scope
    assert one.cache_scope == one.cache_scope
    assert cache.get_registry(one, RegistryFilter(), 1, 20).total_rows == 2
    assert cache.get_registry(other, RegistryFilter(), 1, 20).total_rows == 1


@pytest.mark.unit
def test__lakehouse_sessions_share_one_cache_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lakehouse, "DatabricksConnection", lambda _config: object())
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=str(tmp_path))
    store = OnePagerDocumentStore(tmp_path)

    first = lakehouse.LakehouseAccess(config, store)
    second = lakehouse.LakehouseAccess(config, store)

    assert first.cache_scope == second.cache_scope
    assert first.cache_scope.startswith("lakehouse:")


# ============================================================================
# Pages
# ============================================================================


def _app(page: str, data_access: MockDataAccess) -> AppTest:
    user = make_user("ABR", "Alice Brown")
    at = AppTest.from_file(str(APP_DIR / "views" / page), default_timeout=30)
    for key, value in {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": data_access._document_store,
        "current_user": user.username,
        "current_user_info": user,
    }.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__registry_page__reruns_use_the_cache_until_a_write(
    data_access: _CountingDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(APP_DIR))
    at = _app("registry.py", data_access).run()
    assert not at.exception
    calls_after_first_run = dict(data_access.calls)

    at.run()

    assert data_access.calls == calls_after_first_run

    with cache.writes_data():
        data_access._status_rows["OP-0001"].one_pager_status = "Draft Update"
    at.run()

    assert data_access.calls["registry"] == calls_after_first_run["registry"] + 1
    assert "Draft Update" in [m.value for m in at.markdown]


@pytest.mark.unit
def test__use_cases_page__lists_a_restored_use_case_at_once(
    data_access: _CountingDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(APP_DIR))
    at = _app("use_cases.py", data_access).run()
    assert not at.exception
    active = data_access.get_use_cases(UseCaseFilter(), 1, 100).total_rows
    assert any(f"of {active} Use Cases" in m.value for m in at.markdown)

    at.checkbox(key="uc_filter_show_deprecated").check().run()
    at.button(key="uc_details_UC-005").click().run()
    at.button(key="uc_restore").click().run()
    at.checkbox(key="uc_filter_show_deprecated").uncheck().run()

    assert not at.exception
    assert any(f"of {active + 1} Use Cases" in m.value for m in at.markdown)
