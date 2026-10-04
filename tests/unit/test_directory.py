"""SCIM ``Me`` lookup of the signed-in user's name (identity plan Phase 5)."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.iam import ComplexValue, Name, User

from onepagerapp import directory
from onepagerapp.data_access.connection import USER_TOKEN_HEADER
from onepagerapp.directory import (
    DirectoryUser,
    directory_user_from_scim,
    get_me,
    lookup_directory_user,
)
from tests.helpers import config, failing


def _recording_get_me(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Replace ``get_me`` with a recorder of the tokens it is called with."""
    calls: list[str | None] = []

    def get_me(token: str | None) -> DirectoryUser:
        calls.append(token)
        return DirectoryUser()

    monkeypatch.setattr(directory, "get_me", get_me)
    return calls


@pytest.mark.unit
def test__full_scim_user__directory_user_from_scim__names_emails_groups() -> None:
    """Names are trimmed, the primary email comes first, groups are kept."""
    # Given
    user = User(
        display_name="Agnieszka Kępkowska",
        name=Name(given_name=" Agnieszka ", family_name="Kępkowska"),
        emails=[
            ComplexValue(value="ak@other.dk"),
            ComplexValue(value="ak@bec.dk", primary=True),
        ],
        groups=[ComplexValue(display="PAG-BEC-LHX-DEV-DataPlatEng-Base")],
    )

    # When
    result = directory_user_from_scim(user)

    # Then
    assert result == DirectoryUser(
        display_name="Agnieszka Kępkowska",
        given_name="Agnieszka",
        family_name="Kępkowska",
        emails=("ak@bec.dk", "ak@other.dk"),
        groups=("PAG-BEC-LHX-DEV-DataPlatEng-Base",),
    )
    assert result.full_name == "Agnieszka Kępkowska"
    assert result.email == "ak@bec.dk"


@pytest.mark.unit
def test__only_display_name__directory_user_from_scim__no_full_name() -> None:
    """Without given and family name there is no full name."""
    # When
    result = directory_user_from_scim(User(display_name="Agnieszka K."))

    # Then
    assert result.full_name is None
    assert result.display_name == "Agnieszka K."
    assert result.email is None
    assert result.groups == ()


@pytest.mark.unit
def test__blank_values__directory_user_from_scim__empty_user() -> None:
    """Blank values read as missing."""
    # When
    result = directory_user_from_scim(
        User(display_name=" ", name=Name(given_name="", family_name=None))
    )

    # Then
    assert result == DirectoryUser()
    assert result.full_name is None


@pytest.mark.unit
def test__user_token__get_me__client_built_with_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup authenticates with the user's own token."""
    # Given
    built: list[dict[str, object]] = []

    def workspace_client(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        return SimpleNamespace(
            current_user=SimpleNamespace(me=lambda: User(display_name="A B"))
        )

    monkeypatch.setattr(directory, "WorkspaceClient", workspace_client)

    # When
    result = get_me("user-token", "https://adb.example")

    # Then
    assert result.display_name == "A B"
    assert built == [
        {"host": "https://adb.example", "token": "user-token", "auth_type": "pat"}
    ]


@pytest.mark.unit
def test__scim_call_fails__get_me__none_and_token_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed lookup is logged without the token."""
    # Given
    monkeypatch.setattr(
        directory,
        "WorkspaceClient",
        lambda **_: SimpleNamespace(
            current_user=SimpleNamespace(
                me=failing("403 Forbidden: missing scope iam.current-user:read")
            )
        ),
    )

    # When
    result = get_me("user-token")

    # Then
    assert result is None
    assert "SCIM Me) failed" in caplog.text
    assert "user-token" not in caplog.text


@pytest.mark.unit
def test__databricks_with_token__lookup_directory_user__forwarded_token_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployed, the forwarded user token is used."""
    # Given
    calls = _recording_get_me(monkeypatch)

    # When
    result = lookup_directory_user(
        config(APP_MODE="databricks"), {USER_TOKEN_HEADER: "user-token"}
    )

    # Then
    assert result == DirectoryUser()
    assert calls == ["user-token"]


@pytest.mark.unit
def test__databricks_without_token__lookup_directory_user__no_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the user's token the app identity is never used instead."""
    # Given
    calls = _recording_get_me(monkeypatch)

    # When
    result = lookup_directory_user(config(APP_MODE="databricks"), {})

    # Then
    assert result is None
    assert calls == []


@pytest.mark.unit
def test__local_integration__lookup_directory_user__cli_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locally the CLI profile is used (no token)."""
    # Given
    calls = _recording_get_me(monkeypatch)

    # When
    result = lookup_directory_user(config(APP_MODE="local-integration"), {})

    # Then
    assert result is not None
    assert calls == [None]


@pytest.mark.unit
def test__mock_mode_with_name__lookup_directory_user__configured_name_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock mode returns the configured name without any directory call."""
    # Given
    calls = _recording_get_me(monkeypatch)
    monkeypatch.setattr(directory, "WorkspaceClient", calls.append)
    mock = config(
        APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER_NAME="Agnieszka Kępkowska"
    )

    # When
    result = lookup_directory_user(mock, {USER_TOKEN_HEADER: "user-token"})

    # Then
    assert result == DirectoryUser(display_name="Agnieszka Kępkowska")
    assert calls == []


@pytest.mark.unit
def test__mock_mode_without_name__lookup_directory_user__default_name() -> None:
    """Mock mode has a default name."""
    # When
    result = lookup_directory_user(config(APP_MODE="local-mock"), {})

    # Then
    assert result == DirectoryUser(display_name="Local Dev User")
