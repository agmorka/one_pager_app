"""Registry polish (UI_Design.md §4.1): Use Case filter, metric cards, sorting."""

from collections.abc import Callable
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import (
    REGISTRY_SORT_COLUMNS,
    RegistryFilter,
    RegistrySort,
)
from tests.helpers import NEW_ID, page_app, statement_response


@pytest.fixture
def registry(mock_data_access: MockDataAccess) -> MockDataAccess:
    """Add a third One Pager, OP-0003 "account Master Data", to reorder."""
    rows = mock_data_access._status_rows
    rows[NEW_ID] = replace(
        rows["OP-0002"],
        one_pager_id=NEW_ID,
        product_name="account Master Data",
        owner_name="Carol Jones",
        one_pager_status="Draft",
        data_product_status="In Definition",
    )
    return mock_data_access


@pytest.fixture
def counting(connection: SimpleNamespace) -> SimpleNamespace:
    """Make the fake connection answer COUNT queries with 0."""
    connection.handler = lambda statement, _params: (
        statement_response(["total"], [["0"]])
        if "COUNT(*) as total" in statement
        else statement_response([], [])
    )
    return connection


def _ids(
    data_access: MockDataAccess,
    registry_filter: RegistryFilter | None = None,
    sort: RegistrySort | None = None,
    page: int = 1,
    page_size: int = 20,
) -> list[str]:
    """Return the One Pager IDs on a Registry page."""
    result = data_access.get_registry(
        registry_filter or RegistryFilter(), page, page_size, sort
    )
    return [row.one_pager_id for row in result.rows]


def _row_ids(at: AppTest) -> list[str]:
    """Return the One Pager IDs in the order the table shows them."""
    if not at.dataframe:
        return []
    return list(at.dataframe[0].value["ID"])


# ============================================================================
# RegistrySort
# ============================================================================


@pytest.mark.unit
def test__no_arguments__registry_sort__id_ascending() -> None:
    """The default order is by ID, ascending."""
    # When
    sort = RegistrySort()

    # Then
    assert sort == RegistrySort("one_pager_id", descending=False)


@pytest.mark.unit
def test__unknown_column__registry_sort__raises() -> None:
    """Only the known columns can be sorted (no SQL from the page)."""
    # When / Then
    with pytest.raises(ValueError, match="Cannot sort"):
        RegistrySort("owner_email; DROP TABLE x")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sort", "column", "expected"),
    [
        (RegistrySort(), "product_name", RegistrySort("product_name")),
        (
            RegistrySort("product_name"),
            "product_name",
            RegistrySort("product_name", descending=True),
        ),
        (
            RegistrySort("product_name", descending=True),
            "owner_name",
            RegistrySort("owner_name"),
        ),
    ],
    ids=["new-column", "same-column", "other-column-after-descending"],
)
def test__current_sort__toggled__flips_same_column_resets_new_one(
    sort: RegistrySort, column: str, expected: RegistrySort
) -> None:
    """Clicking the sorted column reverses it; another column starts ascending."""
    # When
    result = sort.toggled(column)

    # Then
    assert result == expected


# ============================================================================
# MockDataAccess
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("use_case_id", "expected"),
    [("UC-002", ["OP-0001", "OP-0002"]), ("UC-003", ["OP-0002"]), ("UC-999", [])],
)
def test__use_case_filter__mock_get_registry__one_pagers_referencing_it(
    registry: MockDataAccess, use_case_id: str, expected: list[str]
) -> None:
    """The Use Case filter keeps One Pagers that reference it."""
    # When
    ids = _ids(registry, RegistryFilter(use_case_id=use_case_id))

    # Then
    assert ids == expected


@pytest.mark.unit
def test__use_case_filter__mock_status_counts__counted_with_the_filter(
    registry: MockDataAccess,
) -> None:
    """The metric cards apply the same filter."""
    # When
    counts = registry.get_registry_status_counts(RegistryFilter(use_case_id="UC-001"))

    # Then
    assert counts == {"Approved": 1}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        (RegistrySort("product_name"), ["OP-0003", "OP-0002", "OP-0001"]),
        (
            RegistrySort("product_name", descending=True),
            ["OP-0001", "OP-0002", "OP-0003"],
        ),
        (RegistrySort("one_pager_id", True), ["OP-0003", "OP-0002", "OP-0001"]),
    ],
    ids=["name-ascending", "name-descending", "id-descending"],
)
def test__sort__mock_get_registry__case_insensitive_in_both_directions(
    registry: MockDataAccess, sort: RegistrySort, expected: list[str]
) -> None:
    """Sorting ignores case: "account ..." comes before "Order ..."."""
    # When
    ids = _ids(registry, sort=sort)

    # Then
    assert ids == expected


