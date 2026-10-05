"""Registry and Use Case list caching (UI_Design.md §6)."""

from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.config import AppConfig
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import RegistryFilter, RegistrySort, UseCaseFilter
from tests.helpers import mock_data_access, page_app

COUNTED = {
    "get_registry": "registry",
    "get_registry_status_counts": "counts",
    "get_use_cases": "use_cases",
}


def _count_list_queries(data_access: MockDataAccess) -> dict[str, int]:
    """Count the list queries of ``data_access``; return the live counters."""
    calls = dict.fromkeys(COUNTED.values(), 0)

    def counting(method: Callable, key: str) -> Callable:
        def wrapper(*args: object, **kwargs: object) -> object:
            calls[key] += 1
            return method(*args, **kwargs)

        return wrapper

    for name, key in COUNTED.items():
        setattr(data_access, name, counting(getattr(data_access, name), key))
    return calls


@pytest.fixture
def cache(import_app_module: Callable[[str], ModuleType]) -> Iterator[ModuleType]:
    """Return ``adapters.cache`` with empty caches before and after the test."""
    module = import_app_module("adapters.cache")
    module.invalidate_list_caches()
    yield module
    module.invalidate_list_caches()


@pytest.fixture
def calls(mock_data_access: MockDataAccess) -> dict[str, int]:
    """Count the list queries of the mock data access."""
    return _count_list_queries(mock_data_access)


def _open_details(at: AppTest, use_case_id: str) -> None:
    """Select a Use Case as a row click does (AppTest cannot click rows)."""
    at.session_state["uc_selected_id"] = use_case_id
    at.run()


@pytest.mark.unit
def test__registry_read_once__read_again__served_from_cache(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """The same Registry page is queried once."""
    # Given
    first = cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)

    # When
    second = cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)

    # Then
    assert calls["registry"] == 1
    assert [r.one_pager_id for r in second.rows] == [r.one_pager_id for r in first.rows]


@pytest.mark.unit
def test__different_filter_sort_and_page__get_registry__one_query_each(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """Filter, sort and page are part of the cache key."""
    # When
    cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)
    cache.get_registry(mock_data_access, RegistryFilter(op_status="Draft"), 1, 20)
    cache.get_registry(mock_data_access, RegistryFilter(use_case_id="UC-001"), 1, 20)
    cache.get_registry(mock_data_access, RegistryFilter(), 2, 20)
    cache.get_registry(
        mock_data_access, RegistryFilter(), 1, 20, RegistrySort("product_name")
    )
    cache.get_registry(
        mock_data_access, RegistryFilter(), 1, 20, RegistrySort("product_name", True)
    )

    # Then
    assert calls["registry"] == 6


