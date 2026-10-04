"""Admin page (UI_Design.md §4.7): service, storage and AppTest smoke tests."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType

import pytest
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
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import RegistryFilter
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor
from onepagerapp.workflow import active_reference_values
from tests.helpers import (
    ADMIN_ROLES,
    APPROVER_ROLES,
    NASTY,
    affected_rows,
    audit_messages,
    capture_audit,
    failing,
    fake_connection,
    lakehouse_access,
    make_user,
    page_app,
    update_status_row,
)

USER = make_user("ADA", "Ada Admin")
DOMAINS = "ref_business_domains"
SOURCES = "ref_source_systems"
STATUS_DEFINITION = {
    "display_label": "Draft",
    "sort_order": 1,
    "badge_color": "#808080",
}

# Every admin service call, with the roles of the caller as last argument.
ADMIN_CALLS: dict[str, Callable[[MockDataAccess, frozenset[Actor]], object]] = {
    "get_reference_values": lambda da, roles: get_reference_values(
        da, DOMAINS, USER, roles
    ),
    "add_reference_value": lambda da, roles: add_reference_value(
        da, DOMAINS, "Risk", 9, USER, roles
    ),
    "update_reference_value": lambda da, roles: update_reference_value(
        da, DOMAINS, "HR", sort_order=1, active=False, user=USER, roles=roles
    ),
    "delete_reference_value": lambda da, roles: delete_reference_value(
        da, DOMAINS, "HR", USER, roles
    ),
    "get_status_definitions": lambda da, roles: get_status_definitions(
        da, "ref_op_status", USER, roles
    ),
    "update_status_definition": lambda da, roles: update_status_definition(
        da, "ref_op_status", "Draft", **STATUS_DEFINITION, user=USER, roles=roles
    ),
}


def _domains(data_access: MockDataAccess) -> list[str]:
    """Return every business domain, active or not."""
    return list(data_access.get_ref_business_domains()["domain"])


def _active_domains(data_access: MockDataAccess) -> list[str]:
    """Return the business domains offered in forms."""
    return active_reference_values(data_access.get_ref_business_domains(), "domain")


def _admin_page(data_access: MockDataAccess, roles: frozenset = ADMIN_ROLES) -> AppTest:
    """Return the Admin page for Ada with ``roles``."""
    return page_app("admin.py", data_access, USER, roles)


def _mark_pending(
    data_access: MockDataAccess, one_pager_id: str, reviewed_at: datetime
) -> None:
    """Mark a One Pager as approved at ``reviewed_at`` with its PR pending."""
    update_status_row(
        data_access,
        one_pager_id,
        pending_pr=True,
        reviewed_at=reviewed_at,
        reviewed_by="APR",
    )


# ============================================================================
# Service: permissions
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "roles", [frozenset(), APPROVER_ROLES], ids=["viewer", "approver"]
)
@pytest.mark.parametrize("call", ADMIN_CALLS.values(), ids=ADMIN_CALLS.keys())
def test__user_without_admin_role__admin_service__refused_and_logged(
    mock_data_access: MockDataAccess,
    caplog: pytest.LogCaptureFixture,
    call: Callable[[MockDataAccess, frozenset[Actor]], object],
    roles: frozenset[Actor],
) -> None:
    """Every admin call refuses non-admins, logs it and changes nothing."""
    # When / Then
    with (
        caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER_NAME),
        pytest.raises(PermissionDeniedError, match=ADMIN_DENIED_MESSAGE),
    ):
        call(mock_data_access, roles)
    assert "action=administer outcome=permission_denied user=ADA" in caplog.messages
    assert "Risk" not in _domains(mock_data_access)


# ============================================================================
# Service: reference data
# ============================================================================


@pytest.mark.unit
def test__seeded_domains__get_reference_values__sorted_with_usage(
    mock_data_access: MockDataAccess,
) -> None:
    """Domains come in sort order with the number of One Pagers using them."""
    # When
    values = get_reference_values(mock_data_access, DOMAINS, USER, ADMIN_ROLES)

    # Then
    assert [v.value for v in values][:3] == ["Finance", "Operations", "HR"]
    by_value = {v.value: v for v in values}
    counts = mock_data_access.get_registry_status_counts(
        RegistryFilter(domain="Customer")
    )
    assert by_value["Customer"].in_use == sum(counts.values()) > 0
    assert by_value["HR"].in_use == 0
    assert all(v.active for v in values)


@pytest.mark.unit
def test__source_systems__get_reference_values__no_usage_count(
    mock_data_access: MockDataAccess,
) -> None:
    """Source systems are not counted (``in_use`` is None)."""
    # When
    values = get_reference_values(mock_data_access, SOURCES, USER, ADMIN_ROLES)

    # Then
    assert "SAP ERP" in [v.value for v in values]
    assert {v.in_use for v in values} == {None}


@pytest.mark.unit
def test__value_with_html_and_spaces__add_reference_value__sanitized_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """The stored value is sanitized, offered at once and logged."""
    # Given
    capture_audit(caplog)

    # When
    stored = add_reference_value(
        mock_data_access,
        DOMAINS,
        "  Risk   &  <b>Compliance</b> ",
        8,
        USER,
        ADMIN_ROLES,
    )

    # Then
    assert stored == "Risk & Compliance"
    assert "Risk & Compliance" in _active_domains(mock_data_access)
    assert audit_messages(caplog) == [
        "action=add_reference_value outcome=success user=ADA "
        'table=ref_business_domains value="Risk & Compliance"'
    ]


@pytest.mark.unit
def test__admin_changes__reference_and_status_rows__record_the_actor(
    mock_data_access: MockDataAccess,
) -> None:
    """Adds and updates store who changed the row and when."""
    # When
    add_reference_value(mock_data_access, DOMAINS, "Risk", 8, USER, ADMIN_ROLES)
    update_reference_value(
        mock_data_access,
        DOMAINS,
        "HR",
        sort_order=4,
        active=False,
        user=USER,
        roles=ADMIN_ROLES,
    )
    update_status_definition(
        mock_data_access,
        "ref_op_status",
        "Draft",
        **STATUS_DEFINITION,
        user=USER,
        roles=ADMIN_ROLES,
    )

    # Then
    domains = mock_data_access._reference[DOMAINS]
    for value in ("Risk", "HR"):
        row = next(r for r in domains if r["domain"] == value)
        assert row["last_updated_by"] == "ADA"
        assert row["last_updated_at"] is not None
    draft = next(
        r
        for r in mock_data_access._status_definitions["ref_op_status"]
        if r["status"] == "Draft"
    )
    assert draft["last_updated_by"] == "ADA"


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
def test__invalid_value_or_order__add_reference_value__refused(
    mock_data_access: MockDataAccess, value: str, order: int, message: str
) -> None:
    """Blank, too long, duplicate (any case) and out-of-range input is refused."""
    # Given
    before = len(_domains(mock_data_access))

    # When / Then
    with pytest.raises(AdminError, match=message):
        add_reference_value(mock_data_access, DOMAINS, value, order, USER, ADMIN_ROLES)
    assert len(_domains(mock_data_access)) == before


@pytest.mark.unit
def test__value__deactivate__kept_but_no_longer_offered(
    mock_data_access: MockDataAccess,
) -> None:
    """A deactivated value stays in the table with its new order."""
    # When
    update_reference_value(
        mock_data_access,
        DOMAINS,
        "Finance",
        sort_order=20,
        active=False,
        user=USER,
        roles=ADMIN_ROLES,
    )

    # Then
    assert "Finance" in _domains(mock_data_access)
    assert "Finance" not in _active_domains(mock_data_access)
    values = get_reference_values(mock_data_access, DOMAINS, USER, ADMIN_ROLES)
    finance = next(v for v in values if v.value == "Finance")
    assert (finance.sort_order, finance.active) == (20, False)


@pytest.mark.unit
def test__unknown_value__update_reference_value__refused(
    mock_data_access: MockDataAccess,
) -> None:
    """Updating a value that no longer exists is refused."""
    # When / Then
    with pytest.raises(AdminError, match="no longer exists"):
        update_reference_value(
            mock_data_access,
            DOMAINS,
            "Nope",
            sort_order=1,
            active=True,
            user=USER,
            roles=ADMIN_ROLES,
        )


@pytest.mark.unit
def test__value_in_use__delete_reference_value__refused(
    mock_data_access: MockDataAccess,
) -> None:
    """A value used by a One Pager is deactivated, not deleted."""
    # When / Then
    with pytest.raises(AdminError, match="Deactivate it instead"):
        delete_reference_value(mock_data_access, DOMAINS, "Customer", USER, ADMIN_ROLES)
    assert "Customer" in _domains(mock_data_access)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("table", "value", "column"),
    [(DOMAINS, "HR", "domain"), (SOURCES, "Workday", "system_name")],
)
def test__unused_value__delete_reference_value__removed(
    mock_data_access: MockDataAccess, table: str, value: str, column: str
) -> None:
    """An unused value is deleted."""
    # When
    delete_reference_value(mock_data_access, table, value, USER, ADMIN_ROLES)

    # Then
    rows = mock_data_access._reference[table]
    assert value not in [r[column] for r in rows]


@pytest.mark.unit
def test__deleted_value__delete_again__refused(
    mock_data_access: MockDataAccess,
) -> None:
    """Deleting a value twice reports that it no longer exists."""
    # Given
    delete_reference_value(mock_data_access, DOMAINS, "HR", USER, ADMIN_ROLES)

    # When / Then
    with pytest.raises(AdminError, match="no longer exists"):
        delete_reference_value(mock_data_access, DOMAINS, "HR", USER, ADMIN_ROLES)


@pytest.mark.unit
def test__table_that_is_not_reference_data__get_reference_values__raises(
    mock_data_access: MockDataAccess,
) -> None:
    """Only the reference tables can be administered."""
    # When / Then
    with pytest.raises(ValueError, match="Unknown reference table"):
        get_reference_values(mock_data_access, "one_pager_status", USER, ADMIN_ROLES)


# ============================================================================
# Service: status definitions
# ============================================================================


@pytest.mark.unit
def test__new_label_order_color__update_status_definition__stored_and_resorted(
    mock_data_access: MockDataAccess,
) -> None:
    """The label is trimmed, the colour upper-cased and the list re-sorted."""
    # When
    update_status_definition(
        mock_data_access,
        "ref_dp_status",
        "Active",
        display_label="  Live ",
        sort_order=9,
        badge_color="#00aa55",
        user=USER,
        roles=ADMIN_ROLES,
    )

    # Then
    rows = get_status_definitions(mock_data_access, "ref_dp_status", USER, ADMIN_ROLES)
    active = next(r for r in rows if r.status == "Active")
    assert (active.display_label, active.sort_order, active.badge_color) == (
        "Live",
        9,
        "#00AA55",
    )
    assert rows[-1].status == "Active"
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
def test__invalid_definition__update_status_definition__refused(
    mock_data_access: MockDataAccess,
    status: str,
    label: str,
    color: str,
    message: str,
) -> None:
    """Blank or long labels, bad colours and unknown statuses are refused."""
    # When / Then
    with pytest.raises(AdminError, match=message):
        update_status_definition(
            mock_data_access,
            "ref_op_status",
            status,
            display_label=label,
            sort_order=1,
            badge_color=color,
            user=USER,
            roles=ADMIN_ROLES,
        )


# ============================================================================
# Pending PRs (UI_Design.md §4.7, Backend_Design.md §8)
# ============================================================================


@pytest.mark.unit
def test__no_pending_pr__get_pending_prs__empty(
    mock_data_access: MockDataAccess,
) -> None:
    """Without pending PRs the list is empty."""
    # When
    rows = get_pending_prs(mock_data_access, USER, ADMIN_ROLES)

    # Then
    assert rows == []


@pytest.mark.unit
def test__two_pending_prs__get_pending_prs__oldest_approval_first(
    mock_data_access: MockDataAccess,
) -> None:
    """Pending PRs are listed by approval time."""
    # Given
    _mark_pending(mock_data_access, "OP-0002", datetime(2026, 9, 1, tzinfo=UTC))
    _mark_pending(mock_data_access, "OP-0001", datetime(2026, 9, 20, tzinfo=UTC))

    # When
    rows = get_pending_prs(mock_data_access, USER, ADMIN_ROLES)

    # Then
    assert [r.one_pager_id for r in rows] == ["OP-0002", "OP-0001"]
    assert all(r.pending_pr for r in rows)


@pytest.mark.unit
def test__approver__get_pending_prs__refused(mock_data_access: MockDataAccess) -> None:
    """Pending PRs are for Admins only."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        get_pending_prs(mock_data_access, USER, APPROVER_ROLES)


