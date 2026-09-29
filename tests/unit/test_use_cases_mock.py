"""Contract tests for the Use Case methods of MockDataAccess."""

from pathlib import Path

import pytest

from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import UseCaseFilter, UseCaseInput


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(tmp_path))


def _input(**overrides: str) -> UseCaseInput:
    values = {
        "persona": "Risk Analyst",
        "goal": "Assess exposure",
        "scenario": "Reviews dashboards daily",
        "decision_enabled": "Approve or block trades",
        "priority": "High",
    }
    values.update(overrides)
    return UseCaseInput(**values)


def _ids(
    data_access: MockDataAccess,
    use_case_filter: UseCaseFilter,
    page: int = 1,
    page_size: int = 50,
) -> list[str]:
    result = data_access.get_use_cases(use_case_filter, page, page_size)
    return [uc.use_case_id for uc in result.rows]


@pytest.mark.unit
def test_deprecated_hidden_by_default_and_shown_on_request(
    data_access: MockDataAccess,
) -> None:
    assert "UC-005" not in _ids(data_access, UseCaseFilter())
    assert "UC-005" in _ids(data_access, UseCaseFilter(include_deprecated=True))


@pytest.mark.unit
def test_rows_are_ordered_by_id(data_access: MockDataAccess) -> None:
    ids = _ids(data_access, UseCaseFilter(include_deprecated=True))
    assert ids == sorted(ids)


@pytest.mark.unit
def test_search_matches_persona_or_goal_case_insensitively(
    data_access: MockDataAccess,
) -> None:
    assert _ids(data_access, UseCaseFilter(search="COMPLIANCE")) == ["UC-002"]
    assert _ids(data_access, UseCaseFilter(search="revenue")) == ["UC-003"]


@pytest.mark.unit
def test_filters_combine_with_and(data_access: MockDataAccess) -> None:
    assert _ids(data_access, UseCaseFilter(priority="Must Have")) == [
        "UC-001",
        "UC-002",
    ]
    assert (
        _ids(data_access, UseCaseFilter(priority="Must Have", search="finance")) == []
    )


@pytest.mark.unit
def test_pagination_boundaries(data_access: MockDataAccess) -> None:
    first = data_access.get_use_cases(UseCaseFilter(), page=1, page_size=3)
    second = data_access.get_use_cases(UseCaseFilter(), page=2, page_size=3)
    assert first.total_rows == second.total_rows == 4
    assert [uc.use_case_id for uc in first.rows] == ["UC-001", "UC-002", "UC-003"]
    assert [uc.use_case_id for uc in second.rows] == ["UC-004"]
    assert first.has_next
    assert not second.has_next


@pytest.mark.unit
def test_reference_count_matches_references(data_access: MockDataAccess) -> None:
    for use_case in data_access.get_use_cases(
        UseCaseFilter(include_deprecated=True), 1, 50
    ).rows:
        references = data_access.get_use_case_references(use_case.use_case_id)
        assert use_case.reference_count == len(references)
    assert data_access.get_use_case_references("UC-002") == ["OP-0001", "OP-0002"]


@pytest.mark.unit
def test_create_allocates_next_id_and_persists(data_access: MockDataAccess) -> None:
    first = data_access.create_use_case(_input(), "MJO")
    second = data_access.create_use_case(_input(persona="Other"), "MJO")
    assert (first, second) == ("UC-006", "UC-007")

    created = data_access.get_use_case(first)
    assert created is not None
    assert created.persona == "Risk Analyst"
    assert not created.deprecated
    assert created.created_by == created.last_updated_by == "MJO"
    assert created.reference_count == 0


@pytest.mark.unit
def test_update_changes_fields_and_audit_columns(data_access: MockDataAccess) -> None:
    before = data_access.get_use_case("UC-003")
    data_access.update_use_case("UC-003", _input(goal="New goal"), "XY")
    after = data_access.get_use_case("UC-003")
    assert after.goal == "New goal"
    assert after.priority == "High"
    assert after.last_updated_by == "XY"
    assert after.last_updated_at > before.last_updated_at
    assert after.created_by == before.created_by
    assert after.reference_count == before.reference_count


@pytest.mark.unit
def test_deprecate_and_restore(data_access: MockDataAccess) -> None:
    data_access.set_use_case_deprecated("UC-001", True, "XY")
    assert data_access.get_use_case("UC-001").deprecated
    assert "UC-001" not in _ids(data_access, UseCaseFilter())
    # Deprecation keeps existing references
    assert data_access.get_use_case_references("UC-001") == ["OP-0001"]

    data_access.set_use_case_deprecated("UC-001", False, "ZZ")
    restored = data_access.get_use_case("UC-001")
    assert not restored.deprecated
    assert restored.last_updated_by == "ZZ"


@pytest.mark.unit
def test_unknown_id(data_access: MockDataAccess) -> None:
    assert data_access.get_use_case("UC-999") is None
    assert data_access.get_use_case_references("UC-999") == []
    with pytest.raises(NotFoundError):
        data_access.update_use_case("UC-999", _input(), "XY")
    with pytest.raises(NotFoundError):
        data_access.set_use_case_deprecated("UC-999", True, "XY")


@pytest.mark.unit
def test_returned_objects_do_not_leak_internal_state(
    data_access: MockDataAccess,
) -> None:
    use_case = data_access.get_use_case("UC-001")
    use_case.persona = "changed"
    assert data_access.get_use_case("UC-001").persona == "Analytics Manager"
