"""Admin page (UI_Design.md §4.7): service, storage and AppTest smoke tests."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.admin import (
    ADMIN_DENIED_MESSAGE,
    AdminError,
    add_reference_value,
    delete_reference_value,
    get_pending_prs,
    get_reference_values,
    get_status_definitions,
    update_reference_value,
    update_status_definition,
)
from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor
from onepagerapp.workflow import active_reference_values
from tests.conftest import FIXTURES_DIR
from tests.unit.test_lakehouse_writes import (
    NASTY,
    _access,
    _FakeConnection,
    _response,
)

APP_DIR = Path(__file__).resolve().parents[2] / "app"
ADMIN = frozenset({Actor.ADMIN})
USER = resolve_current_user("ada.admin@company.com")
DOMAINS = "ref_business_domains"
SOURCES = "ref_source_systems"


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path))


# ============================================================================
# Service: permissions
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda da, roles: get_reference_values(da, DOMAINS, USER, roles),
        lambda da, roles: add_reference_value(da, DOMAINS, "Risk", 9, USER, roles),
        lambda da, roles: update_reference_value(
            da, DOMAINS, "HR", sort_order=1, active=False, user=USER, roles=roles
        ),
        lambda da, roles: delete_reference_value(da, DOMAINS, "HR", USER, roles),
        lambda da, roles: get_status_definitions(da, "ref_op_status", USER, roles),
        lambda da, roles: update_status_definition(
            da,
            "ref_op_status",
            "Draft",
            display_label="Draft",
            sort_order=1,
            badge_color="#808080",
            user=USER,
            roles=roles,
        ),
    ],
)
def test__admin__only_for_admins(
    data_access: MockDataAccess,
    call: Callable[[MockDataAccess, frozenset[Actor]], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    for roles in (frozenset(), frozenset({Actor.APPROVER})):
        with (
            caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER_NAME),
            pytest.raises(PermissionDeniedError, match=ADMIN_DENIED_MESSAGE),
        ):
            call(data_access, roles)
    assert "action=administer outcome=permission_denied user=AA" in caplog.messages
    assert "Risk" not in list(data_access.get_ref_business_domains()["domain"])


# ============================================================================
# Service: reference data
# ============================================================================


@pytest.mark.unit
def test__reference_values__with_usage(data_access: MockDataAccess) -> None:
    values = get_reference_values(data_access, DOMAINS, USER, ADMIN)

    assert [v.value for v in values][:3] == ["Finance", "Operations", "HR"]
    by_value = {v.value: v for v in values}
    registry = data_access.get_registry_status_counts
    from onepagerapp.models import RegistryFilter  # noqa: PLC0415

    assert by_value["Customer"].in_use == sum(
        registry(RegistryFilter(domain="Customer")).values()
    )
    assert by_value["Customer"].in_use > 0
    assert by_value["HR"].in_use == 0
    assert all(v.active for v in values)

    sources = get_reference_values(data_access, SOURCES, USER, ADMIN)
    assert "SAP ERP" in [v.value for v in sources]
    assert {v.in_use for v in sources} == {None}


@pytest.mark.unit
def test__add_reference_value(
    data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        stored = add_reference_value(
            data_access, DOMAINS, "  Risk   &  <b>Compliance</b> ", 8, USER, ADMIN
        )

    assert stored == "Risk & Compliance"
    assert "Risk & Compliance" in active_reference_values(
        data_access.get_ref_business_domains(), "domain"
    )
    assert (
        'action=add_reference_value outcome=success user=AA '
        'table=ref_business_domains value="Risk & Compliance"' in caplog.messages
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "order", "message"),
    [
        ("", 1, "Enter the business domain."),
        ("<i></i>", 1, "Enter the business domain."),
        ("x" * 101, 1, "at most 100 characters"),
        ("finance", 1, "already exists"),
        ("Risk", 0, "from 1 to 9999"),
    ],
)
def test__add_reference_value__refused(
    data_access: MockDataAccess, value: str, order: int, message: str
) -> None:
    before = len(data_access.get_ref_business_domains())
    with pytest.raises(AdminError, match=message):
        add_reference_value(data_access, DOMAINS, value, order, USER, ADMIN)
    assert len(data_access.get_ref_business_domains()) == before


@pytest.mark.unit
def test__deactivated_value_is_no_longer_offered(data_access: MockDataAccess) -> None:
    update_reference_value(
        data_access,
        DOMAINS,
        "Finance",
        sort_order=20,
        active=False,
        user=USER,
        roles=ADMIN,
    )

    frame = data_access.get_ref_business_domains()
    assert "Finance" in list(frame["domain"])
    assert "Finance" not in active_reference_values(frame, "domain")
    values = get_reference_values(data_access, DOMAINS, USER, ADMIN)
    finance = next(v for v in values if v.value == "Finance")
    assert (finance.sort_order, finance.active) == (20, False)

    with pytest.raises(AdminError, match="no longer exists"):
        update_reference_value(
            data_access,
            DOMAINS,
            "Nope",
            sort_order=1,
            active=True,
            user=USER,
            roles=ADMIN,
        )


@pytest.mark.unit
def test__delete_reference_value(data_access: MockDataAccess) -> None:
    with pytest.raises(AdminError, match="Deactivate it instead"):
        delete_reference_value(data_access, DOMAINS, "Customer", USER, ADMIN)
    assert "Customer" in list(data_access.get_ref_business_domains()["domain"])

    delete_reference_value(data_access, DOMAINS, "HR", USER, ADMIN)
    assert "HR" not in list(data_access.get_ref_business_domains()["domain"])

    delete_reference_value(data_access, SOURCES, "Workday", USER, ADMIN)
    assert "Workday" not in list(data_access.get_ref_source_systems()["system_name"])

    with pytest.raises(AdminError, match="no longer exists"):
        delete_reference_value(data_access, DOMAINS, "HR", USER, ADMIN)


@pytest.mark.unit
def test__unknown_reference_table(data_access: MockDataAccess) -> None:
    with pytest.raises(ValueError, match="Unknown reference table"):
        get_reference_values(data_access, "one_pager_status", USER, ADMIN)


# ============================================================================
# Service: status definitions
# ============================================================================


@pytest.mark.unit
def test__status_definitions__update(data_access: MockDataAccess) -> None:
    update_status_definition(
        data_access,
        "ref_dp_status",
        "Active",
        display_label="  Live ",
        sort_order=9,
        badge_color="#00aa55",
        user=USER,
        roles=ADMIN,
    )

    rows = get_status_definitions(data_access, "ref_dp_status", USER, ADMIN)
    active = next(r for r in rows if r.status == "Active")
    assert (active.display_label, active.sort_order, active.badge_color) == (
        "Live",
        9,
        "#00AA55",
    )
    assert rows[-1].status == "Active"  # sorted by the new order
    assert [r.status for r in rows if r.is_terminal] == ["Deprecated", "Cancelled"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "label", "color", "message"),
    [
        ("Draft", "", "#808080", "Enter a display label"),
        ("Draft", "x" * 51, "#808080", "at most 50"),
        ("Draft", "Draft", "red", "must look like"),
        ("Active", "Active", "#808080", "Unknown status"),  # a DP status
    ],
)
def test__status_definitions__refused(
    data_access: MockDataAccess, status: str, label: str, color: str, message: str
) -> None:
    with pytest.raises(AdminError, match=message):
        update_status_definition(
            data_access,
            "ref_op_status",
            status,
            display_label=label,
            sort_order=1,
            badge_color=color,
            user=USER,
            roles=ADMIN,
        )


# ============================================================================
# Lakehouse SQL
# ============================================================================


@pytest.mark.unit
def test__lakehouse__insert_reference_value_is_parameterized() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).insert_reference_value(
        "ref_source_systems", NASTY, sort_order=3, active=True
    )
    statement, params = conn.calls[0]
    assert statement.startswith("INSERT INTO cat.sch.ref_source_systems (system_name")
    assert "WHERE NOT EXISTS" in statement
    assert NASTY not in statement
    assert params == {"value": NASTY, "sort_order": 3, "active": True}

    conn = _FakeConnection([_response(["num_affected_rows"], [["0"]])])
    assert not _access(conn).insert_reference_value(
        "ref_source_systems", "SAP", sort_order=1, active=True
    )


@pytest.mark.unit
def test__lakehouse__update_and_delete_reference_value() -> None:
    conn = _FakeConnection(
        [
            _response(["num_affected_rows"], [["1"]]),
            _response(["num_affected_rows"], [["0"]]),
        ]
    )
    access = _access(conn)
    assert access.update_reference_value(
        "ref_business_domains", "HR", sort_order=2, active=False
    )
    assert not access.delete_reference_value("ref_data_product_types", "Augmented")

    update, params = conn.calls[0]
    assert "UPDATE cat.sch.ref_business_domains SET sort_order = :sort_order" in update
    assert "WHERE domain = :value" in update
    assert params == {"value": "HR", "sort_order": 2, "active": False}
    delete, params = conn.calls[1]
    assert delete == "DELETE FROM cat.sch.ref_data_product_types WHERE type = :value"
    assert params == {"value": "Augmented"}


@pytest.mark.unit
def test__lakehouse__update_status_definition() -> None:
    conn = _FakeConnection([_response(["num_affected_rows"], [["1"]])])
    assert _access(conn).update_status_definition(
        "ref_op_status",
        "Draft",
        display_label="Draft",
        sort_order=1,
        badge_color="#808080",
    )
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.ref_op_status SET display_label")
    assert "WHERE status = :status" in statement
    assert params["badge_color"] == "#808080"


@pytest.mark.unit
def test__lakehouse__only_known_tables() -> None:
    access = _access(_FakeConnection())
    with pytest.raises(ValueError, match="Unknown reference table"):
        access.delete_reference_value("one_pager_status; DROP TABLE x", "a")
    with pytest.raises(ValueError, match="Unknown status table"):
        access.update_status_definition(
            "ref_business_domains",
            "a",
            display_label="a",
            sort_order=1,
            badge_color="#000000",
        )
    assert access._connection.calls == []


# ============================================================================
# Page
# ============================================================================


class _FailingDomains(MockDataAccess):
    def get_ref_business_domains(self) -> NoReturn:
        msg = "warehouse unreachable: secret internals"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _app(data_access: MockDataAccess, roles: frozenset[Actor] = ADMIN) -> AppTest:
    at = AppTest.from_file(str(APP_DIR / "views" / "admin.py"), default_timeout=30)
    state = {
        "services_initialized": True,
        "data_access": data_access,
        "current_user": USER.username,
        "current_user_info": USER,
        "current_user_roles": roles,
    }
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__admin_page__not_for_other_users(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access, roles=frozenset({Actor.APPROVER})).run()

    assert not at.exception
    assert at.info[0].value == ADMIN_DENIED_MESSAGE
    assert not at.radio
    assert not at.dataframe


@pytest.mark.unit
def test__admin_page__adds_a_business_domain(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()

    assert not at.exception
    assert at.title[0].value == "Administration"
    assert at.subheader[0].value == "Business Domains"
    table = at.dataframe[0].value
    assert list(table["Value"])[:2] == ["Finance", "Operations"]

    at.text_input(key="admin_add_ref_business_domains").input("Treasury")
    next(b for b in at.button if b.label == "Add").click().run()

    assert not at.exception
    assert at.success[0].value == "Added Treasury."
    assert "Treasury" in list(data_access.get_ref_business_domains()["domain"])


@pytest.mark.unit
def test__admin_page__deactivates_a_value(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()
    at.selectbox(key="admin_edit_ref_business_domains").set_value("HR").run()
    at.checkbox(key="admin_edit_active_ref_business_domains_HR").uncheck()
    at.button(key="admin_save_ref_business_domains").click().run()

    assert not at.exception
    assert at.success[0].value == "Saved HR."
    frame = data_access.get_ref_business_domains()
    assert "HR" not in active_reference_values(frame, "domain")


@pytest.mark.unit
def test__admin_page__delete_disabled_while_in_use(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()
    at.selectbox(key="admin_edit_ref_business_domains").set_value("Customer").run()

    assert at.button(key="admin_delete_ref_business_domains").disabled


@pytest.mark.unit
def test__admin_page__status_definitions(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()
    at.radio(key="admin_section").set_value("Status Definitions").run()

    assert not at.exception
    assert at.subheader[0].value == "Status Definitions"
    at.selectbox(key="admin_status_ref_op_status").set_value("Approved").run()
    at.color_picker(key="admin_status_color_ref_op_status_Approved").set_value("#118844")
    at.button(key="admin_status_save_ref_op_status").click().run()

    assert not at.exception
    assert at.success[0].value == "Saved the status Approved."
    approved = next(
        r
        for r in get_status_definitions(data_access, "ref_op_status", USER, ADMIN)
        if r.status == "Approved"
    )
    assert approved.badge_color == "#118844"


@pytest.mark.unit
def test__admin_page__load_error_hides_internals(
    tmp_path: Path, switched: list[str]
) -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    at = _app(_FailingDomains(store)).run()

    assert not at.exception
    assert at.error[0].value == "Couldn't load this section. Please retry."
    assert at.button(key="admin_retry_ref_business_domains")


@pytest.mark.unit
def test__navigation__admin_page_only_for_admins(switched: list[str]) -> None:
    from app import navigation_entries  # noqa: PLC0415 - needs app/ on sys.path

    def titles(roles: frozenset[Actor]) -> list[str]:
        return [title for _, title in navigation_entries(roles)]

    assert titles(ADMIN)[-1] == "Admin"
    assert "Admin" not in titles(frozenset())
    assert "Admin" not in titles(frozenset({Actor.APPROVER}))


# ============================================================================
# Pending PRs (UI_Design.md §4.7, Backend_Design.md §8)
# ============================================================================


def _mark_pending(
    data_access: MockDataAccess, one_pager_id: str, reviewed_at: datetime | None
) -> None:
    row = data_access._status_rows[one_pager_id]
    data_access._status_rows[one_pager_id] = replace(
        row, pending_pr=True, reviewed_at=reviewed_at, reviewed_by="APR"
    )


@pytest.mark.unit
def test__pending_prs__oldest_approval_first(data_access: MockDataAccess) -> None:
    assert get_pending_prs(data_access, USER, ADMIN) == []

    _mark_pending(data_access, "OP-0002", datetime(2026, 9, 1, tzinfo=UTC))
    _mark_pending(data_access, "OP-0001", datetime(2026, 9, 20, tzinfo=UTC))

    rows = get_pending_prs(data_access, USER, ADMIN)
    assert [r.one_pager_id for r in rows] == ["OP-0002", "OP-0001"]
    assert all(r.pending_pr for r in rows)
    with pytest.raises(PermissionDeniedError):
        get_pending_prs(data_access, USER, frozenset({Actor.APPROVER}))


@pytest.mark.unit
def test__lakehouse__pending_pr_rows() -> None:
    conn = _FakeConnection()
    assert _access(conn).get_pending_pr_rows() == []
    statement, params = conn.calls[0]
    assert "FROM cat.sch.one_pager_status WHERE pending_pr = true" in statement
    assert "ORDER BY reviewed_at ASC" in statement
    assert params == {}


@pytest.mark.unit
def test__admin_page__pending_prs(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()
    at.radio(key="admin_section").set_value("Pending PRs").run()

    assert not at.exception
    assert at.subheader[0].value == "Pending PRs"
    assert "No pending PRs" in at.success[0].value

    _mark_pending(data_access, "OP-0001", datetime(2026, 9, 20, 14, 30, tzinfo=UTC))
    at.run()

    assert not at.exception
    assert any(m.value == "OP-0001" for m in at.markdown)
    assert any(m.value == "2026-09-20 14:30 UTC" for m in at.markdown)
    retry = at.button(key="admin_retry_pr_OP-0001")
    assert retry.disabled
    assert "Git integration" in at.info[0].value
