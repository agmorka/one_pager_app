"""Create tier and the Basics checks of every save (New_One_Pager_Plan D2).

Works on the Editor's form input (create) or the edited document (save):
Data Product, names, Business Domain, Product Type and the Owner/SMEs.
"""

from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
    PersonRef,
    ValidationError,
)
from onepagerapp.validation.rules import (
    BUSINESS_PROBLEM_REQUIRED_MESSAGE,
    DATA_PRODUCT_PATTERN,
    DATA_PRODUCT_RULE,
    DATA_PRODUCT_TYPES,
    EMAIL_PATTERN,
    INITIALS_RULE,
    MAX_EMAIL_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
    check_length,
    initials_pattern,
)
from onepagerapp.validation.sanitize import person_from_dict


def _validate_domain_and_type(
    errors: list[ValidationError],
    business_domain: str,
    data_product_type: str,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> None:
    """Check the Business Domain and Product Type: set, and an allowed value."""
    if not business_domain:
        errors.append(ValidationError("businessDomain", "Business Domain is required."))
    elif business_domain not in allowed_domains:
        errors.append(
            ValidationError("businessDomain", "Select an active Business Domain.")
        )
    if not data_product_type:
        errors.append(ValidationError("dataProductType", "Product Type is required."))
    elif (
        data_product_type not in allowed_types
        or data_product_type not in DATA_PRODUCT_TYPES
    ):
        errors.append(
            ValidationError("dataProductType", "Select an active Product Type.")
        )


def validate_edit_basics(
    document: OnePagerDocument,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> list[ValidationError]:
    """Check a (normalized) edited document beyond the lenient schema tier.

    ``one_pager_status`` and ``one_pager_authorized_users`` need a Business
    Domain, a Product Type, and a complete Owner / SME list on every save, so
    these are checked on Save Draft too. A value that was valid when stored
    stays allowed even if the reference value has since been deactivated.
    """
    errors: list[ValidationError] = []
    _validate_domain_and_type(
        errors,
        document.business_domain,
        document.data_product_type,
        allowed_domains,
        allowed_types,
    )
    owner = PersonRef(
        name=document.owner_name,
        initials=document.owner_initials,
        email=document.owner_email,
        team=document.owner_team,
    )
    smes = [person_from_dict(s) for s in document.smes]
    errors.extend(validate_owner_and_smes(owner, smes))
    return errors


def _validate_person(
    errors: list[ValidationError], path: str, person: PersonRef
) -> None:
    if not person.name:
        errors.append(ValidationError(f"{path}.name", "Name is required."))
    check_length(errors, f"{path}.name", person.name, MAX_NAME_LENGTH)
    check_length(errors, f"{path}.team", person.team, MAX_NAME_LENGTH)

    if not person.initials:
        errors.append(ValidationError(f"{path}.initials", "Initials are required."))
    elif not initials_pattern().match(person.initials):
        errors.append(ValidationError(f"{path}.initials", INITIALS_RULE))

    if not person.email:
        errors.append(ValidationError(f"{path}.email", "Email is required."))
    elif len(person.email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(person.email):
        errors.append(ValidationError(f"{path}.email", "Enter a valid email address."))


def _validate_basics(
    errors: list[ValidationError],
    data: NewOnePagerInput,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> None:
    if not data.data_product:
        errors.append(ValidationError("dataProduct", "Data Product is required."))
    elif not DATA_PRODUCT_PATTERN.match(data.data_product):
        errors.append(ValidationError("dataProduct", DATA_PRODUCT_RULE))

    if not data.product_name:
        errors.append(ValidationError("productName", "Product Name is required."))
    check_length(errors, "productName", data.product_name, MAX_NAME_LENGTH)

    if not data.description:
        errors.append(ValidationError("description", "Description is required."))
    check_length(errors, "description", data.description, MAX_TEXT_LENGTH)

    if not data.business_problem_statement:
        errors.append(
            ValidationError(
                "businessProblemStatement", BUSINESS_PROBLEM_REQUIRED_MESSAGE
            )
        )
    check_length(
        errors,
        "businessProblemStatement",
        data.business_problem_statement,
        MAX_TEXT_LENGTH,
    )
    _validate_domain_and_type(
        errors,
        data.business_domain,
        data.data_product_type,
        allowed_domains,
        allowed_types,
    )


def validate_owner_and_smes(
    owner: PersonRef, smes: list[PersonRef]
) -> list[ValidationError]:
    """Validate the (normalized) Owner and SMEs.

    Their initials grant edit access (``one_pager_authorized_users``), so every
    person needs a name, valid initials and an email, and nobody may appear
    twice.
    """
    errors: list[ValidationError] = []
    _validate_person(errors, "dataProductOwner", owner)

    seen_initials: set[str] = set()
    for i, sme in enumerate(smes):
        path = f"smes[{i}]"
        _validate_person(errors, path, sme)
        if not sme.initials:
            continue
        if sme.initials == owner.initials:
            errors.append(
                ValidationError(
                    f"{path}.initials", "The Owner cannot also be listed as an SME."
                )
            )
        elif sme.initials in seen_initials:
            errors.append(
                ValidationError(f"{path}.initials", "This SME is listed twice.")
            )
        seen_initials.add(sme.initials)
    return errors


def _validate_people(
    errors: list[ValidationError], data: NewOnePagerInput, creator: CurrentUser
) -> None:
    errors.extend(validate_owner_and_smes(data.owner, data.smes))

    # D3: the creator must keep edit access to the Draft they create.
    seen_initials = {s.initials for s in data.smes if s.initials}
    if creator.initials not in {data.owner.initials, *seen_initials}:
        errors.append(
            ValidationError(
                "smes",
                f"Add yourself ({creator.initials}) as the Owner or as an SME "
                "to keep edit access to this One Pager.",
            )
        )


def validate_create(
    data: NewOnePagerInput,
    creator: CurrentUser,
    allowed_domains: list[str],
    allowed_types: list[str],
) -> list[ValidationError]:
    """Validate (already normalized) create input.

    Args:
        data: Input after ``normalize_new_one_pager``.
        creator: The authenticated user creating the One Pager.
        allowed_domains: Active values of ``ref_business_domains``.
        allowed_types: Active values of ``ref_data_product_types``.

    Returns:
        List of validation errors (empty = valid).

    """
    errors: list[ValidationError] = []
    _validate_basics(errors, data, allowed_domains, allowed_types)
    _validate_people(errors, data, creator)
    return errors
