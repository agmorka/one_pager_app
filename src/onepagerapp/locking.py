"""Pessimistic edit locks (Requirements_and_Scope.md §10, Backend_Design.md §6).

One lock row per One Pager in the ``locks`` Delta table. The editor calls
``acquire_lock`` when it opens an existing One Pager and on every Streamlit
re-run after that: a re-run by the lock holder is the heartbeat that pushes
``expires_at`` forward. A lock without a heartbeat for the TTL (30 minutes by
default, ``AppConfig.lock_ttl``) is expired and may be taken over; taking over
another user's expired lock is logged as a security event.

All timestamps are timezone-aware UTC. Pure Python — no Streamlit.
"""

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum

from onepagerapp.audit import (
    Outcome,
    log_event,
    log_lock_override,
    log_permission_denied,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import CurrentUser, LockInfo
from onepagerapp.permissions import (
    PermissionDeniedError,
    can_release_lock,
    require_identity,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL = timedelta(minutes=30)

OTHER_SESSION_MESSAGE = (
    "You have this document open in another browser tab. Editing in multiple "
    "tabs simultaneously is not supported. Please close one tab."
)


class LockStatus(str, Enum):
    """How an ``acquire_lock`` call ended."""

    ACQUIRED = "acquired"
    """No active lock existed; a new lock was written."""
    REUSED = "reused"
    """The caller already held the lock in this session; heartbeat refreshed."""
    OVERRIDDEN = "overridden"
    """Another user's expired lock was taken over (logged)."""
    OTHER_SESSION = "other_session"
    """The caller holds an active lock in another session (browser tab)."""
    LOCKED_BY_OTHER = "locked_by_other"
    """Another user holds an active lock."""


@dataclass
class LockResult:
    """Outcome of ``acquire_lock``.

    Attributes:
        status: What happened.
        lock: The caller's lock when acquired, otherwise the blocking lock.
        message: User-facing text when the lock was not acquired, else "".

    """

    status: LockStatus
    lock: LockInfo | None
    message: str = ""

    @property
    def acquired(self) -> bool:
        """Whether the caller now holds the lock and may edit."""
        return self.status in (
            LockStatus.ACQUIRED,
            LockStatus.REUSED,
            LockStatus.OVERRIDDEN,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Treat naive timestamps (Delta TIMESTAMP read back without an offset) as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def is_expired(lock: LockInfo, now: datetime | None = None) -> bool:
    """Whether the lock has passed ``expires_at`` (Data_Model.md §4 ``locks``)."""
    return _as_utc(now or utc_now()) > _as_utc(lock.expires_at)


def active_lock(lock: LockInfo | None, now: datetime | None = None) -> LockInfo | None:
    """Return ``lock`` if it is still active, None if absent or expired."""
    if lock is None or is_expired(lock, now):
        return None
    return lock


def get_active_lock(
    data_access: DataAccess, one_pager_id: str, now: datetime | None = None
) -> LockInfo | None:
    """Read the lock of a One Pager fresh from storage; expired locks count as none.

    Lock status is never cached (UI_Design.md §6).
    """
    return active_lock(data_access.get_lock(one_pager_id), now)


def get_active_locks(
    data_access: DataAccess, one_pager_ids: list[str], now: datetime | None = None
) -> dict[str, LockInfo]:
    """Active locks of several One Pagers (Registry page), keyed by One Pager ID."""
    now = now or utc_now()
    return {
        lock.one_pager_id: lock
        for lock in data_access.get_locks(one_pager_ids)
        if not is_expired(lock, now)
    }


def is_held_by(
    lock: LockInfo, user: CurrentUser, session_id: str | None = None
) -> bool:
    """Whether ``user`` (and, when given, the same session) holds ``lock``."""
    if lock.locked_by_initials != user.initials:
        return False
    return session_id is None or lock.session_id == session_id


def locked_by_message(lock: LockInfo) -> str:
    """User-facing text for a lock held by someone else (Backend_Design.md §6)."""
    since = _as_utc(lock.acquired_at).strftime("%Y-%m-%d %H:%M UTC")
    return f"Locked by {lock.locked_by_name} since {since}."


def _new_lock(
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    now: datetime,
    ttl: timedelta,
) -> LockInfo:
    return LockInfo(
        one_pager_id=one_pager_id,
        locked_by_initials=user.initials,
        locked_by_name=user.display_name,
        session_id=session_id,
        acquired_at=now,
        last_heartbeat=now,
        expires_at=now + ttl,
    )


def _blocked(lock: LockInfo, user: CurrentUser) -> LockResult:
    """Refuse because the caller's session does not hold the active lock."""
    if lock.locked_by_initials == user.initials:
        return LockResult(LockStatus.OTHER_SESSION, lock, OTHER_SESSION_MESSAGE)
    return LockResult(LockStatus.LOCKED_BY_OTHER, lock, locked_by_message(lock))


def acquire_lock(  # noqa: PLR0913 - every argument is part of the lock identity
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    *,
    ttl: timedelta = DEFAULT_LOCK_TTL,
    now: datetime | None = None,
) -> LockResult:
    """Acquire, reuse or refuse the edit lock of a One Pager (Backend_Design.md §6).

    1. No lock, or an expired lock of the same user → write a new lock.
    2. Same user and same session → reuse it and refresh the heartbeat.
    3. Same user, other session, not expired → refuse (another tab).
    4. Other user, not expired → refuse ("Locked by {name} since {time}").
    5. Other user, expired → take it over and log a lock override.

    The write is conditional on the lock row not having changed in between, and
    the row is read back afterwards, so of two sessions racing for the same
    lock only one ends up holding it.

    Args:
        data_access: Storage for the ``locks`` table.
        one_pager_id: The One Pager to edit.
        user: The user entering the editor.
        session_id: Streamlit session ID (tells browser tabs apart).
        ttl: Expiry window after the last heartbeat.
        now: Current time (tests); defaults to now in UTC.

    Returns:
        LockResult; ``result.acquired`` tells whether editing may proceed.

    Raises:
        RuntimeError: If the locks table cannot be read or written. The editor
            shows an error instead of opening (Testing_Strategy.md §9).

    """
    require_identity(user, "acquire_lock", one_pager_id)
    now = _as_utc(now or utc_now())
    existing = data_access.get_lock(one_pager_id)

    if existing is not None and not is_expired(existing, now):
        if not is_held_by(existing, user, session_id):
            return _blocked(existing, user)
        # Same user and session: this re-run is the heartbeat.
        lock = replace(existing, last_heartbeat=now, expires_at=now + ttl)
        status = LockStatus.REUSED
    else:
        lock = _new_lock(one_pager_id, user, session_id, now, ttl)
        overriding = (
            existing is not None and existing.locked_by_initials != user.initials
        )
        status = LockStatus.OVERRIDDEN if overriding else LockStatus.ACQUIRED

    written = data_access.write_lock(lock, now=now)
    stored = data_access.get_lock(one_pager_id)
    if not written or stored is None or not is_held_by(stored, user, session_id):
        # Another session won the race between our read and our write.
        logger.info(f"Lock race on {one_pager_id} lost by {user.initials}")
        if stored is None or is_expired(stored, now):
            msg = f"Lock on {one_pager_id} could not be written"
            raise RuntimeError(msg)
        return _blocked(stored, user)

    if status is LockStatus.OVERRIDDEN and existing is not None:
        log_lock_override(
            one_pager_id=one_pager_id,
            user=user.initials,
            previous_holder=existing.locked_by_initials,
        )
    elif status is LockStatus.ACQUIRED:
        log_event(
            "acquire_lock",
            Outcome.SUCCESS,
            user=user.initials,
            one_pager_id=one_pager_id,
        )
    return LockResult(status, stored)


def heartbeat(  # noqa: PLR0913 - every argument is part of the lock identity
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    *,
    ttl: timedelta = DEFAULT_LOCK_TTL,
    now: datetime | None = None,
) -> bool:
    """Push the expiry of the caller's lock forward (called on every editor re-run).

    Only the lock row of this user and session is updated, so a lock that was
    released or taken over after expiry is never revived.

    Returns:
        True if the caller still holds the lock, False if it was lost.

    """
    require_identity(user, "heartbeat", one_pager_id)
    now = _as_utc(now or utc_now())
    return data_access.refresh_lock(
        one_pager_id,
        locked_by_initials=user.initials,
        session_id=session_id,
        last_heartbeat=now,
        expires_at=now + ttl,
    )


def release_lock(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
) -> bool:
    """Release the caller's own lock (Preview **Release my lock**, Backend_Design §6).

    Only the lock holder may release a lock (``locked_by_initials ==
    user.initials``), from any session. Submit and Cancel release the lock
    through this function too once they exist (Phase 5).

    Returns:
        True if a lock was released, False if the One Pager was not locked.

    Raises:
        PermissionDeniedError: If another user holds the lock (logged).

    """
    require_identity(user, "release_lock", one_pager_id)
    lock = data_access.get_lock(one_pager_id)
    if lock is None:
        return False
    if not can_release_lock(user, lock):
        log_permission_denied(
            "release_lock", user=user.initials, one_pager_id=one_pager_id
        )
        msg = "Only the lock holder can release this lock."
        raise PermissionDeniedError(msg)
    released = data_access.delete_lock(one_pager_id, locked_by_initials=user.initials)
    if released:
        log_event(
            "release_lock",
            Outcome.SUCCESS,
            user=user.initials,
            one_pager_id=one_pager_id,
        )
    return released
