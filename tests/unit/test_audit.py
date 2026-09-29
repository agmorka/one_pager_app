"""Tests for structured security-event logging."""

import logging

import pytest

from onepagerapp.audit import (
    AUDIT_LOGGER_NAME,
    Outcome,
    format_event,
    log_event,
    log_lock_override,
    log_permission_denied,
    log_status_transition,
)
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import create_one_pager


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == AUDIT_LOGGER_NAME]


@pytest.mark.unit
def test__format_event__fixed_keys_first_then_details() -> None:
    message = format_event(
        "create_one_pager",
        Outcome.FAILED,
        user="MJO",
        one_pager_id="OP-0003",
        step="write_document",
    )
    assert message == (
        "action=create_one_pager outcome=failed one_pager_id=OP-0003 "
        "user=MJO step=write_document"
    )


@pytest.mark.unit
def test__format_event__none_and_missing_record_id() -> None:
    message = format_event("create_one_pager", Outcome.PERMISSION_DENIED, user=None)
    assert message == "action=create_one_pager outcome=permission_denied user=-"


@pytest.mark.unit
def test__format_event__quotes_values_that_could_forge_fields() -> None:
    message = format_event(
        "x", Outcome.SUCCESS, user='AB outcome=success\nfake="1"', one_pager_id="OP-1"
    )
    assert message == (
        "action=x outcome=success one_pager_id=OP-1 "
        'user="AB outcome=success\\nfake=\\"1\\""'
    )
    assert "\n" not in message


@pytest.mark.unit
def test__format_event__rejects_invalid_field_names() -> None:
    with pytest.raises(ValueError, match="Invalid audit field name"):
        format_event("x", Outcome.SUCCESS, user="AB", **{"Bad Key": 1})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "level"),
    [
        (Outcome.SUCCESS, logging.INFO),
        (Outcome.PERMISSION_DENIED, logging.WARNING),
        (Outcome.LOCK_OVERRIDE, logging.WARNING),
        (Outcome.FAILED, logging.ERROR),
        (Outcome.COMPENSATION_FAILED, logging.ERROR),
    ],
)
def test__log_event__level_follows_outcome(
    caplog: pytest.LogCaptureFixture, outcome: Outcome, level: int
) -> None:
    with caplog.at_level(logging.DEBUG, logger=AUDIT_LOGGER_NAME):
        log_event("x", outcome, user="AB")
    (record,) = _records(caplog)
    assert record.levelno == level


@pytest.mark.unit
def test__helpers__produce_expected_events(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        log_permission_denied("edit_one_pager", user="AB", one_pager_id="OP-0001")
        log_status_transition(
            one_pager_id="OP-0001",
            user="MJO",
            status_field="one_pager_status",
            from_status="In Review",
            to_status="Approved",
            version="1.0.0",
        )
        log_lock_override(one_pager_id="OP-0001", user="AB", previous_holder="MJO")

    messages = [r.getMessage() for r in _records(caplog)]
    assert messages == [
        "action=edit_one_pager outcome=permission_denied one_pager_id=OP-0001 user=AB",
        "action=status_transition outcome=success one_pager_id=OP-0001 user=MJO "
        'status_field=one_pager_status from_status="In Review" to_status=Approved '
        "version=1.0.0",
        "action=acquire_lock outcome=lock_override one_pager_id=OP-0001 user=AB "
        "previous_holder=MJO",
    ]


@pytest.mark.unit
def test__create_one_pager__logs_success_event(
    caplog: pytest.LogCaptureFixture,
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        result = create_one_pager(
            valid_input, creator, mock_data_access, document_store
        )

    (record,) = _records(caplog)
    assert record.getMessage() == (
        f"action=create_one_pager outcome=success "
        f"one_pager_id={result.one_pager_id} user=MJO"
    )


@pytest.mark.unit
def test__create_one_pager__logs_permission_denied(
    caplog: pytest.LogCaptureFixture,
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    with (
        caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME),
        pytest.raises(PermissionDeniedError),
    ):
        create_one_pager(valid_input, None, mock_data_access, document_store)

    (record,) = _records(caplog)
    assert record.getMessage() == (
        "action=create_one_pager outcome=permission_denied user=-"
    )
    assert record.levelno == logging.WARNING
