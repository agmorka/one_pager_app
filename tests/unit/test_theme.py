"""Tests for the app-shell theme helpers."""

from pathlib import Path
from types import ModuleType

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def theme(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    from adapters import theme  # noqa: PLC0415

    return theme


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["DEV", "INT", "TST", "UAT", "PRD"])
def test__environment_badge__shows_label_as_text(
    theme: ModuleType, environment: str
) -> None:
    html = theme.environment_badge(environment)
    background, text = theme.ENVIRONMENT_BADGE_COLORS[environment]
    assert f">{environment}</span>" in html
    assert f"background-color: {background}" in html
    assert f"color: {text}" in html


@pytest.mark.unit
def test__environment_badge__unknown_environment_uses_default_color(
    theme: ModuleType,
) -> None:
    assert theme.DEFAULT_BADGE_COLOR in theme.environment_badge("LOCAL")


@pytest.mark.unit
def test__streamlit_config__uses_bec_theme() -> None:
    config = (APP_DIR / ".streamlit" / "config.toml").read_text()
    assert 'primaryColor = "#0c1c49"' in config
    assert 'textColor = "#343333"' in config


@pytest.mark.unit
def test__role_badges__text_in_an_outlined_pill(theme: ModuleType) -> None:
    badges = theme.role_badges(["Owner/SME", "Approver"])

    assert badges.count('class="role-badge"') == 2
    assert ">Owner/SME</span>" in badges
    assert 'aria-label="Role: Approver"' in badges
    assert theme.role_badges(["<b>x</b>"]) == (
        '<span class="role-badge" aria-label="Role: &lt;b&gt;x&lt;/b&gt;">'
        "&lt;b&gt;x&lt;/b&gt;</span>"
    )
