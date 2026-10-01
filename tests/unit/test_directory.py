"""SCIM ``Me`` lookup of the signed-in user's name (identity plan Phase 5)."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.iam import ComplexValue, Name, User

from onepagerapp import directory
from onepagerapp.config import AppConfig
from onepagerapp.data_access.connection import USER_TOKEN_HEADER
from onepagerapp.directory import (
    DirectoryUser,
    directory_user_from_scim,
    get_me,
    lookup_directory_user,
)


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


def _config(mode: str) -> AppConfig:
    return AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH="/Volumes/x")


@pytest.mark.unit
def test__lookup__databricks_uses_the_forwarded_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(
        directory, "get_me", lambda token: calls.append(token) or DirectoryUser()
    )

    result = lookup_directory_user(
        _config("databricks"), {USER_TOKEN_HEADER: "user-token"}
    )

    assert result == DirectoryUser()
    assert calls == ["user-token"]


@pytest.mark.unit
def test__lookup__databricks_without_token_does_not_use_the_app_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(directory, "get_me", calls.append)

    assert lookup_directory_user(_config("databricks"), {}) is None
    assert calls == []


@pytest.mark.unit
def test__lookup__local_integration_uses_the_cli_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(
        directory, "get_me", lambda token: calls.append(token) or DirectoryUser()
    )

    assert lookup_directory_user(_config("local-integration"), {}) is not None
    assert calls == [None]


@pytest.mark.unit
def test__lookup__no_call_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(directory, "get_me", calls.append)
    monkeypatch.setattr(directory, "WorkspaceClient", calls.append)

    lookup_directory_user(_config("local-mock"), {USER_TOKEN_HEADER: "user-token"})

    assert calls == []
