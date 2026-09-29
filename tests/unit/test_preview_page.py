"""AppTest smoke tests for the Preview page states (UI_Design.md §4.4)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import LockInfo
from tests.conftest import FIXTURES_DIR

APP_DIR = Path(__file__).resolve().parents[2] / "app"


class _FailingDataAccess(MockDataAccess):
    def get_one_pager(self, one_pager_id: str) -> NoReturn:  # noqa: ARG002
        msg = "warehouse 4efe1f3d3f86e320 unreachable: secret internals"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _services(tmp_path: Path, data_access_cls: type = MockDataAccess) -> dict:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    user = resolve_current_user("alice.brown@company.com")
    return {
        "services_initialized": True,
        "data_access": data_access_cls(store),
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
    }


def _app(state: dict) -> AppTest:
    at = AppTest.from_file(str(APP_DIR / "views" / "preview.py"), default_timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__preview__without_id_does_not_default_to_a_one_pager(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_services(tmp_path)).run()

    assert not at.exception
    assert "No One Pager selected" in at.info[0].value
    assert "preview_one_pager_id" not in at.session_state
    assert all(t.value != "OP-0001" for t in at.title)

    at.button(key="preview_go_to_registry").click().run()
    assert switched == ["views/registry.py"]


@pytest.mark.unit
def test__preview__with_id_renders_one_pager(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app({**_services(tmp_path), "preview_one_pager_id": "OP-0001"}).run()

    assert not at.exception
    assert not at.error
    assert at.title


@pytest.mark.unit
def test__preview__load_error_is_friendly_with_retry(
    tmp_path: Path, switched: list[str]
) -> None:
    state = {
        **_services(tmp_path, _FailingDataAccess),
        "preview_one_pager_id": "OP-0001",
    }
    at = _app(state).run()

    assert not at.exception
    assert at.error[0].value == "Couldn't load this One Pager. Please retry."
    page_text = " ".join(e.value for e in at.error)
    assert "secret internals" not in page_text
    assert "RuntimeError" not in page_text

    retry = at.button(key="preview_retry_load")
    assert retry.label == "Retry"
    retry.click().run()
    assert not at.exception
    assert at.error[0].value == "Couldn't load this One Pager. Please retry."


def _expander(at: AppTest, label: str):  # noqa: ANN202
    return next(e for e in at.expander if e.label == label)


@pytest.mark.unit
def test__preview__renders_every_content_section(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app({**_services(tmp_path), "preview_one_pager_id": "OP-0001"}).run()

    assert not at.exception
    labels = [e.label for e in at.expander]
    for label in (
        "📝 Description",
        "🎯 Business Problem Statement",
        "💼 Use Cases",
        "✅ Business Requirements",
        "📊 Data Sources",
        "🔍 Data Product Preview",
        "🔐 Classification",
        "🏛️ Governance",
        "🧭 Scope & Questions",
    ):
        assert label in labels


@pytest.mark.unit
def test__preview__use_cases_resolved_with_ids(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app({**_services(tmp_path), "preview_one_pager_id": "OP-0001"}).run()

    table = _expander(at, "💼 Use Cases").dataframe[0].value
    assert list(table["ID"]) == ["UC-001", "UC-002"]
    assert list(table["Persona"]) == ["Analytics Manager", "Compliance Officer"]


@pytest.mark.unit
def test__preview__unknown_use_case_still_shows_its_id(
    tmp_path: Path, switched: list[str]
) -> None:
    services = _services(tmp_path)
    services["data_access"].get_use_case = lambda _id: None
    at = _app({**services, "preview_one_pager_id": "OP-0001"}).run()

    table = _expander(at, "💼 Use Cases").dataframe[0].value
    assert list(table["ID"]) == ["UC-001", "UC-002"]
    assert set(table["Persona"]) == {"(not available)"}


@pytest.mark.unit
def test__preview__requirement_ids_and_new_sections_shown(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app({**_services(tmp_path), "preview_one_pager_id": "OP-0001"}).run()

    requirements = _expander(at, "✅ Business Requirements").dataframe[0].value
    assert list(requirements["ID"]) == ["BR-001", "BR-002"]

    governance = _expander(at, "🏛️ Governance")
    assert len(governance.dataframe) == 3
    assert list(governance.dataframe[1].value["Dimension"]) == [
        "Uniqueness",
        "Validity",
    ]

    scope = _expander(at, "🧭 Scope & Questions")
    markdown = " ".join(m.value for m in scope.markdown)
    assert "Corporate customers" in markdown
    assert "SAP ERP remains the system of record" in markdown
    assert list(scope.dataframe[0].value["Status"]) == ["Answered"]

    retention = _expander(at, "🔐 Classification").dataframe[0].value
    assert list(retention["Legal Basis"]) == ["Danish Bookkeeping Act"]


@pytest.mark.unit
def test__preview__v1_document_uses_legacy_fields(
    tmp_path: Path, switched: list[str]
) -> None:
    services = _services(tmp_path)
    rows = services["data_access"]._status_rows
    rows["OP-0001"] = replace(rows["OP-0001"], version="0.2.0")
    at = _app({**services, "preview_one_pager_id": "OP-0001"}).run()

    assert not at.exception
    use_cases = _expander(at, "💼 Use Cases").dataframe[0].value
    assert list(use_cases["Persona"]) == ["Analytics Manager", "Compliance Officer"]
    requirements = _expander(at, "✅ Business Requirements").dataframe[0].value
    assert requirements["Requirement"][0].startswith("Person records must be updated")
    sources = _expander(at, "📊 Data Sources").dataframe[0].value
    assert list(sources["Source System"]) == ["Enterprise System", "SaaS Application"]
    elements = _expander(at, "🔍 Data Product Preview").dataframe[0].value
    assert "person_id" in list(elements["Element"])
    classification = _expander(at, "🔐 Classification")
    assert any("7 years" in m.value for m in classification.markdown)


ALICE = "alice.brown@company.com"
MAJA = "MJOADM@BECOC001.onmicrosoft.com"


def _lock_services(tmp_path: Path, holder: str, expires_in: timedelta) -> dict:
    services = _services(tmp_path)
    now = datetime.now(UTC)
    holder_user = resolve_current_user(holder)
    services["data_access"]._locks["OP-0001"] = LockInfo(
        one_pager_id="OP-0001",
        locked_by_initials=holder_user.initials,
        locked_by_name=holder_user.display_name,
        session_id="other-session",
        acquired_at=now - timedelta(minutes=5),
        last_heartbeat=now - timedelta(minutes=5),
        expires_at=now + expires_in,
    )
    return {**services, "preview_one_pager_id": "OP-0001"}


@pytest.mark.unit
def test__preview__own_lock_can_be_released(
    tmp_path: Path, switched: list[str]
) -> None:
    state = _lock_services(tmp_path, ALICE, timedelta(minutes=25))
    at = _app(state).run()

    assert not at.exception
    assert any("Locked by you" in i.value for i in at.info)
    at.button(key="preview_release_lock").click().run()

    assert not at.exception
    assert state["data_access"].get_lock("OP-0001") is None
    assert "Your lock was released." in [s.value for s in at.success]
    assert not any("Locked by you" in i.value for i in at.info)


@pytest.mark.unit
def test__preview__lock_of_other_user_is_read_only(
    tmp_path: Path, switched: list[str]
) -> None:
    state = _lock_services(tmp_path, MAJA, timedelta(minutes=25))
    at = _app(state).run()

    assert not at.exception
    assert any("Locked by MJO** (MJO)" in w.value for w in at.warning)
    assert not [b for b in at.button if b.key == "preview_release_lock"]


@pytest.mark.unit
def test__preview__expired_lock_is_not_shown(
    tmp_path: Path, switched: list[str]
) -> None:
    state = _lock_services(tmp_path, MAJA, -timedelta(minutes=1))
    at = _app(state).run()

    assert not at.exception
    assert not [w for w in at.warning if "Locked by" in w.value]
