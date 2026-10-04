"""Unit tests for Use Case business rules, models and permission helpers."""

import json

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
from tests.helpers import CREATOR_ROLES, SCHEMAS_DIR


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


def _schema(name: str) -> dict:
    """Return a One Pager JSON schema from schemas/."""
    return json.loads((SCHEMAS_DIR / name).read_text("utf-8"))


@pytest.mark.unit
def test__valid_input__validate__no_errors() -> None:
    """Valid input passes."""
    # When
    errors = validate_use_case_input(clean_use_case_input(_input()))

    # Then
    assert errors == {}


@pytest.mark.unit
@pytest.mark.parametrize("field", list(USE_CASE_FIELDS))
def test__blank_text_field__validate__field_required(field: str) -> None:
    """Every text field is required."""
    # Given
    data = clean_use_case_input(_input(**{field: "   "}))

    # When
    errors = validate_use_case_input(data)

    # Then
    assert set(errors) == {field}
    assert "required" in errors[field]


@pytest.mark.unit
@pytest.mark.parametrize("field", list(USE_CASE_FIELDS))
def test__text_one_over_max_length__validate__field_error(field: str) -> None:
    """Text one character over the limit is refused."""
    # Given
    max_length = USE_CASE_FIELDS[field][1]

    # When
    errors = validate_use_case_input(_input(**{field: "x" * (max_length + 1)}))

    # Then
    assert set(errors) == {field}


@pytest.mark.unit
@pytest.mark.parametrize("field", list(USE_CASE_FIELDS))
def test__text_at_max_length__validate__no_errors(field: str) -> None:
    """Text of exactly the maximum length is accepted."""
    # Given
    max_length = USE_CASE_FIELDS[field][1]

    # When
    errors = validate_use_case_input(_input(**{field: "x" * max_length}))

    # Then
    assert errors == {}


@pytest.mark.unit
def test__priority_not_in_schema__validate__priority_error() -> None:
    """The priority must be one of the schema values."""
    # When
    errors = validate_use_case_input(clean_use_case_input(_input(priority="Urgent")))

    # Then
    assert set(errors) == {"priority"}


@pytest.mark.unit
def test__json_schemas__priority_enums__match_priority_options() -> None:
    """The app's priority options are the schemas' enum values (v1 and v2)."""
    # When
    v1 = _schema("structure_one_pager_v_1.json")
    v2 = _schema("structure_one_pager_v_2.json")

    # Then
    v1_priority = v1["properties"]["useCases"]["items"]["properties"]["priority"]
    assert tuple(v1_priority["enum"]) == PRIORITY_OPTIONS
    assert tuple(v2["definitions"]["priority"]["enum"]) == PRIORITY_OPTIONS


@pytest.mark.unit
def test__html_and_spaces__clean__stripped() -> None:
    """Tags and surrounding whitespace are removed."""
    # When
    cleaned = clean_use_case_input(
        _input(persona="  <b>Risk</b> Analyst<script>alert(1)</script> ")
    )

    # Then
    assert cleaned.persona == "Risk Analystalert(1)"


@pytest.mark.unit
def test__sql_metacharacters__clean__kept_verbatim() -> None:
    """Quotes and SQL are data, not something to strip."""
    # Given
    text = "O'Brien; DROP TABLE use_cases; -- 100%"

    # When
    cleaned = clean_use_case_input(_input(goal=text))

    # Then
    assert cleaned.goal == text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"), [(1, "UC-001"), (42, "UC-042"), (999, "UC-999")]
)
def test__sequence_value__format_use_case_id__zero_padded(
    value: int, expected: str
) -> None:
    """IDs have three digits."""
    # When
    use_case_id = format_use_case_id(value)

    # Then
    assert use_case_id == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1, 1000])
def test__value_out_of_range__format_use_case_id__raises(value: int) -> None:
    """Only 1..999 fit the UC-### format."""
    # When / Then
    with pytest.raises(ValueError, match="UC-###"):
        format_use_case_id(value)


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
def test__rows_and_page__use_case_page__navigation_properties(
    total_rows: int,
    page: int,
    total_pages: int,
    has_prev: bool,
    has_next: bool,
    offset: int,
) -> None:
    """Page count, previous/next and offset follow from the totals."""
    # When
    use_case_page = UseCasePage(rows=[], total_rows=total_rows, page=page, page_size=20)

    # Then
    assert use_case_page.total_pages == total_pages
    assert use_case_page.has_previous is has_prev
    assert use_case_page.has_next is has_next
    assert use_case_page.offset == offset


@pytest.mark.unit
@pytest.mark.parametrize(
    ("initials", "roles", "allowed"),
    [
        ("MJO", CREATOR_ROLES, True),
        ("MJO", frozenset(), False),  # Viewer
        ("MJO", frozenset({Actor.APPROVER, Actor.ADMIN}), False),
        (None, CREATOR_ROLES, False),  # not recognised
        ("", CREATOR_ROLES, False),
    ],
)
def test__user_and_roles__can_manage_use_cases__owner_sme_group_only(
    initials: str | None, roles: frozenset[Actor], allowed: bool
) -> None:
    """Managing Use Cases needs initials and the Owner/SME group."""
    # When
    result = can_manage_use_cases(initials, roles)

    # Then
    assert result is allowed
