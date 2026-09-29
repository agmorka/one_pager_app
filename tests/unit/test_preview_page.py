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
from onepagerapp.state_machine import Actor
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


APPROVER = "cjo@bec.dk"


def _review_services(tmp_path: Path, username: str = APPROVER) -> dict:
    services = _services(tmp_path)
    user = resolve_current_user(username)
    return {
        **services,
        "current_user": user.username,
        "current_user_info": user,
        "current_user_roles": frozenset({Actor.APPROVER}),
        "preview_one_pager_id": "OP-0002",
        "preview_review_mode": "OP-0002",
    }


@pytest.mark.unit
def test__preview__review_mode_for_approver(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_review_services(tmp_path)).run()

    assert not at.exception
    assert any("Review mode" in i.value for i in at.info)
    keys = {b.key for b in at.button}
    assert {"preview_reject", "preview_approve"} <= keys
    assert not at.button(key="preview_reject").disabled

    at.button(key="preview_back_to_queue").click().run()
    assert switched == ["views/review.py"]
    assert "preview_review_mode" not in at.session_state


@pytest.mark.unit
def test__preview__no_review_actions_for_owner_or_sme(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_review_services(tmp_path, "dp@bec.dk")).run()  # SME of OP-0002

    assert not at.exception
    keys = {b.key for b in at.button}
    assert "preview_reject" not in keys
    assert "preview_approve" not in keys


@pytest.mark.unit
def test__preview__reject_opens_the_dialog(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_review_services(tmp_path)).run()
    at.button(key="preview_reject").click().run()

    assert not at.exception
    assert at.text_area(key="preview_reject_reason")  # the dialog is open


@pytest.mark.unit
def test__preview__approve_opens_the_dialog(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_review_services(tmp_path)).run()
    assert not at.button(key="preview_approve").disabled
    at.button(key="preview_approve").click().run()

    assert not at.exception
    assert any("v1.0.0" in m.value for m in at.markdown)  # the dialog is open
    assert any("Ready for Development" in m.value for m in at.markdown)


@pytest.mark.unit
def test__preview__add_comment_opens_the_dialog(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_review_services(tmp_path)).run()
    at.button(key="preview_add_comment").click().run()

    assert not at.exception
    assert at.selectbox(key="preview_comment_section").value is None  # Whole document
    assert at.text_area(key="preview_comment_text")


@pytest.mark.unit
def test__preview__owner_resolves_review_comments(
    tmp_path: Path, switched: list[str]
) -> None:
    from onepagerapp.workflow import reject_one_pager  # noqa: PLC0415

    state = _review_services(tmp_path, "bob.smith@company.com")  # Owner
    data_access = state["data_access"]
    approver = resolve_current_user(APPROVER)
    reject_one_pager(
        data_access, "OP-0002", approver, "Needs work", roles={Actor.APPROVER}
    )
    [comment] = data_access.get_review_comments("OP-0002")

    at = _app(state).run()
    assert not at.exception
    resolve = at.button(key=f"preview_resolve_{comment.id}")
    assert not resolve.disabled
    resolve.click().run()

    assert not at.exception
    assert data_access.get_review_comments("OP-0002")[0].resolved_by == "BS"
    assert "The comment was marked as resolved." in [s.value for s in at.success]
    assert not [b for b in at.button if b.key == f"preview_resolve_{comment.id}"]


@pytest.mark.unit
def test__preview__viewers_cannot_resolve(
    tmp_path: Path, switched: list[str]
) -> None:
    from onepagerapp.workflow import reject_one_pager  # noqa: PLC0415

    state = _review_services(tmp_path, MAJA)  # neither Owner nor SME
    reject_one_pager(
        state["data_access"],
        "OP-0002",
        resolve_current_user(APPROVER),
        "Needs work",
        roles={Actor.APPROVER},
    )
    at = _app(state).run()

    assert not at.exception
    assert not [b for b in at.button if str(b.key).startswith("preview_resolve_")]
    assert any("Unresolved" in i.value for i in at.info)
