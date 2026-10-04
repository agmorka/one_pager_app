"""Create-form validation, normalization and sanitizing."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from onepagerapp.config import AppConfig
from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    PersonRef,
    ValidationError,
)
from onepagerapp.validation import (
    INITIALS_RULE,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    initials_pattern,
    load_schema,
    normalize_new_one_pager,
    sanitize_text,
    set_initials_pattern,
    validate_create,
    validate_schema,
)
from tests.helpers import DOMAINS, TYPES, make_user


def _validate(data: NewOnePagerInput, user: CurrentUser) -> list[ValidationError]:
    """Normalize and validate create-form input with the usual reference values."""
    return validate_create(normalize_new_one_pager(data), user, DOMAINS, TYPES)


def _paths(errors: list[ValidationError]) -> set[str]:
    """Return the field paths of ``errors``."""
    return {e.field_path for e in errors}


def _with_sme(
    data: NewOnePagerInput, name: str, initials: str, email: str
) -> NewOnePagerInput:
    """Return ``data`` with one SME."""
    return replace(data, smes=[PersonRef(name=name, initials=initials, email=email)])


@pytest.fixture
def four_character_initials() -> Iterator[None]:
    """Allow initials of 3 or 4 characters for the test, then restore."""
    default = initials_pattern()
    set_initials_pattern(
        AppConfig(
            ONE_PAGER_APP_VOLUME_PATH="/Volumes/x",
            ONE_PAGER_APP_INITIALS_PATTERN=r"^[A-Z0-9]{3,4}$",
        ).initials_pattern
    )
    yield
    set_initials_pattern(default)


@pytest.mark.unit
def test__valid_input__validate_create__no_errors(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """The sample input is valid."""
    # When
    errors = _validate(valid_input, creator)

    # Then
    assert errors == []


@pytest.mark.unit
def test__no_sme_and_no_problem_statement__validate_create__no_errors(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """SMEs and the problem statement are optional at create."""
    # Given
    data = replace(valid_input, smes=[], business_problem_statement="")

    # When
    errors = _validate(data, creator)

    # Then
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"data_product": ""}, "dataProduct"),
        ({"product_name": ""}, "productName"),
        ({"description": "  "}, "description"),
        ({"business_domain": ""}, "businessDomain"),
        ({"data_product_type": ""}, "dataProductType"),
    ],
)
def test__missing_required_field__validate_create__error_on_that_field(
    valid_input: NewOnePagerInput, creator: CurrentUser, change: dict, path: str
) -> None:
    """Each required basic field is reported on its own path."""
    # When
    errors = _validate(replace(valid_input, **change), creator)

    # Then
    assert _paths(errors) == {path}


@pytest.mark.unit
@pytest.mark.parametrize("field", ["name", "initials", "email"])
def test__missing_owner_field__validate_create__owner_field_error(
    valid_input: NewOnePagerInput, creator: CurrentUser, field: str
) -> None:
    """Each owner field is required."""
    # Given the creator stays an SME, so only the owner error remains
    owner = replace(valid_input.owner, **{field: ""})
    data = _with_sme(replace(valid_input, owner=owner), "Maja", "MJO", "m@bec.dk")

    # When
    errors = _validate(data, creator)

    # Then
    assert f"dataProductOwner.{field}" in _paths(errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "data_product",
    ["Customer_Master", "1customer", "customer-master", "c", "a" * 64, "cust master"],
)
def test__data_product_not_snake_case__validate_create__data_product_error(
    valid_input: NewOnePagerInput, creator: CurrentUser, data_product: str
) -> None:
    """The data product is lower snake case of 2 to 63 characters."""
    # When
    errors = _validate(replace(valid_input, data_product=data_product), creator)

    # Then
    assert _paths(errors) == {"dataProduct"}


@pytest.mark.unit
def test__inactive_reference_values__validate_create__both_rejected(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """Domain and type must be active reference values."""
    # Given
    data = replace(valid_input, business_domain="Unknown", data_product_type="Other")

    # When
    errors = _validate(data, creator)

    # Then
    assert _paths(errors) == {"businessDomain", "dataProductType"}


@pytest.mark.unit
def test__invalid_email_and_initials__validate_create__both_reported(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """A malformed email and initials are reported on their paths."""
    # Given
    owner = replace(valid_input.owner, email="not-an-email")
    data = _with_sme(replace(valid_input, owner=owner), "X", "X1", "x@bec.dk")

    # When
    errors = _validate(data, creator)

    # Then
    assert _paths(errors) == {"dataProductOwner.email", "smes[0].initials"}


@pytest.mark.unit
@pytest.mark.parametrize("initials", ["X0W", "x0w", " x0w ", "AB1", "123"])
def test__corporate_initials__validate_create__accepted_and_upper_cased(
    valid_input: NewOnePagerInput, creator: CurrentUser, initials: str
) -> None:
    """Three letters or digits are accepted, in any case."""
    # Given
    data = normalize_new_one_pager(_with_sme(valid_input, "X", initials, "x@bec.dk"))

    # When
    errors = validate_create(data, creator, DOMAINS, TYPES)

    # Then
    assert errors == []
    assert data.smes[0].initials == initials.strip().upper()


@pytest.mark.unit
@pytest.mark.parametrize("initials", ["X", "X0", "X0WA", "X0W!", "X-W", "ÆØÅ"])
def test__invalid_initials__validate_create__initials_rule_error(
    valid_input: NewOnePagerInput, creator: CurrentUser, initials: str
) -> None:
    """Other initials get the initials rule as message."""
    # When
    errors = _validate(_with_sme(valid_input, "X", initials, "x@bec.dk"), creator)

    # Then
    assert [(e.field_path, e.message) for e in errors] == [
        ("smes[0].initials", INITIALS_RULE)
    ]


@pytest.mark.unit
def test__configured_initials_pattern__validate_create__four_characters_accepted(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    four_character_initials: None,
) -> None:
    """The initials pattern is configurable."""
    # When
    errors = _validate(_with_sme(valid_input, "X", "X0WA", "x@bec.dk"), creator)

    # Then
    assert errors == []


@pytest.mark.unit
def test__sme_with_only_a_name__validate_create__initials_and_email_errors(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """A started SME row needs initials and an email."""
    # When
    errors = _validate(_with_sme(valid_input, "Only Name", "", ""), creator)

    # Then
    assert _paths(errors) == {"smes[0].initials", "smes[0].email"}


@pytest.mark.unit
def test__blank_sme_row__normalize__dropped(valid_input: NewOnePagerInput) -> None:
    """An empty SME row is not an SME."""
    # Given
    data = replace(valid_input, smes=[PersonRef(name="", initials="", email="")])

    # When
    normalized = normalize_new_one_pager(data)

    # Then
    assert normalized.smes == []


@pytest.mark.unit
def test__owner_listed_as_sme__validate_create__sme_initials_error(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """The Owner cannot also be an SME (initials compared ignoring case)."""
    # When
    errors = _validate(_with_sme(valid_input, "Maja", "mjo", "m@bec.dk"), creator)

    # Then
    assert _paths(errors) == {"smes[0].initials"}


@pytest.mark.unit
def test__same_sme_twice__validate_create__second_row_error(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """Duplicate SMEs are reported on the later row."""
    # Given
    sme = PersonRef(name="Dee", initials="DPR", email="d@bec.dk")

    # When
    errors = _validate(replace(valid_input, smes=[sme, sme]), creator)

    # Then
    assert _paths(errors) == {"smes[1].initials"}


@pytest.mark.unit
def test__creator_neither_owner_nor_sme__validate_create__smes_error(
    valid_input: NewOnePagerInput,
) -> None:
    """The creator must be the Owner or an SME."""
    # When
    errors = _validate(valid_input, make_user("ZZZ", "Z Z"))

    # Then
    assert _paths(errors) == {"smes"}


@pytest.mark.unit
def test__creator_listed_as_sme__validate_create__no_errors(
    valid_input: NewOnePagerInput,
) -> None:
    """Being an SME is enough for the creator."""
    # Given
    data = _with_sme(valid_input, "Z Z", "ZZZ", "zz@bec.dk")

    # When
    errors = _validate(data, make_user("ZZZ", "Z Z"))

    # Then
    assert errors == []


@pytest.mark.unit
def test__name_and_description_too_long__validate_create__both_reported(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    """Names and texts have maximum lengths."""
    # Given
    data = replace(
        valid_input,
        product_name="x" * (MAX_NAME_LENGTH + 1),
        description="x" * (MAX_TEXT_LENGTH + 1),
    )

    # When
    errors = _validate(data, creator)

    # Then
    assert _paths(errors) == {"productName", "description"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  <script>alert(1)</script>Hello <b>World</b>  ", "alert(1)Hello World"),
        (None, ""),
    ],
)
def test__text__sanitize_text__tags_and_whitespace_removed(
    text: str | None, expected: str
) -> None:
    """Tags are stripped (their text kept) and None becomes empty."""
    # When
    result = sanitize_text(text)

    # Then
    assert result == expected


@pytest.mark.unit
def test__html_and_lower_case_initials__normalize__cleaned(
    valid_input: NewOnePagerInput,
) -> None:
    """Normalizing strips HTML and upper-cases initials."""
    # Given
    data = replace(
        valid_input,
        product_name="<i>Customer</i> Master",
        owner=replace(valid_input.owner, initials=" mjo "),
    )

    # When
    normalized = normalize_new_one_pager(data)

    # Then
    assert normalized.product_name == "Customer Master"
    assert normalized.owner.initials == "MJO"


@pytest.mark.unit
def test__document_without_data_product__validate_schema__required_error() -> None:
    """The current schema (v2) requires the data product."""
    # When
    errors = validate_schema({"productName": "x"})

    # Then
    assert load_schema()["title"] == "One Pager Structure Definition v2"
    assert ValidationError("dataProduct", "This field is required.") in errors