# ============================================================================
# Lakehouse SQL
# ============================================================================


@pytest.mark.unit
def test__new_value__lakehouse_insert_reference_value__guarded_bound_insert() -> None:
    """The INSERT binds every value and only runs if the value is new."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    inserted = lakehouse_access(conn).insert_reference_value(
        SOURCES, NASTY, sort_order=3, active=True, user_initials="ADA"
    )

    # Then
    assert inserted
    statement, params = conn.calls[0]
    assert statement.startswith("INSERT INTO cat.sch.ref_source_systems (system_name")
    assert "last_updated_by, last_updated_at)" in statement
    assert ":user_initials, current_timestamp()" in statement
    assert "WHERE NOT EXISTS" in statement
    assert NASTY not in statement
    assert params == {
        "value": NASTY,
        "sort_order": 3,
        "active": True,
        "user_initials": "ADA",
    }


@pytest.mark.unit
def test__existing_value__lakehouse_insert_reference_value__false() -> None:
    """No row inserted means the value already existed."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    inserted = lakehouse_access(conn).insert_reference_value(
        SOURCES, "SAP", sort_order=1, active=True, user_initials="ADA"
    )

    # Then
    assert not inserted


@pytest.mark.unit
def test__value__lakehouse_update_reference_value__bound_update_with_actor() -> None:
    """The UPDATE binds the value, order, flag and actor."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    updated = lakehouse_access(conn).update_reference_value(
        DOMAINS, "HR", sort_order=2, active=False, user_initials="ADA"
    )

    # Then
    assert updated
    statement, params = conn.calls[0]
    assert (
        "UPDATE cat.sch.ref_business_domains SET sort_order = :sort_order" in statement
    )
    assert "last_updated_by = :user_initials" in statement
    assert "last_updated_at = current_timestamp()" in statement
    assert "WHERE domain = :value" in statement
    assert params == {
        "value": "HR",
        "sort_order": 2,
        "active": False,
        "user_initials": "ADA",
    }


@pytest.mark.unit
def test__missing_value__lakehouse_delete_reference_value__false_bound_delete() -> None:
    """The DELETE binds the value; no row deleted gives False."""
    # Given
    conn = fake_connection([affected_rows(0)])

    # When
    deleted = lakehouse_access(conn).delete_reference_value(
        "ref_data_product_types", "Augmented"
    )

    # Then
    assert not deleted
    statement, params = conn.calls[0]
    assert statement == "DELETE FROM cat.sch.ref_data_product_types WHERE type = :value"
    assert params == {"value": "Augmented"}


@pytest.mark.unit
def test__definition__lakehouse_update_status_definition__bound_update() -> None:
    """The status definition UPDATE binds every column and the actor."""
    # Given
    conn = fake_connection([affected_rows(1)])

    # When
    updated = lakehouse_access(conn).update_status_definition(
        "ref_op_status", "Draft", **STATUS_DEFINITION, user_initials="ADA"
    )

    # Then
    assert updated
    statement, params = conn.calls[0]
    assert statement.startswith("UPDATE cat.sch.ref_op_status SET display_label")
    assert "last_updated_by = :user_initials" in statement
    assert "last_updated_at = current_timestamp()" in statement
    assert "WHERE status = :status" in statement
    assert (params["badge_color"], params["user_initials"]) == ("#808080", "ADA")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("write", "message"),
    [
        (
            lambda a: a.delete_reference_value("one_pager_status; DROP TABLE x", "a"),
            "Unknown reference table",
        ),
        (
            lambda a: a.update_status_definition(
                DOMAINS, "a", **STATUS_DEFINITION, user_initials="ADA"
            ),
            "Unknown status table",
        ),
    ],
    ids=["reference", "status"],
)
def test__unknown_table__lakehouse_admin_write__raises_without_sql(
    write: Callable, message: str
) -> None:
    """Table names are checked against a fixed list before any SQL runs."""
    # Given
    conn = fake_connection()

    # When / Then
    with pytest.raises(ValueError, match=message):
        write(lakehouse_access(conn))
    assert conn.calls == []


@pytest.mark.unit
def test__pending_rows__lakehouse_get_pending_pr_rows__oldest_approval_first() -> None:
    """Pending PRs are read in approval order."""
    # Given
    conn = fake_connection()

    # When
    rows = lakehouse_access(conn).get_pending_pr_rows()

    # Then
    assert rows == []
    statement, params = conn.calls[0]
    assert "FROM cat.sch.one_pager_status WHERE pending_pr = true" in statement
    assert "ORDER BY reviewed_at ASC" in statement
    assert params == {}


# ============================================================================
# Page
# ============================================================================


@pytest.mark.unit
def test__approver__open_admin_page__denied_without_content(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Non-admins see the denial message and no admin controls."""
    # When
    at = _admin_page(mock_data_access, roles=APPROVER_ROLES).run()

    # Then
    assert not at.exception
    assert at.info[0].value == ADMIN_DENIED_MESSAGE
    assert not at.radio
    assert not at.dataframe


