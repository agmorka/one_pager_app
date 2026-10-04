import pytest

from onepagerapp.auth import (
    display_name,
    initials_from_username,
    resolve_current_user,
)
from onepagerapp.config import AppConfig
from onepagerapp.directory import DirectoryUser


def _config(**overrides: str) -> AppConfig:
    return AppConfig(ONE_PAGER_APP_VOLUME_PATH="/Volumes/x", **overrides)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("x0wadm@becoc001.onmicrosoft.com", "X0W"),
        ("X0WADM@BECOC001.ONMICROSOFT.COM", "X0W"),
        ("X0wAdm@BecOC001.onmicrosoft.com", "X0W"),
        ("MJOADM@BECOC001.onmicrosoft.com", "MJO"),
        (" mjoadm@becoc001.onmicrosoft.com ", "MJO"),
    ],
)
def test__initials_from_username__corporate_format(
    username: str, expected: str
) -> None:
    assert initials_from_username(username, _config()) == expected


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
def test__initials_from_username__not_recognised(username: str | None) -> None:
    assert initials_from_username(username, _config()) is None


@pytest.mark.unit
def test__initials_from_username__suffix_list_accepts_both_formats() -> None:
    config = _config(ONE_PAGER_APP_USERNAME_SUFFIXES="adm,")

    assert initials_from_username("x0wadm@becoc001.onmicrosoft.com", config) == "X0W"
    assert initials_from_username("x0w@becoc001.onmicrosoft.com", config) == "X0W"


@pytest.mark.unit
def test__initials_from_username__without_suffix() -> None:
    config = _config(ONE_PAGER_APP_USERNAME_SUFFIXES="")

    assert initials_from_username("x0w@becoc001.onmicrosoft.com", config) == "X0W"
    assert initials_from_username("x0wadm@becoc001.onmicrosoft.com", config) is None


@pytest.mark.unit
def test__initials_from_username__changed_domain() -> None:
    config = _config(ONE_PAGER_APP_USER_DOMAINS="bec.dk")

    assert initials_from_username("x0wadm@BEC.dk", config) == "X0W"
    assert initials_from_username("x0wadm@becoc001.onmicrosoft.com", config) is None


@pytest.mark.unit
def test__initials_from_username__configured_pattern() -> None:
    config = _config(ONE_PAGER_APP_INITIALS_PATTERN=r"^[A-Z]{2,4}$")

    assert initials_from_username("abadm@becoc001.onmicrosoft.com", config) == "AB"
    assert initials_from_username("x0wadm@becoc001.onmicrosoft.com", config) is None


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
def test__display_name__directory_then_initials(
    directory_user: DirectoryUser | None, expected: str
) -> None:
    assert display_name(directory_user, "X0W") == expected


@pytest.mark.unit
def test__resolve_current_user__recognised_username() -> None:
    user = resolve_current_user("x0wadm@becoc001.onmicrosoft.com", _config())

    assert user.username == "x0wadm@becoc001.onmicrosoft.com"
    assert user.initials == "X0W"
    assert user.display_name == "X0W"  # no directory entry: the initials


@pytest.mark.unit
def test__resolve_current_user__name_from_the_directory() -> None:
    user = resolve_current_user(
        "x0wadm@becoc001.onmicrosoft.com",
        _config(),
        DirectoryUser(
            given_name="Agnieszka",
            family_name="Kępkowska",
            emails=("agnieszka.kepkowska@bec.dk",),
        ),
    )

    assert user.initials == "X0W"
    assert user.display_name == "Agnieszka Kępkowska"
    assert user.email == "x0w@bec.dk"  # from the initials, not the directory


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
def test__resolve_current_user__name_without_the_admin_account(
    directory_user: DirectoryUser,
) -> None:
    user = resolve_current_user(
        "x0wadm@becoc001.onmicrosoft.com", _config(), directory_user
    )

    assert user.display_name == "Agnieszka Kępkowska"


@pytest.mark.unit
def test__resolve_current_user__only_the_admin_account_falls_back_to_initials() -> (
    None
):
    user = resolve_current_user(
        "x0wadm@becoc001.onmicrosoft.com",
        _config(),
        DirectoryUser(display_name="X0WADM"),
    )

    assert user.display_name == "X0W"


@pytest.mark.unit
def test__resolve_current_user__email_domain_is_configurable() -> None:
    user = resolve_current_user(
        "x0wadm@becoc001.onmicrosoft.com",
        _config(ONE_PAGER_APP_EMAIL_DOMAIN="example.com"),
    )

    assert user.email == "x0w@example.com"


@pytest.mark.unit
def test__resolve_current_user__unrecognised_username_has_no_initials() -> None:
    user = resolve_current_user("alice.brown@company.com", _config())

    assert user.initials == ""
    assert user.display_name == "alice.brown@company.com"  # never guessed
