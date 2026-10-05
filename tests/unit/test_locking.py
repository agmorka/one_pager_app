"""Tests for pessimistic edit locks (Backend_Design.md §6, Testing_Strategy.md §3)."""

import logging
from datetime import datetime, timedelta

import pytest

from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.config import AppConfig
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.locking import (
    DEFAULT_LOCK_TTL,
    OTHER_SESSION_MESSAGE,
    LockResult,
    LockStatus,
    acquire_lock,
    active_lock,
    get_active_lock,
    get_active_locks,
    heartbeat,
    heartbeat_due,
    is_expired,
    release_lock,
)
from onepagerapp.models import CurrentUser, LockInfo
from onepagerapp.permissions import PermissionDeniedError, get_action_states
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    IN_REVIEW_ID,
    MAJA,
    NOW,
    SESSION_ID,
    audit_messages,
    capture_audit,
    failing,
    make_lock,
)

OP_ID = APPROVED_ID
TTL = timedelta(seconds=60)


def _acquire(
    data_access: MockDataAccess,
    user: CurrentUser = ALICE,
    session_id: str = SESSION_ID,
    now: datetime = NOW,
) -> LockResult:
    """Acquire the lock of OP-0001 with a 60 second TTL."""
    return acquire_lock(data_access, OP_ID, user, session_id, ttl=TTL, now=now)


@pytest.mark.unit
def test__no_setting__lock_ttl__30_minutes() -> None:
    """Locks expire after 30 minutes by default."""
    # When
    ttl = AppConfig(ONE_PAGER_APP_VOLUME_PATH="/x").lock_ttl

    # Then
    assert ttl == DEFAULT_LOCK_TTL == timedelta(minutes=30)


@pytest.mark.unit
def test__ttl_setting__lock_ttl__configured_seconds() -> None:
    """The lock TTL is configurable in seconds."""
    # Given
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH="/x", ONE_PAGER_APP_LOCK_TTL_SECONDS=5)

    # When
    ttl = config.lock_ttl

    # Then
    assert ttl == timedelta(seconds=5)