@pytest.mark.unit
def test__admin__open_admin_page__business_domains_listed(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The page opens on the Business Domains table."""
    # When
    at = _admin_page(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.title[0].value == "Administration"
    assert at.subheader[0].value == "Business Domains"
    assert list(at.dataframe[0].value["Value"])[:2] == ["Finance", "Operations"]


@pytest.mark.unit
def test__new_domain_entered__click_add__stored_with_success(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Adding a domain stores it and confirms."""
    # Given
    at = _admin_page(mock_data_access).run()
    at.text_input(key="admin_add_ref_business_domains").input("Treasury")

    # When
    next(b for b in at.button if b.label == "Add").click().run()

    # Then
    assert not at.exception
    assert at.success[0].value == "Added Treasury."
    assert "Treasury" in _domains(mock_data_access)


@pytest.mark.unit
def test__active_unchecked__click_save__value_deactivated(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Saving with Active unchecked deactivates the value."""
    # Given
    at = _admin_page(mock_data_access).run()
    at.selectbox(key="admin_edit_ref_business_domains").set_value("HR").run()
    at.checkbox(key="admin_edit_active_ref_business_domains_HR").uncheck()

    # When
    at.button(key="admin_save_ref_business_domains").click().run()

    # Then
    assert not at.exception
    assert at.success[0].value == "Saved HR."
    assert "HR" not in _active_domains(mock_data_access)


@pytest.mark.unit
def test__value_in_use__select_it__delete_disabled(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A value in use cannot be deleted."""
    # Given
    at = _admin_page(mock_data_access).run()

    # When
    at.selectbox(key="admin_edit_ref_business_domains").set_value("Customer").run()

    # Then
    assert at.button(key="admin_delete_ref_business_domains").disabled


@pytest.mark.unit
def test__status_definitions_section__save_new_color__stored(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A status badge colour is edited on the Status Definitions section."""
    # Given
    at = _admin_page(mock_data_access).run()
    at.radio(key="admin_section").set_value("Status Definitions").run()
    assert at.subheader[0].value == "Status Definitions"
    at.selectbox(key="admin_status_ref_op_status").set_value("Approved").run()
    at.color_picker(key="admin_status_color_ref_op_status_Approved").set_value(
        "#118844"
    )

    # When
    at.button(key="admin_status_save_ref_op_status").click().run()

    # Then
    assert not at.exception
    assert at.success[0].value == "Saved the status Approved."
    approved = next(
        r
        for r in get_status_definitions(
            mock_data_access, "ref_op_status", USER, ADMIN_ROLES
        )
        if r.status == "Approved"
    )
    assert approved.badge_color == "#118844"


@pytest.mark.unit
def test__domains_fail_to_load__open_admin_page__friendly_error_with_retry(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A load error hides the internals and offers a retry."""
    # Given
    monkeypatch.setattr(
        mock_data_access,
        "get_ref_business_domains",
        failing("warehouse unreachable: secret internals"),
    )

    # When
    at = _admin_page(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.error[0].value == "Couldn't load this section. Please retry."
    assert at.button(key="admin_retry_ref_business_domains")


@pytest.mark.unit
def test__no_pending_pr__pending_prs_section__all_clear(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Without pending PRs the section says so."""
    # Given
    at = _admin_page(mock_data_access).run()

    # When
    at.radio(key="admin_section").set_value("Pending PRs").run()

    # Then
    assert not at.exception
    assert at.subheader[0].value == "Pending PRs"
    assert "No pending PRs" in at.success[0].value


@pytest.mark.unit
def test__pending_pr__pending_prs_section__listed_with_disabled_retry(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A pending PR shows its One Pager and approval time; retry needs Git."""
    # Given
    at = _admin_page(mock_data_access).run()
    at.radio(key="admin_section").set_value("Pending PRs").run()
    _mark_pending(
        mock_data_access, "OP-0001", datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    )

    # When
    at.run()

    # Then
    assert not at.exception
    assert any(m.value == "OP-0001" for m in at.markdown)
    assert any(m.value == "2026-09-20 14:30 UTC" for m in at.markdown)
    assert at.button(key="admin_retry_pr_OP-0001").disabled
    assert "Git integration" in at.info[0].value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "shown"),
    [(ADMIN_ROLES, True), (frozenset(), False), (APPROVER_ROLES, False)],
    ids=["admin", "viewer", "approver"],
)
def test__roles__navigation_entries__admin_page_for_admins_only(
    import_app_module: Callable[[str], ModuleType],
    roles: frozenset[Actor],
    shown: bool,
) -> None:
    """Only Admins see the Admin page, as the last entry."""
    # Given
    app = import_app_module("app")

    # When
    titles = [title for _, title in app.navigation_entries(roles)]

    # Then
    assert ("Admin" in titles) is shown
    if shown:
        assert titles[-1] == "Admin"
