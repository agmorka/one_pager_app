"""SCIM ``Me`` lookup of the signed-in user's name (identity plan Phase 5)."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.iam import ComplexValue, Name, User

from onepagerapp import directory
from onepagerapp.directory import DirectoryUser, directory_user_from_scim, get_me


@pytest.mark.unit
def test__scim__full_name_emails_and_groups() -> None:
    user = User(
        display_name="Agnieszka Kępkowska",
        name=Name(given_name=" Agnieszka ", family_name="Kępkowska"),
        emails=[
            ComplexValue(value="ak@other.dk"),
            ComplexValue(value="ak@bec.dk", primary=True),
        ],
        groups=[ComplexValue(display="BEC_BECOC001_LHX_DEV_DataPlatEng")],
    )

    result = directory_user_from_scim(user)

    assert result == DirectoryUser(
        display_name="Agnieszka Kępkowska",
        given_name="Agnieszka",
        family_name="Kępkowska",
        emails=("ak@bec.dk", "ak@other.dk"),
        groups=("BEC_BECOC001_LHX_DEV_DataPlatEng",),
    )
    assert result.full_name == "Agnieszka Kępkowska"
    assert result.email == "ak@bec.dk"


@pytest.mark.unit
def test__scim__only_display_name() -> None:
    result = directory_user_from_scim(User(display_name="Agnieszka K."))

    assert result.full_name is None
    assert result.display_name == "Agnieszka K."
    assert result.email is None
    assert result.groups == ()


@pytest.mark.unit
def test__scim__empty_response() -> None:
    result = directory_user_from_scim(
        User(display_name=" ", name=Name(given_name="", family_name=None))
    )

    assert result == DirectoryUser()
    assert result.full_name is None


@pytest.mark.unit
def test__get_me__uses_the_users_token(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, object]] = []

    def workspace_client(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        return SimpleNamespace(
            current_user=SimpleNamespace(me=lambda: User(display_name="A B"))
        )

    monkeypatch.setattr(directory, "WorkspaceClient", workspace_client)

    assert get_me("user-token", "https://adb.example").display_name == "A B"
    assert built == [
        {"host": "https://adb.example", "token": "user-token", "auth_type": "pat"}
    ]


@pytest.mark.unit
def test__get_me__http_error_gives_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing_me() -> User:
        msg = "403 Forbidden: missing scope iam.current-user:read"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        directory,
        "WorkspaceClient",
        lambda **_: SimpleNamespace(current_user=SimpleNamespace(me=failing_me)),
    )

    assert get_me("user-token") is None
    assert "SCIM Me) failed" in caplog.text
    assert "user-token" not in caplog.text
