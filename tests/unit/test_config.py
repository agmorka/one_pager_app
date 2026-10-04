"""Tests for AppConfig and the environment shown in the sidebar badge."""

import pytest

from onepagerapp.auth import initials_from_username
from onepagerapp.config import AppConfig, Environment
from onepagerapp.data_access import create_data_access

DEDICATED_GROUPS = {
    "ONE_PAGER_APP_GROUP_OWNER_SME": "OPA-OwnerSME-{env}",
    "ONE_PAGER_APP_GROUP_APPROVER": "OPA-Approver-{env}",
    "ONE_PAGER_APP_GROUP_ADMIN": "OPA-Admin-{env}",
}


def _config(**settings: str) -> AppConfig:
    """Return a config on a volume whose catalog says nothing about the environment."""
    return AppConfig(ONE_PAGER_APP_VOLUME_PATH="/Volumes/cat/schema/vol", **settings)


# ============================================================================
# Environment
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("value", ["UAT", "uat", " uat "])
def test__environment_setting__environment__setting_wins_over_catalog(
    value: str,
) -> None:
    """An explicit setting wins, ignoring case and spaces."""
    # Given
    config = _config(
        ONE_PAGER_APP_ENVIRONMENT=value,
        ONE_PAGER_APP_DATABRICKS_CATALOG="prd_one_pager",
    )

    # When / Then
    assert config.environment is Environment.UAT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("catalog", "expected"),
    [
        ("dev_bia_meta", Environment.DEV),
        ("int_one_pager", Environment.INT),
        ("tst_bia_meta", Environment.TST),
        ("prd_one_pager", Environment.PRD),
        ("sandbox", Environment.DEV),
    ],
)
def test__catalog__environment__derived_from_prefix(
    catalog: str, expected: Environment
) -> None:
    """Without a setting the catalog prefix decides; unknown means DEV."""
    # Given
    config = _config(ONE_PAGER_APP_DATABRICKS_CATALOG=catalog)

    # When / Then
    assert config.environment is expected


@pytest.mark.unit
def test__unknown_environment_setting__environment__falls_back_to_catalog() -> None:
    """An unknown setting is ignored."""
    # Given
    config = _config(
        ONE_PAGER_APP_ENVIRONMENT="staging",
        ONE_PAGER_APP_DATABRICKS_CATALOG="uat_one_pager",
    )

    # When / Then
    assert config.environment is Environment.UAT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("volume", "expected"),
    [
        ("/Volumes/prd_bia_meta/onepager_app/one_pager_registry", Environment.PRD),
        ("/Volumes/tst_bia_meta/onepager_app/one_pager_registry/", Environment.TST),
        ("/Volumes/uat_bia_meta/onepager_app/one_pager_registry", Environment.UAT),
    ],
)
def test__volume_in_environment_catalog__environment__from_volume_before_catalog(
    volume: str, expected: Environment
) -> None:
    """The volume's catalog beats the default catalog, and sets the groups."""
    # Given
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=volume)  # catalog: dev default

    # When / Then
    assert config.environment is expected
    approver = f"PAG-BEC-LHX-{expected.value}-DataPlatEng-Base"
    assert config.role_groups["approver"] == approver


@pytest.mark.unit
def test__setting_and_volume__environment__setting_wins() -> None:
    """An explicit setting beats the volume."""
    # Given
    config = AppConfig(
        ONE_PAGER_APP_VOLUME_PATH="/Volumes/prd_bia_meta/onepager_app/v",
        ONE_PAGER_APP_ENVIRONMENT="UAT",
    )

    # When / Then
    assert config.environment is Environment.UAT


@pytest.mark.unit
def test__local_folder_volume__environment__falls_back_to_catalog() -> None:
    """A local folder says nothing about the environment."""
    # Given
    config = AppConfig(
        ONE_PAGER_APP_VOLUME_PATH="../tests/fixtures/sample_one_pagers",
        ONE_PAGER_APP_DATABRICKS_CATALOG="int_bia_meta",
    )

    # When / Then
    assert config.environment is Environment.INT


