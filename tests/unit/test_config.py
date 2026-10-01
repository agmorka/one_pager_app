"""Tests for AppConfig and the environment shown in the sidebar badge."""

import pytest

from onepagerapp.auth import initials_from_username
from onepagerapp.config import AppConfig, Environment
from onepagerapp.data_access import create_data_access


def _config(**overrides: str) -> AppConfig:
    return AppConfig(ONE_PAGER_APP_VOLUME_PATH="/Volumes/cat/schema/vol", **overrides)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["UAT", "uat", " uat "])
def test__environment__explicit_setting_wins(value: str) -> None:
    config = _config(
        ONE_PAGER_APP_ENVIRONMENT=value,
        ONE_PAGER_APP_DATABRICKS_CATALOG="prd_one_pager",
    )
    assert config.environment is Environment.UAT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("catalog", "expected"),
    [
        ("dev_bia_meta", Environment.DEV),
        ("int_one_pager", Environment.INT),
        ("prd_one_pager", Environment.PRD),
        ("sandbox", Environment.DEV),
    ],
)
def test__environment__derived_from_catalog_prefix(
    catalog: str, expected: Environment
) -> None:
    assert _config(ONE_PAGER_APP_DATABRICKS_CATALOG=catalog).environment is expected


@pytest.mark.unit
def test__environment__unknown_setting_falls_back_to_catalog() -> None:
    config = _config(
        ONE_PAGER_APP_ENVIRONMENT="staging",
        ONE_PAGER_APP_DATABRICKS_CATALOG="uat_one_pager",
    )
    assert config.environment is Environment.UAT


@pytest.mark.unit
def test__from_env__reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONE_PAGER_APP_VOLUME_PATH", "/Volumes/cat/schema/vol")
    monkeypatch.setenv("ONE_PAGER_APP_ENVIRONMENT", "PRD")
    assert AppConfig.from_env().environment is Environment.PRD


@pytest.mark.unit
def test__identity_settings__defaults() -> None:
    config = _config()
    assert config.user_domains == frozenset({"becoc001.onmicrosoft.com"})
    assert config.username_suffixes == ("adm",)
    assert config.initials_pattern.pattern == r"^[A-Z0-9]{3}$"


@pytest.mark.unit
def test__user_domains__normalised() -> None:
    config = _config(
        ONE_PAGER_APP_USER_DOMAINS=" BECOC001.onmicrosoft.com , mock.local,"
    )
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
def test__username_suffixes__longest_first(
    value: str, expected: tuple[str, ...]
) -> None:
    assert _config(ONE_PAGER_APP_USERNAME_SUFFIXES=value).username_suffixes == expected


@pytest.mark.unit
def test__initials_pattern__invalid_regex_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid regex"):
        _config(ONE_PAGER_APP_INITIALS_PATTERN="^[A-Z")


@pytest.mark.unit
def test__mock_user__default_is_parsed_like_a_real_username() -> None:
    config = _config(APP_MODE="local-mock")  # default domains and suffixes
    username = create_data_access(config).get_current_user()

    assert username == "lduadm@becoc001.onmicrosoft.com"
    assert initials_from_username(username, config) == "LDU"


@pytest.mark.unit
def test__mock_user__configurable() -> None:
    config = _config(
        APP_MODE="local-mock",
        ONE_PAGER_APP_MOCK_USER="x0wadm@becoc001.onmicrosoft.com",
    )
    username = create_data_access(config).get_current_user()

    assert username == "x0wadm@becoc001.onmicrosoft.com"
    assert initials_from_username(username, config) == "X0W"


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["DEV", "INT", "TST", "UAT", "PRD"])
def test__role_groups__default_is_the_interim_group_per_environment(
    environment: str,
) -> None:
    groups = _config(ONE_PAGER_APP_ENVIRONMENT=environment).role_groups

    expected = f"BEC_BECOC001_LHX_{environment}_DataPlatEng"
    assert groups == {"owner_sme": expected, "approver": expected, "admin": expected}


@pytest.mark.unit
def test__role_groups__configured_values() -> None:
    groups = _config(
        ONE_PAGER_APP_ENVIRONMENT="UAT",
        ONE_PAGER_APP_GROUP_OWNER_SME="OPA-OwnerSME-{env}",
        ONE_PAGER_APP_GROUP_APPROVER=" OPA-Approver ",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",
    ).role_groups

    assert groups == {
        "owner_sme": "OPA-OwnerSME-UAT",
        "approver": "OPA-Approver",
        "admin": "OPA-Admin-UAT",
    }


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "  ", "GroupA,GroupB"])
def test__role_groups__must_be_one_group(value: str) -> None:
    with pytest.raises(ValueError, match="one group name"):
        _config(ONE_PAGER_APP_GROUP_APPROVER=value)


@pytest.mark.unit
def test__environment__tst_from_catalog_prefix() -> None:
    assert _config(ONE_PAGER_APP_DATABRICKS_CATALOG="tst_bia_meta").environment is (
        Environment.TST
    )


@pytest.mark.unit
def test__mock_groups__default_is_the_interim_group() -> None:
    assert _config(ONE_PAGER_APP_ENVIRONMENT="INT").mock_groups == frozenset(
        {"BEC_BECOC001_LHX_INT_DataPlatEng"}
    )


@pytest.mark.unit
def test__mock_groups__configured_and_empty() -> None:
    assert _config(ONE_PAGER_APP_MOCK_GROUPS=" A, B ,").mock_groups == frozenset(
        {"A", "B"}
    )
    assert _config(ONE_PAGER_APP_MOCK_GROUPS="").mock_groups == frozenset()


@pytest.mark.unit
def test__mock_data_access__memberships_from_mock_groups() -> None:
    config = _config(APP_MODE="local-mock", ONE_PAGER_APP_MOCK_GROUPS="OPA-Approver")
    data_access = create_data_access(config)

    assert data_access.get_group_memberships(
        {"approver": "OPA-Approver", "admin": "OPA-Admin"}
    ) == {"approver": True, "admin": False}
