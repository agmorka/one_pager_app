"""Shared test data, builders and fakes.

Fixtures live in ``conftest.py``; this module holds plain constants and
functions that tests import directly.

Test users have fixed corporate initials, so tests do not depend on the
configured username format (``auth.py``). The sample data
(``tests/fixtures`` and ``MockDataAccess``) uses these people:

- ``ABR`` Alice Brown: Owner of OP-0001
- ``BSM`` Bob Smith: Owner of OP-0002
- ``CDA`` Charlie Davis: SME of OP-0001
- ``DPI`` Diana Prince: SME of OP-0002
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

from streamlit.testing.v1 import AppTest

from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.config import AppConfig, AppMode
from onepagerapp.data_access.connection import Identity
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import SaveResult, save_draft
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    CurrentUser,
    LockInfo,
    OnePagerDocument,
    OnePagerStatusRow,
    RegistryFilter,
    ReviewComment,
    UseCaseFilter,
    UseCaseInput,
)
from onepagerapp.state_machine import Actor

# ============================================================================
# Paths and fixed values
# ============================================================================

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures" / "sample_one_pagers"
APP_DIR = TESTS_DIR.parent / "app"
SCHEMAS_DIR = TESTS_DIR.parent / "schemas"

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
SESSION_ID = "s1"

APPROVED_ID = "OP-0001"  # seeded Approved / Ready for Development, v1.0.0
IN_REVIEW_ID = "OP-0002"  # seeded In Review / In Definition, v0.3.0
NEW_ID = "OP-0003"  # the next ID: the mock seeds OP-0001..OP-0002

DOMAINS = ["Customer", "Sales"]
TYPES = ["Foundational", "Integrated", "Augmented"]
INTERIM_GROUP_DEV = "BEC_BECOC001_LHX_DEV_DataPlatEng"

# SQL injection probe: it must be bound as a parameter, never interpolated.
NASTY = "x'); DROP TABLE one_pager_status; --"

# ============================================================================
# Users and roles
# ============================================================================

CREATOR_ROLES = frozenset({Actor.OWNER_SME_GROUP})
APPROVER_ROLES = frozenset({Actor.APPROVER})
ADMIN_ROLES = frozenset({Actor.ADMIN})
ALL_GROUP_ROLES = frozenset({Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN})


def make_user(initials: str, display_name: str | None = None) -> CurrentUser:
    """Return a signed-in user with the given corporate initials."""
    return CurrentUser(
        username=f"{initials.lower()}adm@becoc001.onmicrosoft.com",
        initials=initials,
        display_name=display_name or initials,
    )


ALICE = make_user("ABR", "Alice Brown")  # Owner of OP-0001
BOB = make_user("BSM", "Bob Smith")  # Owner of OP-0002
DIANA = make_user("DPI", "Diana Prince")  # SME of OP-0002
MAJA = make_user("MJO")  # creator and Owner of a new One Pager (valid_input)
APPROVER = make_user("CJO")  # Owner or SME of no seeded One Pager
UNRECOGNISED = CurrentUser("guest@example.com", "", "Guest")

# ============================================================================
# Builders
# ============================================================================


def config(**settings: Any) -> AppConfig:  # noqa: ANN401 - any AppConfig setting
    """Return an ``AppConfig`` on a dummy volume with the given settings."""
    return AppConfig(**{"ONE_PAGER_APP_VOLUME_PATH": "/Volumes/x", **settings})


def mock_data_access(write_path: Path, **kwargs: Any) -> MockDataAccess:  # noqa: ANN401
    """Return mock data on the read-only fixtures, writing under ``write_path``."""
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=write_path)
    return MockDataAccess(store, **kwargs)


def update_status_row(
    data_access: MockDataAccess,
    one_pager_id: str,
    **changes: Any,  # noqa: ANN401 - any status row column
) -> OnePagerStatusRow:
    """Change columns of a stored status row, as if written by someone else."""
    row = replace(data_access._status_rows[one_pager_id], **changes)
    data_access._status_rows[one_pager_id] = row
    return row


def make_lock(
    one_pager_id: str = APPROVED_ID,
    holder: str = "ABR",
    *,
    name: str | None = None,
    session_id: str = SESSION_ID,
    acquired_at: datetime = NOW,
    expires_at: datetime = NOW,
) -> LockInfo:
    """Return a lock row held by ``holder``."""
    return LockInfo(
        one_pager_id=one_pager_id,
        locked_by_initials=holder,
        locked_by_name=name or holder,
        session_id=session_id,
        acquired_at=acquired_at,
        last_heartbeat=acquired_at,
        expires_at=expires_at,
    )


def live_lock(
    one_pager_id: str, holder: CurrentUser, expires_in: timedelta
) -> LockInfo:
    """Return a lock taken five minutes ago (real clock) that ends in ``expires_in``.

    A negative ``expires_in`` gives an expired lock.
    """
    now = datetime.now(UTC)
    return make_lock(
        one_pager_id,
        holder.initials,
        name=holder.display_name,
        session_id="other-session",
        acquired_at=now - timedelta(minutes=5),
        expires_at=now + expires_in,
    )


def status_row(**changes: Any) -> OnePagerStatusRow:  # noqa: ANN401
    """Return a Draft status row of OP-0007, with ``changes`` applied."""
    row = OnePagerStatusRow(
        one_pager_id="OP-0007",
        data_product="customer_master",
        product_name="P",
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.2.0",
        owner_name="A",
        owner_initials="ABR",
        owner_email="a@b.dk",
        owner_team=None,
        created_by="ABR",
        created_at=NOW,
        last_updated_at=NOW,
        last_updated_by="ABR",
        structure_definition="structure_one_pager_v_2.json",
    )
    return replace(row, **changes)


def change_log_entry(**changes: Any) -> ChangeLogEntry:  # noqa: ANN401
    """Return a change log entry of OP-0007, with ``changes`` applied."""
    entry = ChangeLogEntry(
        id=0,
        one_pager_id="OP-0007",
        version="0.1.0",
        event_type="creation",
        author_initials="ABR",
        author_name="A B",
        summary="s",
        created_at=NOW,
    )
    return replace(entry, **changes)


def review_comment(**changes: Any) -> ReviewComment:  # noqa: ANN401
    """Return an unresolved whole-document review comment on OP-0002."""
    comment = ReviewComment(
        id=0,
        one_pager_id=IN_REVIEW_ID,
        version="0.3.0",
        section=None,
        reviewer_initials="CJO",
        reviewer_name="Cjo",
        comment="c",
        resolved=False,
        created_at=NOW,
    )
    return replace(comment, **changes)


def fill_all_sections(doc: OnePagerDocument) -> None:
    """Fill ``doc`` with content that passes the strict tier, as the editor does."""
    doc.business_problem_statement = "Customer data is scattered."
    doc.use_cases = [{"useCaseId": "UC-001"}]
    doc.business_requirements = [
        {"id": "BR-001", "requirement": "Daily refresh", "priority": "High"}
    ]
    doc.data_sources = [{"name": "CRM", "dataProvided": "Customer master data"}]
    doc.data_product_preview = [
        {
            "elementName": "customer_id",
            "dataType": "STRING",
            "isPrimaryKey": True,
            "containsPII": False,
            "isCriticalDataElement": True,
            "cdeCriticalityTiering": "Tier 1",
            "description": "Customer key",
            "useCaseLinks": ["UC-001"],
        },
        {
            "elementName": "segment",
            "dataType": "STRING",
            "isPrimaryKey": False,
            "containsPII": False,
            "isCriticalDataElement": False,
            "description": "Segment",
        },
    ]
    doc.data_classification = {
        "classificationLevel": "Internal",
        "containsPII": True,
        "containsSensitiveData": False,
    }
    doc.retention_requirements = [
        {"dataCategory": "Customer", "retentionPeriod": "5 years"}
    ]
    doc.data_governance_artifacts = {
        "businessConcepts": [{"name": "Customer", "definition": "A client"}],
        "cdeQuality": [
            {"elementName": "customer_id", "dimension": "Uniqueness", "rule": "Unique"}
        ],
        "cdeLineage": [],
    }
    doc.out_of_scope = ["Prospects"]
    doc.open_questions = [{"question": "Which CRM?", "status": "Open"}]
    doc.assumptions = ["CRM is the master"]


def save(
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
    document: OnePagerDocument,
    user: CurrentUser,
    *,
    summary: str = "Clarified the description",
    session_id: str = SESSION_ID,
    one_pager_id: str = NEW_ID,
    allowed_domains: list[str] = DOMAINS,
    now: datetime = LATER,
) -> SaveResult:
    """Save ``document`` as a new Draft version with the usual reference values."""
    return save_draft(
        data_access,
        store,
        one_pager_id,
        document,
        summary,
        user,
        session_id,
        allowed_domains=allowed_domains,
        allowed_types=TYPES,
        now=now,
    )


# ============================================================================
# Fault injection
# ============================================================================


def failing(message: str = "boom", error: type[Exception] = RuntimeError) -> Callable:
    """Return a stand-in for any method that raises ``error(message)``."""

    def fail(*_: object, **__: object) -> NoReturn:
        raise error(message)

    return fail


def failing_for_event(original: Callable, event_type: str) -> Callable:
    """Wrap ``append_change_log`` so it fails for entries of ``event_type`` only."""

    def append(entry: ChangeLogEntry) -> None:
        if entry.event_type == event_type:
            msg = "boom"
            raise RuntimeError(msg)
        original(entry)

    return append


def capture_audit(caplog: Any) -> None:  # noqa: ANN401 - LogCaptureFixture
    """Capture security events from now on; drop the records so far."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    caplog.clear()


