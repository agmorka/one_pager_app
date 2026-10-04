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
    log_unrecognised_user,
)
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.workflow import create_one_pager
from tests.helpers import CREATOR_ROLES, audit_messages, capture_audit


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the records of the security-event logger."""
    return [r for r in caplog.records if r.name == AUDIT_LOGGER_NAME]


@pytest.mark.unit
def test__event_with_details__format_event__fixed_keys_first() -> None:
    """Action, outcome, record and user come first, then the details."""
    # When
    message = format_event(
        "create_one_pager",
        Outcome.FAILED,
        user="MJO",
        one_pager_id="OP-0003",
        step="write_document",
    )

    # Then
    assert message == (
        "action=create_one_pager outcome=failed one_pager_id=OP-0003 "
        "user=MJO step=write_document"
    )


@pytest.mark.unit
def test__no_user_and_no_record__format_event__dash_and_no_id() -> None:
    """A missing user is shown as ``-``; a missing record ID is left out."""
    # When
    message = format_event("create_one_pager", Outcome.PERMISSION_DENIED, user=None)

    # Then
    assert message == "action=create_one_pager outcome=permission_denied user=-"


@pytest.mark.unit
def test__value_that_could_forge_fields__format_event__quoted_and_escaped() -> None:
    """Spaces, quotes and newlines cannot inject fields or lines."""
    # When
    message = format_event(
        "x", Outcome.SUCCESS, user='AB outcome=success\nfake="1"', one_pager_id="OP-1"
    )

    # Then
    assert message == (
        "action=x outcome=success one_pager_id=OP-1 "
        'user="AB outcome=success\\nfake=\\"1\\""'
    )
    assert "\n" not in message


@pytest.mark.unit
def test__invalid_field_name__format_event__raises() -> None:
    """Field names must be identifiers."""
    # When / Then
    with pytest.raises(ValueError, match="Invalid audit field name"):
        format_event("x", Outcome.SUCCESS, user="ABR", **{"Bad Key": 1})


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
def test__outcome__log_event__level_follows_outcome(
    caplog: pytest.LogCaptureFixture, outcome: Outcome, level: int
) -> None:
    """Denials and overrides warn; failures are errors."""
    # When
    with caplog.at_level(logging.DEBUG, logger=AUDIT_LOGGER_NAME):
        log_event("x", outcome, user="ABR")

    # Then
    (record,) = _records(caplog)
    assert record.levelno == level


@pytest.mark.unit
def test__helper_calls__log_helpers__expected_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each helper logs its event with its own fields."""
    # Given
    capture_audit(caplog)

    # When
    log_permission_denied("edit_one_pager", user="ABR", one_pager_id="OP-0001")
    log_status_transition(
        one_pager_id="OP-0001",
        user="MJO",
        status_field="one_pager_status",
        from_status="In Review",
        to_status="Approved",
        version="1.0.0",
    )
    log_lock_override(one_pager_id="OP-0001", user="ABR", previous_holder="MJO")

    # Then
    assert audit_messages(caplog) == [
        "action=edit_one_pager outcome=permission_denied one_pager_id=OP-0001 user=ABR",
        "action=status_transition outcome=success one_pager_id=OP-0001 user=MJO "
        'status_field=one_pager_status from_status="In Review" to_status=Approved '
        "version=1.0.0",
        "action=acquire_lock outcome=lock_override one_pager_id=OP-0001 user=ABR "
        "previous_holder=MJO",
    ]


@pytest.mark.unit
def test__valid_input__create_one_pager__logs_success(
    caplog: pytest.LogCaptureFixture,
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A successful create logs one success event with the new ID."""
    # Given
    capture_audit(caplog)

    # When
    result = create_one_pager(
        valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert audit_messages(caplog) == [
        f"action=create_one_pager outcome=success "
        f"one_pager_id={result.one_pager_id} user=MJO"
    ]


@pytest.mark.unit
def test__no_user__create_one_pager__logs_permission_denied(
    caplog: pytest.LogCaptureFixture,
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A refused create logs one warning without a user."""
    # Given
    capture_audit(caplog)

    # When / Then
    with pytest.raises(PermissionDeniedError):
        create_one_pager(
            valid_input, None, mock_data_access, document_store, roles=CREATOR_ROLES
        )
    (record,) = _records(caplog)
    assert record.getMessage() == (
        "action=create_one_pager outcome=permission_denied user=-"
    )
    assert record.levelno == logging.WARNING


@pytest.mark.unit
@pytest.mark.parametrize(
    ("username", "shown"), [("x0wadm@guest.com", "x0wadm@guest.com"), (None, "-")]
)
def test__unrecognised_username__log_unrecognised_user__username_logged(
    caplog: pytest.LogCaptureFixture, username: str | None, shown: str
) -> None:
    """The refused username is logged (``-`` when there is none)."""
    # When
    with caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER_NAME):
        log_unrecognised_user(username)

    # Then
    assert caplog.messages == [
        f"action=access_app outcome=permission_denied user=- username={shown}"
    ]