@pytest.mark.unit
@pytest.mark.parametrize("descending", [False, True])
def test__tied_sort_values__mock_get_registry__id_order_kept(
    registry: MockDataAccess, descending: bool
) -> None:
    """OP-0002 and OP-0003 are both In Definition; they stay in ID order."""
    # When
    ids = _ids(registry, sort=RegistrySort("data_product_status", descending))

    # Then
    assert ids.index("OP-0002") < ids.index("OP-0003")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("page", "expected"), [(1, ["OP-0003", "OP-0002"]), (2, ["OP-0001"])]
)
def test__descending_sort__mock_get_registry_pages__sorted_before_paging(
    registry: MockDataAccess, page: int, expected: list[str]
) -> None:
    """The sort applies to all rows, then the page is cut."""
    # When
    ids = _ids(
        registry, sort=RegistrySort("one_pager_id", True), page=page, page_size=2
    )

    # Then
    assert ids == expected


# ============================================================================
# LakehouseAccess SQL
# ============================================================================


@pytest.mark.unit
def test__use_case_filter__lakehouse_registry__references_subquery_with_quoting(
    patched_lakehouse: LakehouseAccess, counting: SimpleNamespace
) -> None:
    """The Use Case filter becomes a quoted subquery in every statement."""
    # Given
    registry_filter = RegistryFilter(use_case_id="UC-0'1")

    # When
    patched_lakehouse.get_registry(registry_filter, 1, 20)
    patched_lakehouse.get_registry_status_counts(registry_filter)

    # Then
    assert len(counting.calls) == 3
    for statement, _ in counting.calls:
        assert "use_case_references WHERE use_case_id = 'UC-0''1'" in statement


@pytest.mark.unit
@pytest.mark.parametrize("column", REGISTRY_SORT_COLUMNS)
def test__sort_column__lakehouse_registry__ordered_by_it_then_id(
    patched_lakehouse: LakehouseAccess, counting: SimpleNamespace, column: str
) -> None:
    """Text columns sort case-insensitively, with the ID as tie-breaker."""
    # When
    patched_lakehouse.get_registry(
        RegistryFilter(), 2, 20, RegistrySort(column, descending=True)
    )

    # Then
    page_query = counting.calls[-1][0]
    if column == "one_pager_id":
        assert "ORDER BY one_pager_id DESC LIMIT 20 OFFSET 20" in page_query
    else:
        assert f"ORDER BY LOWER({column}) DESC, one_pager_id ASC LIMIT 20" in page_query


@pytest.mark.unit
def test__no_sort__lakehouse_registry__ordered_by_id(
    patched_lakehouse: LakehouseAccess, counting: SimpleNamespace
) -> None:
    """Without a sort the Registry is in ID order."""
    # When
    patched_lakehouse.get_registry(RegistryFilter(), 1, 20)

    # Then
    assert "ORDER BY one_pager_id ASC LIMIT 20 OFFSET 0" in counting.calls[-1][0]


# ============================================================================
# Registry page
# ============================================================================


