"""Timestamp helpers. All timestamps the app compares are UTC."""

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Treat naive timestamps (Delta TIMESTAMP read back without an offset) as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_label(value: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timestamp for display in UTC, e.g. "2026-09-19 10:15 UTC".

    Aware timestamps are converted to UTC; naive ones are taken as UTC.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime(fmt) + " UTC"


def age_label(value: datetime | None, now: datetime | None = None) -> str:
    """How long ago ``value`` was, in whole days: "today", "1 day", "3 days"."""
    if value is None:
        return "-"
    days = ((now or datetime.now(UTC)) - as_utc(value)).days
    if days <= 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"
