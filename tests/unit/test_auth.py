"""Initials from the corporate username, display names and the current user."""

import pytest

from onepagerapp.auth import (
    display_name,
    initials_from_username,
    resolve_current_user,
)
from onepagerapp.directory import DirectoryUser
from tests.helpers import config

CORPORATE = "x0wadm@becoc001.onmicrosoft.com"


@pytest.mark.unit
@pytest.mark.parametrize(
    "username",
    [
        "x0wadm@becoc001.onmicrosoft.com",
        "X0WADM@BECOC001.ONMICROSOFT.COM",
        "X0wAdm@BecOC001.onmicrosoft.com",
        " x0wadm@becoc001.onmicrosoft.com ",
    ],
)
def test__corporate_username__initials_from_username__upper_case_initials(
    username: str,
) -> None:
    """Case and surrounding spaces do not matter."""
    # When
    initials = initials_from_username(username, config())

    # Then
    assert initials == "X0W"


@pytest.mark.unit
@pytest.mark.parametrize(
    "username",
    [
        "x0w@becoc001.onmicrosoft.com",  # no suffix, not accepted by default
        "x0wadm@company.com",  # unknown domain
        "x0wadm@onmicrosoft.com",  # parent domain is not the domain
        "x0wadm",  # no domain
        "adm@becoc001.onmicrosoft.com",  # suffix only
        "abadm@becoc001.onmicrosoft.com",  # too short
        "abcdadm@becoc001.onmicrosoft.com",  # too long
        "x-wadm@becoc001.onmicrosoft.com",  # invalid character
        "alice.brown@becoc001.onmicrosoft.com",
        "",
        None,
    ],
)
def test__other_username__initials_from_username__none(username: str | None) -> None:
    """Usernames that do not follow the corporate format are not recognised."""
    # When
    initials = initials_from_username(username, config())

    # Then
    assert initials is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "username", "expected"),
    [
        (
            {"ONE_PAGER_APP_USERNAME_SUFFIXES": "adm,"},
            "x0wadm@becoc001.onmicrosoft.com",
            "X0W",
        ),
        (
            {"ONE_PAGER_APP_USERNAME_SUFFIXES": "adm,"},
            "x0w@becoc001.onmicrosoft.com",
            "X0W",
        ),
        (
            {"ONE_PAGER_APP_USERNAME_SUFFIXES": ""},
            "x0w@becoc001.onmicrosoft.com",
            "X0W",
        ),
        ({"ONE_PAGER_APP_USERNAME_SUFFIXES": ""}, CORPORATE, None),
        ({"ONE_PAGER_APP_USER_DOMAINS": "bec.dk"}, "x0wadm@BEC.dk", "X0W"),
        ({"ONE_PAGER_APP_USER_DOMAINS": "bec.dk"}, CORPORATE, None),
        (
            {"ONE_PAGER_APP_INITIALS_PATTERN": r"^[A-Z]{2,4}$"},
            "abadm@becoc001.onmicrosoft.com",
            "AB",
        ),
        ({"ONE_PAGER_APP_INITIALS_PATTERN": r"^[A-Z]{2,4}$"}, CORPORATE, None),
    ],
    ids=[
        "suffix-list-with-suffix",
        "suffix-list-without-suffix",
        "no-suffix-without-suffix",
        "no-suffix-with-suffix",
        "changed-domain-new",
        "changed-domain-old",
        "pattern-match",
        "pattern-mismatch",
    ],
)
def test__configured_format__initials_from_username__follows_settings(
    settings: dict[str, str], username: str, expected: str | None
) -> None:
    """Suffixes, domains and the initials pattern are configurable."""
    # When
    initials = initials_from_username(username, config(**settings))

    # Then
    assert initials == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("directory_user", "expected"),
    [
        (
            DirectoryUser(
                display_name="A. Kępkowska",
                given_name="Agnieszka",
                family_name="Kępkowska",
            ),
            "Agnieszka Kępkowska",
        ),
        (DirectoryUser(display_name="A. Kępkowska"), "A. Kępkowska"),
        (DirectoryUser(given_name="Agnieszka"), "Agnieszka"),
        (DirectoryUser(), "X0W"),
        (None, "X0W"),
    ],
)
def test__directory_entry__display_name__full_name_then_display_then_initials(
    directory_user: DirectoryUser | None, expected: str
) -> None:
    """The best name the directory has, else the initials."""
    # When
    name = display_name(directory_user, "X0W")

    # Then
    assert name == expected


@pytest.mark.unit
def test__no_directory_entry__resolve_current_user__initials_as_name() -> None:
    """Without a directory entry the initials are the name."""
    # When
    user = resolve_current_user(CORPORATE, config())

    # Then
    assert (user.username, user.initials, user.display_name) == (
        CORPORATE,
        "X0W",
        "X0W",
    )


@pytest.mark.unit
def test__directory_entry__resolve_current_user__directory_name_initials_email() -> (
    None
):
    """The name comes from the directory; the email from the initials."""
    # Given
    directory_user = DirectoryUser(
        given_name="Agnieszka",
        family_name="Kępkowska",
        emails=("agnieszka.kepkowska@bec.dk",),
    )

    # When
    user = resolve_current_user(CORPORATE, config(), directory_user)

    # Then
    assert user.initials == "X0W"
    assert user.display_name == "Agnieszka Kępkowska"
    assert user.email == "x0w@bec.dk"


@pytest.mark.unit
@pytest.mark.parametrize(
    "directory_user",
    [
        DirectoryUser(display_name="Agnieszka Kępkowska (X0WADM)"),
        DirectoryUser(display_name="X0WADM - Agnieszka Kępkowska"),
        DirectoryUser(display_name="Agnieszka Kępkowska [x0wadm]"),
        DirectoryUser(given_name="Agnieszka", family_name="Kępkowska (X0WAdm)"),
        DirectoryUser(
            display_name="x0wadm@becoc001.onmicrosoft.com Agnieszka Kępkowska"
        ),
    ],
)
def test__name_with_admin_account__resolve_current_user__account_removed(
    directory_user: DirectoryUser,
) -> None:
    """The admin account in a directory name is not part of the name."""
    # When
    user = resolve_current_user(CORPORATE, config(), directory_user)

    # Then
    assert user.display_name == "Agnieszka Kępkowska"


@pytest.mark.unit
def test__name_is_only_the_admin_account__resolve_current_user__initials() -> None:
    """Nothing left after removing the account falls back to the initials."""
    # When
    user = resolve_current_user(
        CORPORATE, config(), DirectoryUser(display_name="X0WADM")
    )

    # Then
    assert user.display_name == "X0W"


@pytest.mark.unit
def test__email_domain_setting__resolve_current_user__email_in_that_domain() -> None:
    """The email domain is configurable."""
    # When
    user = resolve_current_user(
        CORPORATE, config(ONE_PAGER_APP_EMAIL_DOMAIN="example.com")
    )

    # Then
    assert user.email == "x0w@example.com"


@pytest.mark.unit
def test__unrecognised_username__resolve_current_user__no_initials() -> None:
    """Initials are never guessed; the username is shown instead."""
    # When
    user = resolve_current_user("alice.brown@company.com", config())

    # Then
    assert user.initials == ""
    assert user.display_name == "alice.brown@company.com"
