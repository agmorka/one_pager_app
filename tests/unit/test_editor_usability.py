"""Editor usability: section list, submit state, lock warning, Add me."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from types import ModuleType

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.locking import acquire_lock
from tests.helpers import ALICE, NEW_ID, NOW, SESSION_ID, editor_page, switch_tab


@pytest.fixture
def edit_mode(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return ``adapters.edit_mode``."""
    return import_app_module("adapters.edit_mode")


@pytest.mark.unit
def test__counts__nav_label__tick_or_count(edit_mode: ModuleType) -> None:
    """Sections show a tick when complete, the count of issues otherwise."""
    counts = {"Data Sources": 2, "Use Cases": 1}
    assert edit_mode.nav_label("Basics", counts) == "Basics ✓"
    assert edit_mode.nav_label("Data Sources", counts) == "Data Sources · 2 to fix"
    assert edit_mode.nav_label("Review", counts) == "Review & submit · 3 left"
    assert edit_mode.nav_label("Review", {}) == "Review & submit ✓"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dirty", "issues", "label", "enabled"),
    [
        (False, 0, "Submit for Review", True),
        (True, 0, "Save & submit for review", True),
        (True, 2, "Save & submit for review", False),
        (False, 1, "Submit for Review", False),
    ],
)
def test__state__submit_state__label_and_enabled(
    edit_mode: ModuleType, dirty: bool, issues: int, label: str, enabled: bool
) -> None:
    """Unsaved changes turn Submit into Save & submit; issues disable it."""
    got_label, got_enabled, reason = edit_mode.submit_state(dirty, issues)
    assert (got_label, got_enabled) == (label, enabled)
    assert reason


@pytest.mark.unit
def test__lock__lock_minutes_left__whole_minutes_never_negative(
    edit_mode: ModuleType, alices_draft: MockDataAccess
) -> None:
    """Minutes left until the lock expires; 0 once expired; None without lock."""
    lock = acquire_lock(alices_draft, NEW_ID, ALICE, SESSION_ID, now=NOW).lock
    expires = lock.expires_at
    assert (
        edit_mode.lock_minutes_left(lock, expires - timedelta(minutes=4, seconds=5))
        == 4
    )
    assert edit_mode.lock_minutes_left(lock, expires + timedelta(minutes=1)) == 0
    assert edit_mode.lock_minutes_left(None) is None


@pytest.mark.unit
def test__lock_about_to_expire__open_editor__warning_and_keep_editing_renews(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Close to expiry the editor warns; Keep editing renews the lock."""
    # Given
    at = editor_page(alices_draft).run()
    lock = at.session_state["edit_lock"]
    at.session_state["edit_lock"] = replace(
        lock, expires_at=lock.expires_at - timedelta(minutes=27)
    )
    at.run()
    assert any("expires in" in w.value for w in at.warning)

    # When
    at.button(key="edit_keep_lock").click().run()

    # Then
    assert not at.exception
    assert not any("expires in" in w.value for w in at.warning)


@pytest.mark.unit
def test__editor_basics__add_me_as_sme__appended_to_working_copy(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Add me as SME puts the signed-in user in the SME list."""
    # Given (Alice owns OP-0003; Bob becomes the Owner, so she can add herself)
    at = editor_page(alices_draft).run()
    at.text_input(key="edit_owner_initials").input("BSM").run()

    # When
    at.button(key="edit_sme_me").click().run()

    # Then
    assert not at.exception
    smes = at.session_state["edit_document"].smes
    assert [s["initials"] for s in smes][-1] == ALICE.initials


@pytest.mark.unit
def test__other_owner__click_use_my_details__owner_fields_mine(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """Use my details fills the Owner with the signed-in user."""
    # Given
    at = editor_page(alices_draft).run()
    at.text_input(key="edit_owner_initials").input("BSM").run()
    at.text_input(key="edit_owner_name").input("Bob Smith").run()

    # When
    at.button(key="edit_owner_me").click().run()

    # Then
    assert at.text_input(key="edit_owner_initials").value == ALICE.initials
    assert at.text_input(key="edit_owner_name").value == ALICE.display_name


@pytest.mark.unit
def test__changed_section__open_editor__summary_suggested_then_kept_when_typed(
    alices_draft: MockDataAccess, switched: list[str]
) -> None:
    """The change summary is suggested from the changed sections."""
    # Given
    at = switch_tab(editor_page(alices_draft).run(), "Business Problem")

    # When
    at.text_area(key="edit_problem").input("Scattered data").run()

    # Then
    summary = at.text_input(key="edit_change_summary")
    assert summary.value == "Updated Business Problem Statement"

    # When the user writes their own summary, it is kept
    summary.input("Problem stated").run()
    at.text_area(key="edit_problem").input("Scattered customer data").run()
    assert at.text_input(key="edit_change_summary").value == "Problem stated"
