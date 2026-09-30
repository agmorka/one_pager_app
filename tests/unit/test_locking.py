"""Tests for pessimistic edit locks (Backend_Design.md §6, Testing_Strategy.md §3)."""

import logging
from datetime import UTC, datetime, timedelta

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
    is_expired,
    release_lock,
)
from onepagerapp.models import CurrentUser, LockInfo
from onepagerapp.permissions import (
    ActionState,
    PermissionDeniedError,
    get_action_states,
)
from tests.users import make_user

OP_ID = "OP-0001"
T0 = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
TTL = timedelta(seconds=60)

ALICE = make_user("ABR", "Alice Brown")
MAJA = make_user("MJO")


def _audit(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER_NAME]


def _acquire(
    data_access: MockDataAccess,
    user: CurrentUser = ALICE,
    session_id: str = "s1",
    now: datetime = T0,
) -> LockResult:
    return acquire_lock(data_access, OP_ID, user, session_id, ttl=TTL, now=now)


@pytest.mark.unit
def test__default_ttl_is_30_minutes_and_configurable() -> None:
    assert timedelta(minutes=30) == DEFAULT_LOCK_TTL
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH="/x")
    assert config.lock_ttl == DEFAULT_LOCK_TTL
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH="/x", ONE_PAGER_APP_LOCK_TTL_SECONDS=5)
    assert config.lock_ttl == timedelta(seconds=5)


