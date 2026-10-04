"""Registry polish (UI_Design.md §4.1): Use Case filter, metric cards, sorting."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.config import AppConfig
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.connection import Identity
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    REGISTRY_SORT_COLUMNS,
    RegistryFilter,
    RegistryPage,
    RegistrySort,
)
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _mock(tmp_path: Path) -> MockDataAccess:
    """Mock data with a third One Pager, so sorting has something to reorder."""
    data_access = MockDataAccess(
        OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    )
    extra = replace(
        data_access._status_rows["OP-0002"],
        one_pager_id="OP-0003",
        product_name="account Master Data",
        owner_name="Carol Jones",
        one_pager_status="Draft",
        data_product_status="In Definition",
    )
    data_access._status_rows[extra.one_pager_id] = extra
    return data_access


def _ids(page: RegistryPage) -> list[str]:
    return [row.one_pager_id for row in page.rows]


# ============================================================================
# RegistrySort
# ============================================================================


@pytest.mark.unit
def test__registry_sort__defaults_to_id_ascending() -> None:
    assert RegistrySort() == RegistrySort("one_pager_id", descending=False)


@pytest.mark.unit
def test__registry_sort__rejects_unknown_columns() -> None:
    with pytest.raises(ValueError, match="Cannot sort"):
        RegistrySort("owner_email; DROP TABLE x")


@pytest.mark.unit
def test__registry_sort__toggle_flips_same_column_and_resets_on_new_column() -> None:
    sort = RegistrySort().toggled("product_name")
    assert sort == RegistrySort("product_name")
    assert sort.toggled("product_name") == RegistrySort("product_name", descending=True)
    assert sort.toggled("product_name").toggled("owner_name") == RegistrySort(
        "owner_name"
    )


# ============================================================================
# MockDataAccess
# ============================================================================


@pytest.mark.unit
def test__mock_registry__filters_by_use_case(tmp_path: Path) -> None:
    data_access = _mock(tmp_path)

    assert _ids(
        data_access.get_registry(RegistryFilter(use_case_id="UC-002"), 1, 20)
    ) == [
        "OP-0001",
        "OP-0002",
    ]
    assert _ids(
        data_access.get_registry(RegistryFilter(use_case_id="UC-003"), 1, 20)
    ) == ["OP-0002"]
    assert (
        data_access.get_registry(RegistryFilter(use_case_id="UC-999"), 1, 20).total_rows
        == 0
    )
    assert data_access.get_registry_status_counts(
        RegistryFilter(use_case_id="UC-001")
    ) == {"Approved": 1}


@pytest.mark.unit
def test__mock_registry__sorts_case_insensitively_in_both_directions(
    tmp_path: Path,
) -> None:
    data_access = _mock(tmp_path)
    by_product = RegistrySort("product_name")

    assert _ids(data_access.get_registry(RegistryFilter(), 1, 20, by_product)) == [
        "OP-0003",  # "account ..." sorts before "Order ..." despite the lower case
        "OP-0002",
        "OP-0001",
    ]
    assert _ids(
        data_access.get_registry(
            RegistryFilter(), 1, 20, by_product.toggled("product_name")
        )
    ) == ["OP-0001", "OP-0002", "OP-0003"]
    assert _ids(
        data_access.get_registry(
            RegistryFilter(), 1, 20, RegistrySort("one_pager_id", True)
        )
    ) == ["OP-0003", "OP-0002", "OP-0001"]


@pytest.mark.unit
@pytest.mark.parametrize("descending", [False, True])
def test__mock_registry__ties_keep_id_order(tmp_path: Path, descending: bool) -> None:
    data_access = _mock(tmp_path)
    sort = RegistrySort("data_product_status", descending)

    ids = _ids(data_access.get_registry(RegistryFilter(), 1, 20, sort))

    # OP-0002 and OP-0003 are both "In Definition"; they stay in ID order.
    assert ids.index("OP-0002") < ids.index("OP-0003")


@pytest.mark.unit
def test__mock_registry__sort_applies_before_pagination(tmp_path: Path) -> None:
    data_access = _mock(tmp_path)
    sort = RegistrySort("one_pager_id", descending=True)

    assert _ids(data_access.get_registry(RegistryFilter(), 1, 2, sort)) == [
        "OP-0003",
        "OP-0002",
    ]
    assert _ids(data_access.get_registry(RegistryFilter(), 2, 2, sort)) == ["OP-0001"]


# ============================================================================
# LakehouseAccess SQL
# ============================================================================


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute_statement(
        self,
        statement: str,
        parameters: dict | None = None,  # noqa: ARG002
        *,
        identity: Identity,  # noqa: ARG002
    ) -> object:
        self.statements.append(statement)
        columns = ["total"] if "COUNT(*) as total" in statement else []
        rows = [["0"]] if columns else []
        return SimpleNamespace(
            manifest=SimpleNamespace(
                schema=SimpleNamespace(
                    columns=[SimpleNamespace(name=c) for c in columns]
                )
            ),
            result=SimpleNamespace(data_array=rows),
        )


@pytest.fixture
def lakehouse_access(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
    connection = _FakeConnection()
    monkeypatch.setattr(lakehouse, "DatabricksConnection", lambda _config: connection)
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=str(tmp_path))
    return lakehouse.LakehouseAccess(
        config, OnePagerDocumentStore(tmp_path)
    ), connection


@pytest.mark.unit
def test__lakehouse_registry__use_case_filter_is_a_references_subquery(
    lakehouse_access: tuple,
) -> None:
    access, connection = lakehouse_access

    access.get_registry(RegistryFilter(use_case_id="UC-0'1"), 1, 20)
    access.get_registry_status_counts(RegistryFilter(use_case_id="UC-0'1"))

    assert len(connection.statements) == 3
    for statement in connection.statements:
        assert "use_case_references WHERE use_case_id = 'UC-0''1'" in statement


@pytest.mark.unit
@pytest.mark.parametrize("column", REGISTRY_SORT_COLUMNS)
def test__lakehouse_registry__orders_by_the_sort_column_then_id(
    lakehouse_access: tuple, column: str
) -> None:
    access, connection = lakehouse_access

    access.get_registry(RegistryFilter(), 2, 20, RegistrySort(column, descending=True))

    page_query = connection.statements[-1]
    if column == "one_pager_id":
        assert "ORDER BY one_pager_id DESC LIMIT 20 OFFSET 20" in page_query
    else:
        assert f"ORDER BY LOWER({column}) DESC, one_pager_id ASC LIMIT 20" in page_query


@pytest.mark.unit
def test__lakehouse_registry__defaults_to_id_order(lakehouse_access: tuple) -> None:
    access, connection = lakehouse_access

    access.get_registry(RegistryFilter(), 1, 20)

    assert "ORDER BY one_pager_id ASC LIMIT 20 OFFSET 0" in connection.statements[-1]


# ============================================================================
# Registry page
# ============================================================================


@pytest.fixture
def app_dir_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "switch_page", lambda _page: None)
    monkeypatch.syspath_prepend(str(APP_DIR))


def _run(tmp_path: Path) -> AppTest:
    data_access = _mock(tmp_path)
    user = make_user("ABR", "Alice Brown")
    at = AppTest.from_file(str(APP_DIR / "views" / "registry.py"), default_timeout=30)
    for key, value in {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": data_access._document_store,
        "current_user": user.username,
        "current_user_info": user,
    }.items():
        at.session_state[key] = value
    return at.run()


def _row_ids(at: AppTest) -> list[str]:
    return [
        b.key.removeprefix("opview-")
        for b in at.button
        if str(b.key).startswith("opview-")
    ]


@pytest.mark.unit
def test__registry_page__metric_cards_have_no_show_button(
    tmp_path: Path, app_dir_on_path: None
) -> None:
    at = _run(tmp_path)

    assert not any(str(b.key).startswith("registry-metric-") for b in at.button)
    assert not any(b.label in ("Show", "✓ Showing") for b in at.button)


@pytest.mark.unit
def test__registry_page__header_click_sorts_and_reverses(
    tmp_path: Path, app_dir_on_path: None
) -> None:
    at = _run(tmp_path)
    assert at.button(key="registry-sort-one_pager_id").label == "ID ▲"

    at.button(key="registry-sort-product_name").click().run()

    assert not at.exception
    assert at.button(key="registry-sort-product_name").label == "Product ▲"
    assert at.button(key="registry-sort-one_pager_id").label == "ID"
    assert _row_ids(at) == ["OP-0003", "OP-0002", "OP-0001"]

    at.button(key="registry-sort-product_name").click().run()

    assert at.button(key="registry-sort-product_name").label == "Product ▼"
    assert _row_ids(at) == ["OP-0001", "OP-0002", "OP-0003"]


@pytest.mark.unit
def test__registry_page__use_case_filter(tmp_path: Path, app_dir_on_path: None) -> None:
    at = _run(tmp_path)
    use_case = at.selectbox(key="filter_use_case")
    assert use_case.options[0] == "All"
    assert any(option.startswith("UC-003 · ") for option in use_case.options)

    use_case.set_value("UC-003").run()

    assert not at.exception
    assert _row_ids(at) == ["OP-0002"]

    at.selectbox(key="filter_op_status").set_value("Approved").run()

    assert any("No One Pagers match your filters" in w.value for w in at.warning)