@pytest.mark.unit
def test__filtered_registry_cached__read_again__cached_filtered_rows(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """A cached filtered page is reused and still filtered."""
    # Given
    cache.get_registry(mock_data_access, RegistryFilter(op_status="Draft"), 1, 20)

    # When
    page = cache.get_registry(
        mock_data_access, RegistryFilter(op_status="Draft"), 1, 20
    )

    # Then
    assert calls["registry"] == 1
    assert all(r.one_pager_status == "Draft" for r in page.rows)


@pytest.mark.unit
def test__repeated_counts_and_use_cases__cached_reads__one_query_per_key(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """Status counts and Use Case pages are cached too."""
    # When
    for _ in range(2):
        cache.get_registry_status_counts(mock_data_access, RegistryFilter())
        cache.get_use_cases(mock_data_access, UseCaseFilter(), 1, 20)
    cache.get_use_cases(mock_data_access, UseCaseFilter(include_deprecated=True), 1, 20)

    # Then
    assert (calls["counts"], calls["use_cases"]) == (1, 2)


@pytest.mark.unit
def test__cached_lists__write_through_writes_data__every_list_read_again(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """A write clears every list cache, so the change shows at once."""
    # Given
    cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)
    cache.get_registry_status_counts(mock_data_access, RegistryFilter())
    cache.get_use_cases(mock_data_access, UseCaseFilter(), 1, 20)

    # When
    with cache.writes_data():
        mock_data_access._status_rows["OP-0002"].product_name = "Renamed Product"

    # Then
    page = cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)
    cache.get_registry_status_counts(mock_data_access, RegistryFilter())
    cache.get_use_cases(mock_data_access, UseCaseFilter(), 1, 20)
    assert calls == {"registry": 2, "counts": 2, "use_cases": 2}
    assert "Renamed Product" in [r.product_name for r in page.rows]


@pytest.mark.unit
def test__cached_registry__failing_write__cache_cleared_anyway(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """A write that fails half way also clears the caches."""
    # Given
    cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)

    # When
    with pytest.raises(RuntimeError), cache.writes_data():
        raise RuntimeError

    # Then
    cache.get_registry(mock_data_access, RegistryFilter(), 1, 20)
    assert calls["registry"] == 2


@pytest.mark.unit
def test__two_mock_instances__get_registry__caches_not_shared(
    cache: ModuleType, tmp_path: Path
) -> None:
    """Each mock instance has its own cache scope."""
    # Given
    one, other = mock_data_access(tmp_path), mock_data_access(tmp_path)
    other._status_rows.pop("OP-0002")

    # When
    one_rows = cache.get_registry(one, RegistryFilter(), 1, 20).total_rows
    other_rows = cache.get_registry(other, RegistryFilter(), 1, 20).total_rows

    # Then
    assert one.cache_scope != other.cache_scope
    assert (one_rows, other_rows) == (2, 1)


@pytest.mark.unit
def test__two_lakehouse_sessions__cache_scope__shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """All sessions on the Lakehouse share one cache."""
    # Given
    monkeypatch.setattr(lakehouse, "DatabricksConnection", lambda _config: object())
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=str(tmp_path))
    store = OnePagerDocumentStore(tmp_path)

    # When
    first = lakehouse.LakehouseAccess(config, store)
    second = lakehouse.LakehouseAccess(config, store)

    # Then
    assert first.cache_scope == second.cache_scope
    assert first.cache_scope.startswith("lakehouse:")


# ============================================================================
# Pages
# ============================================================================


@pytest.mark.unit
def test__registry_page_shown__rerun__no_new_queries(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """Reruns of the Registry page use the cache."""
    # Given
    at = page_app("registry.py", mock_data_access).run()
    assert not at.exception
    after_first_run = dict(calls)

    # When
    at.run()

    # Then
    assert calls == after_first_run


@pytest.mark.unit
def test__registry_page_shown__write_then_rerun__registry_read_again(
    cache: ModuleType, mock_data_access: MockDataAccess, calls: dict[str, int]
) -> None:
    """After a write the Registry shows the change."""
    # Given
    at = page_app("registry.py", mock_data_access).run()
    registry_queries = calls["registry"]

    # When
    with cache.writes_data():
        mock_data_access._status_rows["OP-0001"].one_pager_status = "Draft Update"
    at.run()

    # Then
    assert calls["registry"] == registry_queries + 1
    assert "Draft Update" in list(at.dataframe[0].value["One Pager status"])


@pytest.mark.unit
def test__deprecated_use_case_shown__restore_and_hide_deprecated__listed_at_once(
    cache: ModuleType, mock_data_access: MockDataAccess
) -> None:
    """A restored Use Case is counted among the active ones straight away."""
    # Given
    at = page_app("use_cases.py", mock_data_access).run()
    assert not at.exception
    active = mock_data_access.get_use_cases(UseCaseFilter(), 1, 100).total_rows
    assert any(f"of {active} Use Cases" in m.value for m in at.markdown)
    at.checkbox(key="uc_filter_show_deprecated").check().run()
    _open_details(at, "UC-005")

    # When
    at.button(key="uc_restore").click().run()
    at.checkbox(key="uc_filter_show_deprecated").uncheck().run()

    # Then
    assert not at.exception
    assert any(f"of {active + 1} Use Cases" in m.value for m in at.markdown)
