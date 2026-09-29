import pytest

from onepagerapp.auth import (
    display_name_from_username,
    initials_from_username,
    resolve_current_user,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("MJOADM@BECOC001.onmicrosoft.com", "MJO"),
        ("abADM@BECOC001.onmicrosoft.com", "AB"),
        ("alice.brown@company.com", "AB"),
        ("local-dev-user@mock.local", "LD"),
        ("Alice Brown", "AB"),
        ("svc@company.com", "SVC"),
        ("", "?"),
    ],
)
def test__initials_from_username__known_formats(username: str, expected: str) -> None:
    assert initials_from_username(username) == expected


@pytest.mark.unit
def test__display_name_from_username__email_is_title_cased() -> None:
    assert display_name_from_username("alice.brown@company.com") == "Alice Brown"
    assert display_name_from_username("MJOADM@BECOC001.onmicrosoft.com") == "MJO"
    assert display_name_from_username("") == "Unknown user"


@pytest.mark.unit
def test__resolve_current_user__combines_fields() -> None:
    user = resolve_current_user("alice.brown@company.com")
    assert user.username == "alice.brown@company.com"
    assert user.initials == "AB"
    assert user.display_name == "Alice Brown"
