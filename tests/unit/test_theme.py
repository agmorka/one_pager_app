"""Tests for the app-shell theme helpers."""

from collections.abc import Callable
from types import ModuleType

import pytest

from tests.helpers import APP_DIR


@pytest.fixture
def theme(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return ``adapters.theme``."""
    return import_app_module("adapters.theme")


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["DEV", "INT", "TST", "UAT", "PRD"])
def test__known_environment__environment_badge__label_in_its_colors(
    theme: ModuleType, environment: str
) -> None:
    """The badge shows the environment as text in its own colours."""
    # When
    html = theme.environment_badge(environment)

    # Then
    background, text = theme.ENVIRONMENT_BADGE_COLORS[environment]
    assert f">{environment}</span>" in html
    assert f"background-color: {background}" in html
    assert f"color: {text}" in html


@pytest.mark.unit
def test__unknown_environment__environment_badge__default_color(
    theme: ModuleType,
) -> None:
    """Other environments use the default colour."""
    # When
    html = theme.environment_badge("LOCAL")

    # Then
    assert theme.DEFAULT_BADGE_COLOR in html


@pytest.mark.unit
def test__streamlit_config__read__bec_theme_colors() -> None:
    """The Streamlit theme uses the BEC primary and text colours."""
    # When
    config = (APP_DIR / ".streamlit" / "config.toml").read_text()

    # Then
    assert 'primaryColor = "#0c1c49"' in config
    assert 'textColor = "#343333"' in config


@pytest.mark.unit
def test__two_roles__role_badges__one_labelled_pill_each(theme: ModuleType) -> None:
    """Each role is an outlined pill with an accessible label."""
    # When
    badges = theme.role_badges(["Owner/SME", "Approver"])

    # Then
    assert badges.count('class="role-badge"') == 2
    assert ">Owner/SME</span>" in badges
    assert 'aria-label="Role: Approver"' in badges


@pytest.mark.unit
def test__role_with_html__role_badges__escaped(theme: ModuleType) -> None:
    """Role names are HTML-escaped in the text and the label."""
    # When
    badges = theme.role_badges(["<b>x</b>"])

    # Then
    assert badges == (
        '<span class="role-badge" aria-label="Role: &lt;b&gt;x&lt;/b&gt;">'
        "&lt;b&gt;x&lt;/b&gt;</span>"
    )