def audit_messages(caplog: Any) -> list[str]:  # noqa: ANN401 - LogCaptureFixture
    """Return the messages of the security-event logger, in order."""
    return [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER_NAME]


# ============================================================================
# Fake Statement API connection for LakehouseAccess
# ============================================================================

WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "MERGE")


def statement_response(columns: list[str], rows: list[list]) -> SimpleNamespace:
    """Return an object shaped like a Statement API ``StatementResponse``."""
    return SimpleNamespace(
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns])
        ),
        result=SimpleNamespace(data_array=rows),
    )


def affected_rows(count: int) -> SimpleNamespace:
    """Return the response of a write statement that changed ``count`` rows."""
    return statement_response(["num_affected_rows"], [[str(count)]])


def is_write(statement: str) -> bool:
    """Tell whether ``statement`` changes data."""
    return statement.lstrip().upper().startswith(WRITE_KEYWORDS)


def answer_every_statement(statement: str, _params: dict) -> SimpleNamespace:
    """Answer sequence reads with 4, writes with one row and other reads empty."""
    if statement.startswith("SELECT last_value"):
        return statement_response(["last_value"], [["4"]])
    if is_write(statement):
        return affected_rows(1)
    return statement_response([], [])


def fake_connection(
    responses: list[SimpleNamespace] | None = None,
    *,
    error: Exception | None = None,
    handler: Callable[[str, dict], object] | None = None,
    group_names: dict[str, list[str]] | None = None,
) -> SimpleNamespace:
    """Return a stand-in for ``DatabricksConnection`` that records every statement.

    It raises ``error`` if given, else answers with ``handler`` (which may be
    replaced on the returned object), else replays ``responses`` in order and
    then empty results.
    """
    pending = list(responses or [])
    connection = SimpleNamespace(calls=[], identities=[], handler=handler)

    def execute_statement(
        statement: str,
        parameters: dict[str, Any] | None = None,
        *,
        identity: Identity,
    ) -> object:
        params = dict(parameters or {})
        connection.calls.append((statement, params))
        connection.identities.append(identity)
        if error:
            raise error
        if connection.handler:
            return connection.handler(statement, params)
        return pending.pop(0) if pending else statement_response([], [])

    connection.execute_statement = execute_statement
    connection.find_group_names = lambda name: (group_names or {}).get(name, [])
    return connection


