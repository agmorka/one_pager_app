"""Atomic ID generation for OP-####, UC-### and BR-### (Data_Model.md §4).

Uses the ``id_sequences`` counter through two ``DataAccess`` primitives:
read the current value, then compare-and-set it to ``current + 1``. The CAS
only succeeds when no other writer advanced the counter in between; on a
conflict the whole read/CAS cycle is retried. A gap in the sequence (an ID
consumed by a create that later failed) is harmless.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onepagerapp.data_access.base import DataAccess

logger = logging.getLogger(__name__)

ID_WIDTHS: dict[str, int] = {"OP": 4, "UC": 3, "BR": 3}
MAX_ATTEMPTS = 5


class IdGenerationError(RuntimeError):
    """Raised when a new ID could not be reserved."""


def format_id(id_type: str, value: int) -> str:
    """Format a sequence value, e.g. ("OP", 7) -> "OP-0007"."""
    if id_type not in ID_WIDTHS:
        msg = f"Unknown ID type: {id_type}"
        raise ValueError(msg)
    return f"{id_type}-{value:0{ID_WIDTHS[id_type]}d}"


def next_id(data_access: "DataAccess", id_type: str) -> str:
    """Reserve and return the next ID of the given type.

    Raises:
        IdGenerationError: If the counter could not be advanced after
            ``MAX_ATTEMPTS`` attempts (sustained contention).

    """
    if id_type not in ID_WIDTHS:
        msg = f"Unknown ID type: {id_type}"
        raise ValueError(msg)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        current = data_access.get_sequence_value(id_type)
        if data_access.compare_and_set_sequence(id_type, current, current + 1):
            return format_id(id_type, current + 1)
        logger.info(
            "ID sequence %s changed concurrently (attempt %d/%d), retrying",
            id_type,
            attempt,
            MAX_ATTEMPTS,
        )

    msg = f"Could not reserve a new {id_type} ID after {MAX_ATTEMPTS} attempts."
    raise IdGenerationError(msg)