@pytest.mark.unit
def test__registry__click_status_card__filtered_then_cleared(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """A status card filters the table; clicking it again shows all again."""
    # Given
    at = page_app("registry.py", registry, roles=frozenset()).run()

    # When
    at.button(key="registry-card-Draft").click().run()

    # Then
    assert at.session_state["filter_op_status"] == "Draft"
    assert at.button(key="registry-card-Draft").label == "✓ Showing"
    assert _row_ids(at) == ["OP-0003"]

    # When
    at.button(key="registry-card-Draft").click().run()

    # Then
    assert at.session_state["filter_op_status"] == "All"
    assert len(_row_ids(at)) == 3


@pytest.mark.unit
def test__nobody_ready_for_review__open_registry__no_card_for_it(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """The transient Ready for Review status has no card while it is empty."""
    # When
    at = page_app("registry.py", registry, roles=frozenset()).run()

    # Then
    keys = [str(b.key) for b in at.button]
    assert "registry-card-Ready for Review" not in keys
    assert "registry-card-Draft" in keys


@pytest.mark.unit
def test__registry__open_page__sorted_by_id_ascending(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """The default sort is ID, ascending."""
    # When
    at = page_app("registry.py", registry, roles=frozenset()).run()

    # Then
    assert at.selectbox(key="registry_sort_choice").value == RegistrySort()
    assert _row_ids(at) == ["OP-0001", "OP-0002", "OP-0003"]


@pytest.mark.unit
def test__registry__sort_by_product_both_ways__rows_reordered(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """Choosing a sort order reorders the rows on the server side."""
    # Given
    at = page_app("registry.py", registry, roles=frozenset()).run()

    # When
    at.selectbox(key="registry_sort_choice").set_value(
        RegistrySort("product_name")
    ).run()

    # Then
    assert not at.exception
    assert _row_ids(at) == ["OP-0003", "OP-0002", "OP-0001"]

    # When
    at.selectbox(key="registry_sort_choice").set_value(
        RegistrySort("product_name", descending=True)
    ).run()

    # Then
    assert _row_ids(at) == ["OP-0001", "OP-0002", "OP-0003"]


@pytest.mark.unit
def test__registry__search_box__matches_id_name_and_owner(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """One search box finds by ID, product name or owner."""
    # Given
    at = page_app("registry.py", registry, roles=frozenset()).run()

    # When / Then
    at.text_input(key="filter_search").set_value("op-0002").run()
    assert _row_ids(at) == ["OP-0002"]
    at.text_input(key="filter_search").set_value("carol").run()
    assert _row_ids(at) == ["OP-0003"]


@pytest.mark.unit
def test__owner__my_one_pagers_view__only_own_rows(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """The "My One Pagers" view lists the One Pagers the user is Owner/SME of."""
    # Given (Alice owns OP-0001)
    at = page_app("registry.py", registry).run()

    # When
    at.radio(key="filter_view").set_value("My One Pagers").run()

    # Then
    assert _row_ids(at) == ["OP-0001"]

    # When
    at.radio(key="filter_view").set_value("My drafts").run()

    # Then
    assert any("No One Pagers match your filters" in w.value for w in at.warning)


@pytest.mark.unit
def test__registry__choose_use_case_filter__only_referencing_rows(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """The Use Case filter offers "All" and the Use Cases, and filters rows."""
    # Given
    at = page_app("registry.py", registry, roles=frozenset()).run()
    use_case = at.selectbox(key="filter_use_case")
    assert use_case.options[0] == "All"
    assert any(option.startswith("UC-003 · ") for option in use_case.options)

    # When
    use_case.set_value("UC-003").run()

    # Then
    assert not at.exception
    assert _row_ids(at) == ["OP-0002"]
    assert any("More filters (1 active)" in e.label for e in at.expander)


@pytest.mark.unit
def test__use_case_filter__add_status_filter_without_match__empty_warning(
    registry: MockDataAccess, switched: list[str]
) -> None:
    """Filters combine; no match shows a warning."""
    # Given
    at = page_app("registry.py", registry, roles=frozenset()).run()
    at.selectbox(key="filter_use_case").set_value("UC-003").run()

    # When
    at.selectbox(key="filter_op_status").set_value("Approved").run()

    # Then
    assert any("No One Pagers match your filters" in w.value for w in at.warning)


@pytest.mark.unit
def test__click_on_second_row__selected_value__its_id(
    import_app_module: Callable[[str], ModuleType],
) -> None:
    """A row click (dataframe selection) resolves to that row's ID."""
    # Given
    frame = pd.DataFrame({"ID": ["OP-0001", "OP-0002"]})
    event = SimpleNamespace(selection=SimpleNamespace(rows=[1]))

    tables = import_app_module("adapters.tables")

    # Then
    assert tables.selected_value(event, frame, "ID") == "OP-0002"
    assert tables.selected_value(SimpleNamespace(), frame, "ID") is None