def lakehouse_access(connection: SimpleNamespace) -> LakehouseAccess:
    """Return a ``LakehouseAccess`` on catalog ``cat``, schema ``sch``."""
    access = LakehouseAccess.__new__(LakehouseAccess)
    access._config = AppConfig(
        APP_MODE=AppMode.LOCAL_INTEGRATION,
        ONE_PAGER_APP_VOLUME_PATH="/Volumes/x",
        ONE_PAGER_APP_DATABRICKS_CATALOG="cat",
        ONE_PAGER_APP_DATABRICKS_SCHEMA="sch",
    )
    access._connection = connection
    access._document_store = None
    return access


def assert_not_interpolated(statement: str) -> None:
    """Assert that the injection probe did not reach the SQL text."""
    assert NASTY not in statement
    assert "DROP TABLE" not in statement


# ============================================================================
# Streamlit pages
# ============================================================================


def page_app(
    page: str,
    data_access: MockDataAccess,
    user: CurrentUser | None = ALICE,
    roles: frozenset[Actor] = CREATOR_ROLES,
    **state: Any,  # noqa: ANN401 - any session state value
) -> AppTest:
    """Return an AppTest for ``app/views/<page>`` with the services injected.

    Streamlit 1.38's AppTest does not render pages registered with
    st.navigation, so each page runs on its own with the session state that
    app.py would set up. Without a ``user`` nobody is signed in.
    """
    at = AppTest.from_file(str(APP_DIR / "views" / page), default_timeout=30)
    services: dict[str, Any] = {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": data_access._document_store,
    }
    if user is not None:
        services |= {
            "current_user": user.username,
            "current_user_info": user,
            "current_user_roles": roles,
        }
    for key, value in {**services, **state}.items():
        at.session_state[key] = value
    return at