@pytest.mark.unit
def test__environment_variables__from_env__read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings are read from the process environment."""
    # Given
    monkeypatch.setenv("ONE_PAGER_APP_VOLUME_PATH", "/Volumes/cat/schema/vol")
    monkeypatch.setenv("ONE_PAGER_APP_ENVIRONMENT", "PRD")

    # When
    config = AppConfig.from_env()

    # Then
    assert config.environment is Environment.PRD


# ============================================================================
# Identity settings
# ============================================================================


@pytest.mark.unit
def test__no_identity_settings__config__corporate_defaults() -> None:
    """By default: the corporate domain, the adm suffix and 3 characters."""
    # When
    config = _config()

    # Then
    assert config.user_domains == frozenset({"becoc001.onmicrosoft.com"})
    assert config.username_suffixes == ("adm",)
    assert config.initials_pattern.pattern == r"^[A-Z0-9]{3}$"


@pytest.mark.unit
def test__domains_with_spaces_and_case__user_domains__normalised() -> None:
    """Domains are trimmed, lower-cased and blanks dropped."""
    # When
    config = _config(
        ONE_PAGER_APP_USER_DOMAINS=" BECOC001.onmicrosoft.com , mock.local,"
    )

    # Then
    assert config.user_domains == frozenset({"becoc001.onmicrosoft.com", "mock.local"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("adm", ("adm",)),
        ("ADM, ", ("adm", "")),
        ("adm,", ("adm", "")),
        ("", ("",)),
        ("a,adm,a", ("adm", "a")),
    ],
)
def test__suffix_list__username_suffixes__unique_longest_first(
    value: str, expected: tuple[str, ...]
) -> None:
    """Suffixes are lower-cased, de-duplicated and tried longest first."""
    # When
    suffixes = _config(ONE_PAGER_APP_USERNAME_SUFFIXES=value).username_suffixes

    # Then
    assert suffixes == expected


@pytest.mark.unit
def test__invalid_regex__initials_pattern__raises() -> None:
    """A pattern that does not compile is refused at start-up."""
    # When / Then
    with pytest.raises(ValueError, match="not a valid regex"):
        _config(ONE_PAGER_APP_INITIALS_PATTERN="^[A-Z")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "username", "initials"),
    [
        ({}, "lduadm@becoc001.onmicrosoft.com", "LDU"),
        (
            {"ONE_PAGER_APP_MOCK_USER": "x0wadm@becoc001.onmicrosoft.com"},
            "x0wadm@becoc001.onmicrosoft.com",
            "X0W",
        ),
    ],
    ids=["default", "configured"],
)
def test__mock_mode__current_user__parsed_like_a_real_username(
    settings: dict[str, str], username: str, initials: str
) -> None:
    """The mock user follows the corporate format."""
    # Given
    config = _config(APP_MODE="local-mock", **settings)

    # When
    current = create_data_access(config).get_current_user()

    # Then
    assert current == username
    assert initials_from_username(current, config) == initials


# ============================================================================
# Role groups
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["DEV", "INT", "TST", "UAT", "PRD"])
def test__no_group_settings__role_groups__interim_group_of_environment(
    environment: str,
) -> None:
    """Every role defaults to the environment's DataPlatEng group."""
    # When
    groups = _config(ONE_PAGER_APP_ENVIRONMENT=environment).role_groups

    # Then
    expected = f"PAG-BEC-LHX-{environment}-DataPlatEng-Base"
    assert groups == {"owner_sme": expected, "approver": expected, "admin": expected}


@pytest.mark.unit
def test__group_settings__role_groups__trimmed_with_environment_filled_in() -> None:
    """``{env}`` is replaced and spaces are trimmed."""
    # When
    groups = _config(
        ONE_PAGER_APP_ENVIRONMENT="UAT",
        ONE_PAGER_APP_GROUP_OWNER_SME="OPA-OwnerSME-{env}",
        ONE_PAGER_APP_GROUP_APPROVER=" OPA-Approver ",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",
    ).role_groups

    # Then
    assert groups == {
        "owner_sme": "OPA-OwnerSME-UAT",
        "approver": "OPA-Approver",
        "admin": "OPA-Admin-UAT",
    }


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "  ", "GroupA,GroupB"])
def test__blank_or_several_groups__role_group_setting__raises(value: str) -> None:
    """Each role needs exactly one group name."""
    # When / Then
    with pytest.raises(ValueError, match="one group name"):
        _config(ONE_PAGER_APP_GROUP_APPROVER=value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, ["owner_sme", "approver", "admin"]),
        (
            {
                "ONE_PAGER_APP_ENVIRONMENT": "UAT",
                "ONE_PAGER_APP_GROUP_APPROVER": "OPA-Approver-{env}",
                "ONE_PAGER_APP_GROUP_ADMIN": "OPA-Admin-{env}",
            },
            ["owner_sme"],
        ),
        (DEDICATED_GROUPS, []),
    ],
    ids=["all-default", "owner-sme-default", "dedicated"],
)
def test__group_settings__interim_roles__roles_still_on_the_interim_group(
    settings: dict[str, str], expected: list[str]
) -> None:
    """Interim roles are those still on the DataPlatEng group."""
    # When
    roles = _config(**settings).interim_roles

    # Then
    assert roles == expected


# ============================================================================
# Mock groups
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (
            {"ONE_PAGER_APP_ENVIRONMENT": "INT"},
            frozenset({"PAG-BEC-LHX-INT-DataPlatEng-Base"}),
        ),
        ({"ONE_PAGER_APP_MOCK_GROUPS": " A, B ,"}, frozenset({"A", "B"})),
        ({"ONE_PAGER_APP_MOCK_GROUPS": ""}, frozenset()),
    ],
    ids=["default", "configured", "empty"],
)
def test__mock_groups_setting__mock_groups__parsed(
    settings: dict[str, str], expected: frozenset[str]
) -> None:
    """The mock user is in the interim group unless configured otherwise."""
    # When
    groups = _config(**settings).mock_groups

    # Then
    assert groups == expected


@pytest.mark.unit
def test__mock_groups__mock_data_access_memberships__follow_the_setting() -> None:
    """Mock data answers membership from the configured mock groups."""
    # Given
    config = _config(APP_MODE="local-mock", ONE_PAGER_APP_MOCK_GROUPS="OPA-Approver")

    # When
    memberships = create_data_access(config).get_group_memberships(
        {"approver": "OPA-Approver", "admin": "OPA-Admin"}
    )

    # Then
    assert memberships == {"approver": True, "admin": False}
