"""Tests for AppConfig and the environment shown in the sidebar badge."""

import pytest

from onepagerapp.config import AppConfig, Environment


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
