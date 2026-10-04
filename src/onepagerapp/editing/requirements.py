"""Business Requirement IDs (``BR-###``, Data_Model.md §4)."""

from typing import Any

from onepagerapp.data_access.base import DataAccess
from onepagerapp.id_generator import next_id


def assign_requirement_id(data_access: DataAccess, requirement: dict[str, Any]) -> str:
    """Give a new Business Requirement its ``BR-###`` ID.

    IDs come from the global ``id_sequences`` counter (Data_Model.md §4), so
    they are unique across all One Pagers. An ID consumed by a requirement
    that is never saved leaves a harmless gap.

    Raises:
        IdGenerationError: The counter could not be advanced.
        ValueError: The BR-### range is exhausted.

    """
    requirement["id"] = next_id(data_access, "BR")
    return str(requirement["id"])