def button_labelled(at: AppTest, label: str) -> Any:  # noqa: ANN401 - Button
    """Return the first button with ``label``."""
    return next(b for b in at.button if b.label == label)


def markdown_text(at: AppTest) -> str:
    """Return every markdown element of the page, one per line."""
    return "\n".join(m.value for m in at.markdown)


# ============================================================================
# LakehouseAccess methods: one call each
# ============================================================================

ACTOR = "Q9Z"
_USE_CASE = UseCaseInput("p", "g", "s", "d", "High")
_AUTHORIZED = AuthorizedUser("OP-0001", "ABR", "A", "a@b.dk", "owner")

# Read method -> call. read_document is not here: it reads the YAML file
# through the volume mount, which is always the service principal.
LAKEHOUSE_READS: dict[str, Callable[[LakehouseAccess], object]] = {
    "get_current_user": lambda a: a.get_current_user(),
    "get_group_memberships": lambda a: a.get_group_memberships({"admin": "G"}),
    "read_table": lambda a: a.read_table("ref_op_status"),
    "get_ref_op_status": lambda a: a.get_ref_op_status(),
    "get_ref_dp_status": lambda a: a.get_ref_dp_status(),
    "get_ref_business_domains": lambda a: a.get_ref_business_domains(),
    "get_ref_data_product_types": lambda a: a.get_ref_data_product_types(),
    "get_ref_source_systems": lambda a: a.get_ref_source_systems(),
    "get_registry": lambda a: a.get_registry(RegistryFilter(), 1, 20),
    "get_registry_status_counts": lambda a: a.get_registry_status_counts(
        RegistryFilter()
    ),
    "get_one_pager": lambda a: a.get_one_pager("OP-0001"),
    "get_one_pager_status": lambda a: a.get_one_pager_status("OP-0001"),
    "get_change_log": lambda a: a.get_change_log("OP-0001"),
    "get_review_comments": lambda a: a.get_review_comments("OP-0001"),
    "get_lock": lambda a: a.get_lock("OP-0001"),
    "get_locks": lambda a: a.get_locks(["OP-0001", "OP-0002"]),
    "get_one_pager_ids_for_data_product": lambda a: (
        a.get_one_pager_ids_for_data_product("customer_master")
    ),
    "get_authorized_users": lambda a: a.get_authorized_users("OP-0001"),
    "get_one_pager_status_row": lambda a: a.get_one_pager_status_row("OP-0001"),
    "get_one_pager_status_rows": lambda a: a.get_one_pager_status_rows("Draft"),
    "get_pending_pr_rows": lambda a: a.get_pending_pr_rows(),
    "get_use_cases": lambda a: a.get_use_cases(UseCaseFilter(), 1, 20),
    "get_use_case": lambda a: a.get_use_case("UC-001"),
    "get_use_case_references": lambda a: a.get_use_case_references("UC-001"),
    "get_linked_use_case_ids": lambda a: a.get_linked_use_case_ids("OP-0001"),
}

# Reads that are part of a write: they must see what the writer sees.
LAKEHOUSE_READS_AS_APP: dict[str, Callable[[LakehouseAccess], object]] = {
    "get_sequence_value": lambda a: a.get_sequence_value("OP"),
}

