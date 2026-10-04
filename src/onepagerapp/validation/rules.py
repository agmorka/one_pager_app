"""Field rules shared by every validation tier: patterns, limits and messages.

The initials pattern is the ONE_PAGER_APP_INITIALS_PATTERN setting; app.py
applies the configured value at start-up (``set_initials_pattern``).
"""

import re

from onepagerapp.config import AppConfig
from onepagerapp.models import ValidationError

DATA_PRODUCT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Maximum lengths (D11). Values over the limit are rejected, never truncated.
MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 5000
MAX_EMAIL_LENGTH = 254

DATA_PRODUCT_TYPES = ("Foundational", "Integrated", "Augmented")

# Corporate initials of Owners/SMEs (Architecture.md §4), checked after they
# are upper-cased. The pattern is the ONE_PAGER_APP_INITIALS_PATTERN setting;
# app.py applies the configured value at start-up (set_initials_pattern).
_initials_pattern = re.compile(
    AppConfig.model_fields["ONE_PAGER_APP_INITIALS_PATTERN"].default
)
INITIALS_RULE = "Enter corporate initials: 3 letters or digits (e.g. X0W)."

DATA_PRODUCT_RULE = (
    "Use 2-63 characters: lowercase letters, digits and underscores, "
    "starting with a letter (e.g. customer_master)."
)


def set_initials_pattern(pattern: re.Pattern[str]) -> None:
    """Use the configured pattern (``AppConfig.initials_pattern``) for initials."""
    global _initials_pattern  # noqa: PLW0603 - one setting, applied at start-up
    _initials_pattern = pattern


def initials_pattern() -> re.Pattern[str]:
    """Return the pattern Owner/SME initials must match (after upper-casing)."""
    return _initials_pattern


def check_length(
    errors: list[ValidationError], path: str, value: str | None, max_len: int
) -> None:
    """Add an error to ``errors`` when ``value`` is longer than ``max_len``."""
    if value and len(value) > max_len:
        errors.append(ValidationError(path, f"Must be at most {max_len} characters."))
