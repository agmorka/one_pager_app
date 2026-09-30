import pytest

from onepagerapp.auth import (
    display_name_from_username,
    initials_from_username,
    resolve_current_user,
)
from onepagerapp.config import AppConfig


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
def test__display_name_from_username__email_is_title_cased() -> None:
    assert display_name_from_username("alice.brown@company.com") == "Alice Brown"
    assert display_name_from_username("") == "Unknown user"


@pytest.mark.unit
def test__resolve_current_user__recognised_username() -> None:
    user = resolve_current_user("x0wadm@becoc001.onmicrosoft.com", _config())

    assert user.username == "x0wadm@becoc001.onmicrosoft.com"
    assert user.initials == "X0W"
    assert user.display_name == "X0W"


@pytest.mark.unit
def test__resolve_current_user__unrecognised_username_has_no_initials() -> None:
    user = resolve_current_user("alice.brown@company.com", _config())

    assert user.initials == ""
    assert user.display_name == "Alice Brown"