# Write method -> call that makes it write on behalf of ACTOR.
LAKEHOUSE_ACTOR_WRITES: dict[str, Callable[[LakehouseAccess], object]] = {
    "insert_reference_value": lambda a: a.insert_reference_value(
        "ref_business_domains", "HR", sort_order=1, active=True, user_initials=ACTOR
    ),
    "update_reference_value": lambda a: a.update_reference_value(
        "ref_business_domains", "HR", sort_order=1, active=True, user_initials=ACTOR
    ),
    "update_status_definition": lambda a: a.update_status_definition(
        "ref_op_status",
        "Draft",
        display_label="Draft",
        sort_order=1,
        badge_color="#808080",
        user_initials=ACTOR,
    ),
    "write_lock": lambda a: a.write_lock(make_lock(holder=ACTOR), now=NOW),
    "refresh_lock": lambda a: a.refresh_lock(
        "OP-0001",
        locked_by_initials=ACTOR,
        session_id="s1",
        last_heartbeat=NOW,
        expires_at=NOW,
    ),
    "delete_lock": lambda a: a.delete_lock("OP-0001", locked_by_initials=ACTOR),
    "append_change_log": lambda a: a.append_change_log(
        change_log_entry(author_initials=ACTOR)
    ),
    "append_change_log_entries": lambda a: a.append_change_log_entries(
        [change_log_entry(author_initials=ACTOR)] * 2
    ),
    "insert_one_pager_status": lambda a: a.insert_one_pager_status(
        status_row(last_updated_by=ACTOR)
    ),
    "update_one_pager_status": lambda a: a.update_one_pager_status(
        status_row(last_updated_by=ACTOR),
        expected_version="0.1.0",
        expected_status="Draft",
    ),
    "add_review_comment": lambda a: a.add_review_comment(
        review_comment(reviewer_initials=ACTOR)
    ),
    "resolve_review_comment": lambda a: a.resolve_review_comment(
        "OP-0002", 1, resolved_by=ACTOR, resolved_at=NOW
    ),
    "create_use_case": lambda a: a.create_use_case(_USE_CASE, ACTOR),
    "update_use_case": lambda a: a.update_use_case("UC-001", _USE_CASE, ACTOR),
    "set_use_case_deprecated": lambda a: a.set_use_case_deprecated(
        "UC-001", deprecated=True, user_initials=ACTOR
    ),
}

# Writes without an actor column, the reason (Data_Model.md §5), and a call.
LAKEHOUSE_OTHER_WRITES: dict[str, tuple[str, Callable[[LakehouseAccess], object]]] = {
    # Changed only by create and save; the change_log entry of that operation
    # records the author.
    "insert_authorized_users": (
        "covered by change_log",
        lambda a: a.insert_authorized_users([_AUTHORIZED]),
    ),
    "update_authorized_users": (
        "covered by change_log",
        lambda a: a.update_authorized_users([_AUTHORIZED]),
    ),
    "delete_authorized_users": (
        "covered by change_log",
        lambda a: a.delete_authorized_users("OP-1", ["ABR"]),
    ),
    "add_use_case_reference": (
        "covered by change_log",
        lambda a: a.add_use_case_reference("OP-1", "UC-001"),
    ),
    "remove_use_case_reference": (
        "covered by change_log",
        lambda a: a.remove_use_case_reference("OP-1", "UC-001"),
    ),
    # No row is left; the Admin service logs a security event.
    "delete_reference_value": (
        "security event",
        lambda a: a.delete_reference_value("ref_business_domains", "HR"),
    ),
    # Compensation removing the app's own partial writes (logged as failure).
    "delete_one_pager_records": (
        "compensation",
        lambda a: a.delete_one_pager_records("OP-1"),
    ),
    "delete_review_comment": (
        "compensation",
        lambda a: a.delete_review_comment(review_comment()),
    ),
    # System counter.
    "compare_and_set_sequence": (
        "system",
        lambda a: a.compare_and_set_sequence("OP", 4, 5),
    ),
}


def editor_page(data_access: MockDataAccess, one_pager_id: str = NEW_ID) -> AppTest:
    """Return the Editor in edit mode on ``one_pager_id`` for Alice."""
    return page_app(
        "editor.py",
        data_access,
        editor_mode="edit",
        editor_one_pager_id=one_pager_id,
    )


def switch_tab(at: AppTest, name: str) -> AppTest:
    """Switch the Editor to the tab ``name`` and rerun."""
    return at.radio(key="edit_active_tab").set_value(name).run()