@pytest.mark.unit
def test__acquire__no_lock_creates_one(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    result = _acquire(mock_data_access)

    assert result.status is LockStatus.ACQUIRED
    assert result.acquired
    assert result.message == ""
    stored = mock_data_access.get_lock(OP_ID)
    assert stored == result.lock
    assert stored is not None
    assert stored.locked_by_initials == "ABR"
    assert stored.locked_by_name == "Alice Brown"
    assert stored.session_id == "s1"
    assert stored.acquired_at == stored.last_heartbeat == T0
    assert stored.expires_at == T0 + TTL
    assert _audit(caplog) == [
        "action=acquire_lock outcome=success one_pager_id=OP-0001 user=ABR"
    ]


@pytest.mark.unit
def test__acquire__same_user_same_session_reuses_and_refreshes(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)
    later = T0 + timedelta(seconds=30)

    result = _acquire(mock_data_access, now=later)

    assert result.status is LockStatus.REUSED
    assert result.acquired
    stored = mock_data_access.get_lock(OP_ID)
    assert stored is not None
    assert stored.acquired_at == T0
    assert stored.last_heartbeat == later
    assert stored.expires_at == later + TTL


@pytest.mark.unit
def test__acquire__same_user_other_session_is_refused(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)

    result = _acquire(mock_data_access, session_id="s2", now=T0 + timedelta(seconds=5))

    assert result.status is LockStatus.OTHER_SESSION
    assert not result.acquired
    assert result.message == OTHER_SESSION_MESSAGE
    stored = mock_data_access.get_lock(OP_ID)
    assert stored is not None
    assert stored.session_id == "s1"
    assert stored.last_heartbeat == T0


@pytest.mark.unit
def test__acquire__other_user_active_lock_is_refused(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)

    result = _acquire(mock_data_access, user=MAJA, session_id="s9", now=T0 + TTL)

    assert result.status is LockStatus.LOCKED_BY_OTHER
    assert not result.acquired
    assert result.message == "Locked by Alice Brown since 2026-09-29 10:00 UTC."
    assert result.lock is not None
    assert result.lock.locked_by_initials == "ABR"


@pytest.mark.unit
def test__acquire__other_user_expired_lock_is_overridden_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    _acquire(mock_data_access)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    caplog.clear()
    later = T0 + TTL + timedelta(seconds=1)

    result = _acquire(mock_data_access, user=MAJA, session_id="s9", now=later)

    assert result.status is LockStatus.OVERRIDDEN
    assert result.acquired
    stored = mock_data_access.get_lock(OP_ID)
    assert stored is not None
    assert stored.locked_by_initials == "MJO"
    assert stored.acquired_at == later
    assert _audit(caplog) == [
        "action=acquire_lock outcome=lock_override one_pager_id=OP-0001 "
        "user=MJO previous_holder=ABR"
    ]
    record = next(r for r in caplog.records if r.name == AUDIT_LOGGER_NAME)
    assert record.levelno == logging.WARNING


@pytest.mark.unit
def test__acquire__own_expired_lock_in_other_session_is_reacquired_without_override(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    _acquire(mock_data_access)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    caplog.clear()

    result = _acquire(mock_data_access, session_id="s2", now=T0 + 2 * TTL)

    assert result.status is LockStatus.ACQUIRED
    assert "lock_override" not in " ".join(_audit(caplog))


@pytest.mark.unit
def test__acquire__lost_race_reports_the_winner(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = LockInfo(OP_ID, "MJO", "Maja", "s9", T0, T0, T0 + TTL)

    def racing_write(lock: LockInfo, *, now: datetime) -> bool:
        mock_data_access._locks[OP_ID] = winner
        return False

    monkeypatch.setattr(mock_data_access, "write_lock", racing_write)

    result = _acquire(mock_data_access)

    assert result.status is LockStatus.LOCKED_BY_OTHER
    assert result.lock == winner


@pytest.mark.unit
def test__acquire__unreadable_lock_table_raises(
    mock_data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(one_pager_id: str) -> None:
        msg = "locks table unreachable"
        raise RuntimeError(msg)

    monkeypatch.setattr(mock_data_access, "get_lock", broken)

    with pytest.raises(RuntimeError):
        _acquire(mock_data_access)


@pytest.mark.unit
def test__heartbeat__extends_only_the_holders_lock(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)
    later = T0 + timedelta(seconds=45)

    assert heartbeat(mock_data_access, OP_ID, ALICE, "s1", ttl=TTL, now=later)
    stored = mock_data_access.get_lock(OP_ID)
    assert stored is not None
    assert stored.expires_at == later + TTL
    assert not is_expired(stored, T0 + TTL + timedelta(seconds=1))

    assert not heartbeat(mock_data_access, OP_ID, ALICE, "s2", ttl=TTL, now=later)
    assert not heartbeat(mock_data_access, OP_ID, MAJA, "s1", ttl=TTL, now=later)
    assert not heartbeat(mock_data_access, "OP-0002", ALICE, "s1", ttl=TTL, now=later)


@pytest.mark.unit
def test__heartbeat__lock_taken_over_after_expiry_is_lost(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)
    _acquire(mock_data_access, user=MAJA, session_id="s9", now=T0 + 2 * TTL)

    later = T0 + 2 * TTL
    assert not heartbeat(mock_data_access, OP_ID, ALICE, "s1", ttl=TTL, now=later)
    stored = mock_data_access.get_lock(OP_ID)
    assert stored is not None
    assert stored.locked_by_initials == "MJO"


@pytest.mark.unit
def test__expiry__boundary_and_naive_timestamps() -> None:
    lock = LockInfo(OP_ID, "ABR", "Alice", "s1", T0, T0, T0 + TTL)
    assert not is_expired(lock, T0 + TTL)
    assert is_expired(lock, T0 + TTL + timedelta(microseconds=1))

    naive = LockInfo(
        OP_ID, "ABR", "Alice", "s1", T0, T0, (T0 + TTL).replace(tzinfo=None)
    )
    assert not is_expired(naive, T0)
    assert is_expired(naive, T0 + 2 * TTL)

    assert active_lock(None, T0) is None
    assert active_lock(lock, T0) is lock
    assert active_lock(lock, T0 + 2 * TTL) is None


@pytest.mark.unit
def test__get_active_lock__ignores_expired_rows(
    mock_data_access: MockDataAccess,
) -> None:
    assert get_active_lock(mock_data_access, OP_ID, T0) is None
    _acquire(mock_data_access)
    assert get_active_lock(mock_data_access, OP_ID, T0) is not None
    assert get_active_lock(mock_data_access, OP_ID, T0 + 2 * TTL) is None


@pytest.mark.unit
def test__release__holder_releases_from_any_session(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    _acquire(mock_data_access)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    caplog.clear()

    assert release_lock(mock_data_access, OP_ID, ALICE) is True

    assert mock_data_access.get_lock(OP_ID) is None
    assert _audit(caplog) == [
        "action=release_lock outcome=success one_pager_id=OP-0001 user=ABR"
    ]


@pytest.mark.unit
def test__release__not_locked_is_a_no_op(mock_data_access: MockDataAccess) -> None:
    assert release_lock(mock_data_access, OP_ID, ALICE) is False


@pytest.mark.unit
def test__release__other_user_is_denied_and_logged(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    _acquire(mock_data_access)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    caplog.clear()

    with pytest.raises(PermissionDeniedError):
        release_lock(mock_data_access, OP_ID, MAJA)

    assert mock_data_access.get_lock(OP_ID) is not None
    assert _audit(caplog) == [
        "action=release_lock outcome=permission_denied one_pager_id=OP-0001 user=MJO"
    ]


@pytest.mark.unit
def test__release__lock_can_be_acquired_again_afterwards(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)
    release_lock(mock_data_access, OP_ID, ALICE)

    result = _acquire(mock_data_access, user=MAJA, session_id="s9")

    assert result.status is LockStatus.ACQUIRED


@pytest.mark.unit
def test__release_lock_action_state__only_for_the_holder() -> None:
    def state(user: str, locked: bool, holder: str | None) -> ActionState:
        return get_action_states(
            current_user_initials=user,
            owner_initials="MJO",
            one_pager_status="Draft",
            is_locked=locked,
            lock_holder_initials=holder,
        )["release_lock"]

    assert state("ABR", True, "ABR").enabled
    assert not state("MJO", True, "ABR").enabled
    assert state("MJO", True, "ABR").tooltip == (
        "Only the lock holder can release this lock"
    )
    assert not state("ABR", False, None).enabled


@pytest.mark.unit
def test__get_active_locks__one_read_for_many_ids(
    mock_data_access: MockDataAccess,
) -> None:
    _acquire(mock_data_access)
    acquire_lock(mock_data_access, "OP-0002", MAJA, "s9", ttl=TTL, now=T0 - 2 * TTL)

    locks = get_active_locks(mock_data_access, ["OP-0001", "OP-0002", "OP-9999"], T0)

    assert list(locks) == ["OP-0001"]
    assert locks["OP-0001"].locked_by_initials == "ABR"
    assert get_active_locks(mock_data_access, [], T0) == {}