@pytest.mark.unit
def test__no_lock__acquire__creates_lock_and_logs(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """A free One Pager is locked by the user's session."""
    # Given
    capture_audit(caplog)

    # When
    result = _acquire(mock_data_access)

    # Then
    assert (result.status, result.acquired, result.message) == (
        LockStatus.ACQUIRED,
        True,
        "",
    )
    stored = mock_data_access.get_lock(OP_ID)
    assert stored == result.lock
    assert (stored.locked_by_initials, stored.locked_by_name) == ("ABR", "Alice Brown")
    assert stored.session_id == SESSION_ID
    assert stored.acquired_at == stored.last_heartbeat == NOW
    assert stored.expires_at == NOW + TTL
    assert audit_messages(caplog) == [
        "action=acquire_lock outcome=success one_pager_id=OP-0001 user=ABR"
    ]


@pytest.mark.unit
def test__own_lock_in_same_session__acquire__reused_and_refreshed(
    mock_data_access: MockDataAccess,
) -> None:
    """Re-opening in the same session refreshes the heartbeat and expiry."""
    # Given
    _acquire(mock_data_access)
    later = NOW + timedelta(seconds=30)

    # When
    result = _acquire(mock_data_access, now=later)

    # Then
    assert (result.status, result.acquired) == (LockStatus.REUSED, True)
    stored = mock_data_access.get_lock(OP_ID)
    assert stored.acquired_at == NOW
    assert (stored.last_heartbeat, stored.expires_at) == (later, later + TTL)


@pytest.mark.unit
def test__own_lock_in_other_session__acquire__refused_unchanged(
    mock_data_access: MockDataAccess,
) -> None:
    """The same user in another browser session is refused."""
    # Given
    _acquire(mock_data_access)

    # When
    result = _acquire(mock_data_access, session_id="s2", now=NOW + timedelta(seconds=5))

    # Then
    assert (result.status, result.acquired) == (LockStatus.OTHER_SESSION, False)
    assert result.message == OTHER_SESSION_MESSAGE
    stored = mock_data_access.get_lock(OP_ID)
    assert (stored.session_id, stored.last_heartbeat) == (SESSION_ID, NOW)


@pytest.mark.unit
def test__active_lock_of_other_user__acquire__refused_with_holder(
    mock_data_access: MockDataAccess,
) -> None:
    """Another user's active lock is reported with its holder."""
    # Given
    _acquire(mock_data_access)

    # When
    result = _acquire(mock_data_access, user=MAJA, session_id="s9", now=NOW + TTL)

    # Then
    assert (result.status, result.acquired) == (LockStatus.LOCKED_BY_OTHER, False)
    assert result.message == "Locked by Alice Brown since 2026-09-29 10:00 UTC."
    assert result.lock.locked_by_initials == "ABR"


@pytest.mark.unit
def test__expired_lock_of_other_user__acquire__overridden_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """An expired foreign lock is taken over and logged as a warning."""
    # Given
    _acquire(mock_data_access)
    capture_audit(caplog)
    later = NOW + TTL + timedelta(seconds=1)

    # When
    result = _acquire(mock_data_access, user=MAJA, session_id="s9", now=later)

    # Then
    assert (result.status, result.acquired) == (LockStatus.OVERRIDDEN, True)
    stored = mock_data_access.get_lock(OP_ID)
    assert (stored.locked_by_initials, stored.acquired_at) == ("MJO", later)
    assert audit_messages(caplog) == [
        "action=acquire_lock outcome=lock_override one_pager_id=OP-0001 "
        "user=MJO previous_holder=ABR"
    ]
    record = next(r for r in caplog.records if r.name == AUDIT_LOGGER_NAME)
    assert record.levelno == logging.WARNING


@pytest.mark.unit
def test__own_expired_lock_in_other_session__acquire__acquired_without_override(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """Taking over one's own expired lock is not an override."""
    # Given
    _acquire(mock_data_access)
    capture_audit(caplog)

    # When
    result = _acquire(mock_data_access, session_id="s2", now=NOW + 2 * TTL)

    # Then
    assert result.status is LockStatus.ACQUIRED
    assert "lock_override" not in " ".join(audit_messages(caplog))


@pytest.mark.unit
def test__concurrent_writer_wins__acquire__reports_the_winner(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the conditional write reports the lock that won."""
    # Given
    winner = make_lock(OP_ID, "MJO", name="Maja", session_id="s9", expires_at=NOW + TTL)

    def racing_write(lock: LockInfo, *, now: datetime) -> bool:
        mock_data_access._locks[OP_ID] = winner
        return False

    monkeypatch.setattr(mock_data_access, "write_lock", racing_write)

    # When
    result = _acquire(mock_data_access)

    # Then
    assert result.status is LockStatus.LOCKED_BY_OTHER
    assert result.lock == winner


@pytest.mark.unit
def test__write_conflict_within_own_session__acquire__still_held(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A heartbeat whose write conflicts with the same session keeps the lock."""
    # Given
    _acquire(mock_data_access)

    def conflicting_write(lock: LockInfo, *, now: datetime) -> bool:
        return False

    monkeypatch.setattr(mock_data_access, "write_lock", conflicting_write)

    # When
    result = _acquire(mock_data_access, now=NOW + timedelta(seconds=5))

    # Then
    assert (result.status, result.acquired) == (LockStatus.REUSED, True)
    assert result.message == ""
    assert result.lock.session_id == SESSION_ID


@pytest.mark.unit
def test__unreadable_lock_table__acquire__raises(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock table error is not mistaken for "not locked"."""
    # Given
    monkeypatch.setattr(mock_data_access, "get_lock", failing("unreachable"))

    # When / Then
    with pytest.raises(RuntimeError):
        _acquire(mock_data_access)


@pytest.mark.unit
def test__holders_lock__heartbeat__extends_expiry(
    mock_data_access: MockDataAccess,
) -> None:
    """The holder's heartbeat moves the expiry forward."""
    # Given
    _acquire(mock_data_access)
    later = NOW + timedelta(seconds=45)

    # When
    extended = heartbeat(mock_data_access, OP_ID, ALICE, SESSION_ID, ttl=TTL, now=later)

    # Then
    assert extended
    stored = mock_data_access.get_lock(OP_ID)
    assert stored.expires_at == later + TTL
    assert not is_expired(stored, NOW + TTL + timedelta(seconds=1))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("one_pager_id", "user", "session_id"),
    [
        (OP_ID, ALICE, "s2"),
        (OP_ID, MAJA, SESSION_ID),
        (IN_REVIEW_ID, ALICE, SESSION_ID),
    ],
    ids=["other-session", "other-user", "other-one-pager"],
)
def test__lock_not_held__heartbeat__false(
    mock_data_access: MockDataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
) -> None:
    """Only the holder's session extends its own lock."""
    # Given
    _acquire(mock_data_access)

    # When
    extended = heartbeat(
        mock_data_access, one_pager_id, user, session_id, ttl=TTL, now=NOW
    )

    # Then
    assert not extended


@pytest.mark.unit
def test__lock_taken_over_after_expiry__heartbeat__lost(
    mock_data_access: MockDataAccess,
) -> None:
    """The former holder cannot extend a lock taken over by someone else."""
    # Given
    _acquire(mock_data_access)
    later = NOW + 2 * TTL
    _acquire(mock_data_access, user=MAJA, session_id="s9", now=later)

    # When
    extended = heartbeat(mock_data_access, OP_ID, ALICE, SESSION_ID, ttl=TTL, now=later)

    # Then
    assert not extended
    assert mock_data_access.get_lock(OP_ID).locked_by_initials == "MJO"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("now", "expired"),
    [(NOW + TTL, False), (NOW + TTL + timedelta(microseconds=1), True)],
    ids=["at-expiry", "after-expiry"],
)
def test__lock__is_expired__only_after_expiry(now: datetime, expired: bool) -> None:
    """A lock is still valid at its expiry time."""
    # Given
    lock = make_lock(expires_at=NOW + TTL)

    # When
    result = is_expired(lock, now)

    # Then
    assert result is expired


@pytest.mark.unit
@pytest.mark.parametrize(
    ("now", "expired"), [(NOW, False), (NOW + 2 * TTL, True)], ids=["before", "after"]
)
def test__naive_expiry_timestamp__is_expired__read_as_utc(
    now: datetime, expired: bool
) -> None:
    """A timestamp read back without an offset is taken as UTC."""
    # Given
    lock = make_lock(expires_at=(NOW + TTL).replace(tzinfo=None))

    # When
    result = is_expired(lock, now)

    # Then
    assert result is expired


@pytest.mark.unit
def test__lock_row__active_lock__none_when_missing_or_expired() -> None:
    """``active_lock`` hides missing and expired locks."""
    # Given
    lock = make_lock(expires_at=NOW + TTL)

    # When
    results = [
        active_lock(None, NOW),
        active_lock(lock, NOW),
        active_lock(lock, NOW + 2 * TTL),
    ]

    # Then
    assert results == [None, lock, None]


@pytest.mark.unit
def test__stored_lock__get_active_lock__ignores_expired_row(
    mock_data_access: MockDataAccess,
) -> None:
    """An expired lock row reads as no lock."""
    # Given
    _acquire(mock_data_access)

    # When
    active, expired = (
        get_active_lock(mock_data_access, OP_ID, NOW),
        get_active_lock(mock_data_access, OP_ID, NOW + 2 * TTL),
    )

    # Then
    assert active is not None
    assert expired is None


@pytest.mark.unit
def test__no_lock__get_active_lock__none(mock_data_access: MockDataAccess) -> None:
    """A One Pager without a lock row has no active lock."""
    # When
    result = get_active_lock(mock_data_access, OP_ID, NOW)

    # Then
    assert result is None


@pytest.mark.unit
def test__holders_lock__release_from_any_session__removed_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """The holder releases the lock, whatever the session."""
    # Given
    _acquire(mock_data_access)
    capture_audit(caplog)

    # When
    released = release_lock(mock_data_access, OP_ID, ALICE)

    # Then
    assert released is True
    assert mock_data_access.get_lock(OP_ID) is None
    assert audit_messages(caplog) == [
        "action=release_lock outcome=success one_pager_id=OP-0001 user=ABR"
    ]


@pytest.mark.unit
def test__no_lock__release__no_op(mock_data_access: MockDataAccess) -> None:
    """Releasing a free One Pager does nothing."""
    # When
    released = release_lock(mock_data_access, OP_ID, ALICE)

    # Then
    assert released is False


@pytest.mark.unit
def test__lock_of_other_user__release__raises_and_logs(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the holder may release a lock."""
    # Given
    _acquire(mock_data_access)
    capture_audit(caplog)

    # When / Then
    with pytest.raises(PermissionDeniedError):
        release_lock(mock_data_access, OP_ID, MAJA)
    assert mock_data_access.get_lock(OP_ID) is not None
    assert audit_messages(caplog) == [
        "action=release_lock outcome=permission_denied one_pager_id=OP-0001 user=MJO"
    ]


@pytest.mark.unit
def test__released_lock__other_user_acquires__acquired(
    mock_data_access: MockDataAccess,
) -> None:
    """A released lock can be taken by anybody."""
    # Given
    _acquire(mock_data_access)
    release_lock(mock_data_access, OP_ID, ALICE)

    # When
    result = _acquire(mock_data_access, user=MAJA, session_id="s9")

    # Then
    assert result.status is LockStatus.ACQUIRED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user", "locked", "holder", "enabled"),
    [
        ("ABR", True, "ABR", True),
        ("MJO", True, "ABR", False),
        ("ABR", False, None, False),
    ],
)
def test__lock_state__action_states__release_only_for_holder(
    user: str, locked: bool, holder: str | None, enabled: bool
) -> None:
    """Release lock is enabled for the holder of an existing lock."""
    # When
    state = get_action_states(
        current_user_initials=user,
        owner_initials="MJO",
        one_pager_status="Draft",
        is_locked=locked,
        lock_holder_initials=holder,
    )["release_lock"]

    # Then
    assert state.enabled is enabled


@pytest.mark.unit
def test__lock_of_other_user__action_states__release_tooltip_explains() -> None:
    """The disabled Release lock action says who may release."""
    # When
    state = get_action_states(
        current_user_initials="MJO",
        owner_initials="MJO",
        one_pager_status="Draft",
        is_locked=True,
        lock_holder_initials="ABR",
    )["release_lock"]

    # Then
    assert state.tooltip == "Only the lock holder can release this lock"


@pytest.mark.unit
def test__active_and_expired_locks__get_active_locks__only_active_by_id(
    mock_data_access: MockDataAccess,
) -> None:
    """One read returns the active locks of many One Pagers."""
    # Given
    _acquire(mock_data_access)
    acquire_lock(mock_data_access, IN_REVIEW_ID, MAJA, "s9", ttl=TTL, now=NOW - 2 * TTL)

    # When
    locks = get_active_locks(mock_data_access, [OP_ID, IN_REVIEW_ID, "OP-9999"], NOW)

    # Then
    assert list(locks) == [OP_ID]
    assert locks[OP_ID].locked_by_initials == "ABR"


@pytest.mark.unit
def test__no_ids__get_active_locks__empty(mock_data_access: MockDataAccess) -> None:
    """No IDs, no locks."""
    # When
    locks = get_active_locks(mock_data_access, [], NOW)

    # Then
    assert locks == {}


# ============================================================================
# Heartbeat throttling (editor re-runs)
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lock", "elapsed", "due"),
    [
        (None, timedelta(0), True),
        (make_lock(OP_ID), timedelta(0), False),
        (make_lock(OP_ID), timedelta(minutes=4, seconds=59), False),
        (make_lock(OP_ID), timedelta(minutes=5), True),
        (make_lock(OP_ID), timedelta(minutes=-1), True),
        (make_lock(IN_REVIEW_ID), timedelta(0), True),
        (make_lock(OP_ID, session_id="other-tab"), timedelta(0), True),
    ],
    ids=[
        "no-lock",
        "just-written",
        "before-interval",
        "interval-passed",
        "clock-went-back",
        "other-one-pager",
        "other-session",
    ],
)
def test__session_lock__heartbeat_due__only_after_a_sixth_of_the_ttl(
    lock: LockInfo | None, elapsed: timedelta, due: bool
) -> None:
    """With the 30-minute TTL the heartbeat is written at most every 5 minutes."""
    # When
    result = heartbeat_due(
        lock, OP_ID, SESSION_ID, ttl=DEFAULT_LOCK_TTL, now=NOW + elapsed
    )

    # Then
    assert result is due
