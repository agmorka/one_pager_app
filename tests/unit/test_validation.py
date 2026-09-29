from dataclasses import replace

import pytest

from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef
from onepagerapp.validation import (
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    load_schema,
    normalize_new_one_pager,
    sanitize_text,
    validate_create,
    validate_schema,
)

DOMAINS = ["Customer", "Sales"]
TYPES = ["Foundational", "Integrated", "Augmented"]


def _validate(data: NewOnePagerInput, user: CurrentUser) -> list:
    return validate_create(normalize_new_one_pager(data), user, DOMAINS, TYPES)


def _paths(errors: list) -> set[str]:
    return {e.field_path for e in errors}


@pytest.mark.unit
def test__validate_create__valid_input_passes(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    assert _validate(valid_input, creator) == []


@pytest.mark.unit
def test__validate_create__sme_and_problem_are_optional(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    data = replace(valid_input, smes=[], business_problem_statement="")
    assert _validate(data, creator) == []


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
def test__validate_create__missing_required_field(
    valid_input: NewOnePagerInput, creator: CurrentUser, change: dict, path: str
) -> None:
    errors = _validate(replace(valid_input, **change), creator)
    assert _paths(errors) == {path}


@pytest.mark.unit
@pytest.mark.parametrize("field", ["name", "initials", "email"])
def test__validate_create__owner_field_required(
    valid_input: NewOnePagerInput, creator: CurrentUser, field: str
) -> None:
    owner = replace(valid_input.owner, **{field: ""})
    # Keep the creator as SME so only the owner error remains.
    smes = [PersonRef(name="Maja", initials="MJO", email="m@bec.dk")]
    if field == "initials":
        errors = _validate(replace(valid_input, owner=owner, smes=smes), creator)
    else:
        errors = _validate(replace(valid_input, owner=owner), creator)
    assert f"dataProductOwner.{field}" in _paths(errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "data_product",
    ["Customer_Master", "1customer", "customer-master", "c", "a" * 64, "cust master"],
)
def test__validate_create__data_product_pattern(
    valid_input: NewOnePagerInput, creator: CurrentUser, data_product: str
) -> None:
    errors = _validate(replace(valid_input, data_product=data_product), creator)
    assert _paths(errors) == {"dataProduct"}


@pytest.mark.unit
def test__validate_create__inactive_reference_values_rejected(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    errors = _validate(
        replace(valid_input, business_domain="Unknown", data_product_type="Other"),
        creator,
    )
    assert _paths(errors) == {"businessDomain", "dataProductType"}


@pytest.mark.unit
def test__validate_create__invalid_email_and_initials(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    owner = replace(valid_input.owner, email="not-an-email")
    smes = [PersonRef(name="X", initials="X1", email="x@bec.dk")]
    errors = _validate(replace(valid_input, owner=owner, smes=smes), creator)
    assert _paths(errors) == {"dataProductOwner.email", "smes[0].initials"}


@pytest.mark.unit
def test__validate_create__sme_row_missing_fields(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    smes = [PersonRef(name="Only Name", initials="", email="")]
    errors = _validate(replace(valid_input, smes=smes), creator)
    assert _paths(errors) == {"smes[0].initials", "smes[0].email"}


@pytest.mark.unit
def test__validate_create__blank_sme_rows_are_dropped(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    smes = [PersonRef(name="", initials="", email="", team=None)]
    normalized = normalize_new_one_pager(replace(valid_input, smes=smes))
    assert normalized.smes == []


@pytest.mark.unit
def test__validate_create__owner_listed_as_sme(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    smes = [PersonRef(name="Maja", initials="mjo", email="m@bec.dk")]
    errors = _validate(replace(valid_input, smes=smes), creator)
    assert _paths(errors) == {"smes[0].initials"}


@pytest.mark.unit
def test__validate_create__duplicate_sme(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    sme = PersonRef(name="Dee", initials="DPR", email="d@bec.dk")
    errors = _validate(replace(valid_input, smes=[sme, sme]), creator)
    assert _paths(errors) == {"smes[1].initials"}


@pytest.mark.unit
def test__validate_create__creator_must_be_owner_or_sme(
    valid_input: NewOnePagerInput,
) -> None:
    outsider = CurrentUser(username="zz@bec.dk", initials="ZZ", display_name="Z Z")
    errors = _validate(valid_input, outsider)
    assert _paths(errors) == {"smes"}

    as_sme = replace(
        valid_input,
        smes=[PersonRef(name="Z Z", initials="ZZ", email="zz@bec.dk")],
    )
    assert _validate(as_sme, outsider) == []


@pytest.mark.unit
def test__validate_create__length_limits(
    valid_input: NewOnePagerInput, creator: CurrentUser
) -> None:
    data = replace(
        valid_input,
        product_name="x" * (MAX_NAME_LENGTH + 1),
        description="x" * (MAX_TEXT_LENGTH + 1),
    )
    errors = _validate(data, creator)
    assert _paths(errors) == {"productName", "description"}


@pytest.mark.unit
def test__sanitize_text__strips_html_and_whitespace() -> None:
    assert sanitize_text("  <script>alert(1)</script>Hello <b>World</b>  ") == (
        "alert(1)Hello World"
    )
    assert sanitize_text(None) == ""


@pytest.mark.unit
def test__normalize__uppercases_initials_and_strips_html(
    valid_input: NewOnePagerInput,
) -> None:
    data = replace(
        valid_input,
        product_name="<i>Customer</i> Master",
        owner=replace(valid_input.owner, initials=" mjo "),
    )
    normalized = normalize_new_one_pager(data)
    assert normalized.product_name == "Customer Master"
    assert normalized.owner.initials == "MJO"


@pytest.mark.unit
def test__validate_schema__required_fields_enforced() -> None:
    assert load_schema()["title"] == "One Pager Structure Definition v1"
    errors = validate_schema({"productName": "x"})
    assert errors
    assert any("dataProduct" in e.message for e in errors)
