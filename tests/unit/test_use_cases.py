"""Unit tests for Use Case business rules, models and permission helpers."""

import json
from pathlib import Path

import pytest

from onepagerapp.models import PRIORITY_OPTIONS, UseCaseInput, UseCasePage
from onepagerapp.permissions import can_manage_use_cases
from onepagerapp.state_machine import Actor
from onepagerapp.use_cases import (
    USE_CASE_FIELDS,
    clean_use_case_input,
    format_use_case_id,
    validate_use_case_input,
)
from tests.users import CREATOR_ROLES


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


@pytest.mark.unit
def test_valid_input_has_no_errors() -> None:
    assert validate_use_case_input(clean_use_case_input(_input())) == {}


@pytest.mark.unit
@pytest.mark.parametrize("field", list(USE_CASE_FIELDS))
def test_each_text_field_is_required(field: str) -> None:
    errors = validate_use_case_input(clean_use_case_input(_input(**{field: "   "})))
    assert set(errors) == {field}
    assert "required" in errors[field]


@pytest.mark.unit
@pytest.mark.parametrize("field", list(USE_CASE_FIELDS))
def test_each_text_field_has_a_max_length(field: str) -> None:
    max_length = USE_CASE_FIELDS[field][1]
    errors = validate_use_case_input(_input(**{field: "x" * (max_length + 1)}))
    assert set(errors) == {field}
    assert validate_use_case_input(_input(**{field: "x" * max_length})) == {}


@pytest.mark.unit
def test_priority_must_be_a_schema_value() -> None:
    errors = validate_use_case_input(clean_use_case_input(_input(priority="Urgent")))
    assert set(errors) == {"priority"}


@pytest.mark.unit
def test_priority_options_match_json_schema() -> None:
    schemas = Path(__file__).parents[2] / "schemas"
    v1 = json.loads((schemas / "structure_one_pager_v_1.json").read_text("utf-8"))
    v2 = json.loads((schemas / "structure_one_pager_v_2.json").read_text("utf-8"))
    v1_priority = v1["properties"]["useCases"]["items"]["properties"]["priority"]
    assert tuple(v1_priority["enum"]) == PRIORITY_OPTIONS
    assert tuple(v2["definitions"]["priority"]["enum"]) == PRIORITY_OPTIONS


@pytest.mark.unit
def test_clean_strips_html_tags_and_whitespace() -> None:
    cleaned = clean_use_case_input(
        _input(persona="  <b>Risk</b> Analyst<script>alert(1)</script> ")
    )
    assert cleaned.persona == "Risk Analystalert(1)"


@pytest.mark.unit
def test_clean_keeps_sql_metacharacters_verbatim() -> None:
    text = "O'Brien; DROP TABLE use_cases; -- 100%"
    assert clean_use_case_input(_input(goal=text)).goal == text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"), [(1, "UC-001"), (42, "UC-042"), (999, "UC-999")]
)
def test_format_use_case_id(value: int, expected: str) -> None:
    assert format_use_case_id(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1, 1000])
def test_format_use_case_id_rejects_out_of_range(value: int) -> None:
    with pytest.raises(ValueError, match="UC-###"):
        format_use_case_id(value)


@pytest.mark.unit
def test_can_manage_use_cases_requires_the_owner_sme_group() -> None:
    assert can_manage_use_cases("MJO", CREATOR_ROLES)
    assert not can_manage_use_cases("MJO", frozenset())  # Viewer
    assert not can_manage_use_cases("MJO", {Actor.APPROVER, Actor.ADMIN})
    assert not can_manage_use_cases(None, CREATOR_ROLES)  # not recognised
    assert not can_manage_use_cases("", CREATOR_ROLES)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("total_rows", "page", "total_pages", "has_prev", "has_next", "offset"),
    [
        (0, 1, 0, False, False, 0),
        (20, 1, 1, False, False, 0),
        (21, 1, 2, False, True, 0),
        (21, 2, 2, True, False, 20),
    ],
)
def test_use_case_page_properties(
    total_rows: int,
    page: int,
    total_pages: int,
    has_prev: bool,
    has_next: bool,
    offset: int,
) -> None:
    use_case_page = UseCasePage(rows=[], total_rows=total_rows, page=page, page_size=20)
    assert use_case_page.total_pages == total_pages
    assert use_case_page.has_previous is has_prev
    assert use_case_page.has_next is has_next
    assert use_case_page.offset == offset
