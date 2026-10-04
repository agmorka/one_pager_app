"""Contract tests for the Use Case methods of MockDataAccess."""

from pathlib import Path

import pytest

from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import UseCaseFilter, UseCaseInput


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    """Return mock data with the seeded Use Cases UC-001..UC-005 (UC-005 deprecated)."""
    return MockDataAccess(OnePagerDocumentStore(tmp_path))


def _input(**overrides: str) -> UseCaseInput:
    """Return valid Use Case input with ``overrides`` applied."""
    values = {
        "persona": "Risk Analyst",
        "goal": "Assess exposure",
        "scenario": "Reviews dashboards daily",
        "decision_enabled": "Approve or block trades",
        "priority": "High",
    }
    return UseCaseInput(**{**values, **overrides})


def _ids(data_access: MockDataAccess, use_case_filter: UseCaseFilter) -> list[str]:
    """Return the IDs on the first page of 50 matching ``use_case_filter``."""
    result = data_access.get_use_cases(use_case_filter, 1, 50)
    return [uc.use_case_id for uc in result.rows]


@pytest.mark.unit
def test__deprecated_use_case__default_filter__hidden(
    data_access: MockDataAccess,
) -> None:
    """Deprecated Use Cases are hidden by default."""
    # When
    ids = _ids(data_access, UseCaseFilter())

    # Then
    assert "UC-005" not in ids


@pytest.mark.unit
def test__deprecated_use_case__include_deprecated__shown(
    data_access: MockDataAccess,
) -> None:
    """Deprecated Use Cases are shown on request."""
    # When
    ids = _ids(data_access, UseCaseFilter(include_deprecated=True))

    # Then
    assert "UC-005" in ids


@pytest.mark.unit
def test__seeded_use_cases__get_use_cases__ordered_by_id(
    data_access: MockDataAccess,
) -> None:
    """Rows are ordered by ID."""
    # When
    ids = _ids(data_access, UseCaseFilter(include_deprecated=True))

    # Then
    assert ids == sorted(ids)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("search", "expected"), [("COMPLIANCE", ["UC-002"]), ("revenue", ["UC-003"])]
)
def test__search_text__get_use_cases__matches_persona_or_goal_ignoring_case(
    data_access: MockDataAccess, search: str, expected: list[str]
) -> None:
    """The search matches persona or goal, ignoring case."""
    # When
    ids = _ids(data_access, UseCaseFilter(search=search))

    # Then
    assert ids == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("use_case_filter", "expected"),
    [
        (UseCaseFilter(priority="Must Have"), ["UC-001", "UC-002"]),
        (UseCaseFilter(priority="Must Have", search="finance"), []),
    ],
)
def test__priority_and_search__get_use_cases__filters_combine_with_and(
    data_access: MockDataAccess, use_case_filter: UseCaseFilter, expected: list[str]
) -> None:
    """All filters must match."""
    # When
    ids = _ids(data_access, use_case_filter)

    # Then
    assert ids == expected


@pytest.mark.unit
def test__page_size_3__get_two_pages__split_at_the_boundary(
    data_access: MockDataAccess,
) -> None:
    """Pages split the active Use Cases; only the first has a next page."""
    # When
    first = data_access.get_use_cases(UseCaseFilter(), page=1, page_size=3)
    second = data_access.get_use_cases(UseCaseFilter(), page=2, page_size=3)

    # Then
    assert first.total_rows == second.total_rows == 4
    assert [uc.use_case_id for uc in first.rows] == ["UC-001", "UC-002", "UC-003"]
    assert [uc.use_case_id for uc in second.rows] == ["UC-004"]
    assert first.has_next
    assert not second.has_next


@pytest.mark.unit
def test__seeded_references__get_use_cases__reference_count_matches(
    data_access: MockDataAccess,
) -> None:
    """Each row's reference count equals its references."""
    # When
    rows = data_access.get_use_cases(UseCaseFilter(include_deprecated=True), 1, 50).rows

    # Then
    for use_case in rows:
        references = data_access.get_use_case_references(use_case.use_case_id)
        assert use_case.reference_count == len(references)
    assert data_access.get_use_case_references("UC-002") == ["OP-0001", "OP-0002"]


@pytest.mark.unit
def test__two_inputs__create_use_case__next_ids_and_persisted(
    data_access: MockDataAccess,
) -> None:
    """Creates allocate consecutive IDs and store the actor."""
    # When
    first = data_access.create_use_case(_input(), "MJO")
    second = data_access.create_use_case(_input(persona="Other"), "MJO")

    # Then
    assert (first, second) == ("UC-006", "UC-007")
    created = data_access.get_use_case(first)
    assert created.persona == "Risk Analyst"
    assert not created.deprecated
    assert created.created_by == created.last_updated_by == "MJO"
    assert created.reference_count == 0


@pytest.mark.unit
def test__new_goal__update_use_case__fields_and_audit_columns_changed(
    data_access: MockDataAccess,
) -> None:
    """An update changes the fields and last-updated columns only."""
    # Given
    before = data_access.get_use_case("UC-003")

    # When
    data_access.update_use_case("UC-003", _input(goal="New goal"), "XY")

    # Then
    after = data_access.get_use_case("UC-003")
    assert (after.goal, after.priority, after.last_updated_by) == (
        "New goal",
        "High",
        "XY",
    )
    assert after.last_updated_at > before.last_updated_at
    assert after.created_by == before.created_by
    assert after.reference_count == before.reference_count


@pytest.mark.unit
def test__referenced_use_case__deprecate__hidden_but_references_kept(
    data_access: MockDataAccess,
) -> None:
    """Deprecating hides the Use Case and keeps its references."""
    # When
    data_access.set_use_case_deprecated("UC-001", True, "XY")

    # Then
    assert data_access.get_use_case("UC-001").deprecated
    assert "UC-001" not in _ids(data_access, UseCaseFilter())
    assert data_access.get_use_case_references("UC-001") == ["OP-0001"]


@pytest.mark.unit
def test__deprecated_use_case__restore__active_with_new_actor(
    data_access: MockDataAccess,
) -> None:
    """Restoring makes the Use Case active again."""
    # Given
    data_access.set_use_case_deprecated("UC-001", True, "XY")

    # When
    data_access.set_use_case_deprecated("UC-001", False, "ZZ")

    # Then
    restored = data_access.get_use_case("UC-001")
    assert not restored.deprecated
    assert restored.last_updated_by == "ZZ"


@pytest.mark.unit
def test__unknown_id__read__none_and_no_references(data_access: MockDataAccess) -> None:
    """An unknown Use Case reads as None with no references."""
    # When
    use_case = data_access.get_use_case("UC-999")
    references = data_access.get_use_case_references("UC-999")

    # Then
    assert use_case is None
    assert references == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "write",
    [
        lambda da: da.update_use_case("UC-999", _input(), "XY"),
        lambda da: da.set_use_case_deprecated("UC-999", True, "XY"),
    ],
    ids=["update", "deprecate"],
)
def test__unknown_id__write__raises_not_found(
    data_access: MockDataAccess, write: object
) -> None:
    """Writing an unknown Use Case raises NotFoundError."""
    # When / Then
    with pytest.raises(NotFoundError):
        write(data_access)  # type: ignore[operator]


@pytest.mark.unit
def test__returned_use_case__changed_by_caller__stored_copy_unchanged(
    data_access: MockDataAccess,
) -> None:
    """Returned objects are copies of the stored state."""
    # Given
    use_case = data_access.get_use_case("UC-001")

    # When
    use_case.persona = "changed"

    # Then
    assert data_access.get_use_case("UC-001").persona == "Analytics Manager"
