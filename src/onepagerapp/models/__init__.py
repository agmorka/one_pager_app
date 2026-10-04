"""Domain models: the data that flows between the application layers.

Modules:

- ``registry``: Registry page (filter, sort, status reference data, rows).
- ``one_pager``: a One Pager (document, status row, change log, review
  comments, lock, authorized users, Preview data).
- ``users``: the signed-in user, Owner/SMEs, create input and results,
  validation errors.
- ``use_cases``: the shared Use Case registry.
- ``pagination``: page arithmetic of paginated results.

Everything is re-exported here, so callers import from ``onepagerapp.models``.
"""

from onepagerapp.models.one_pager import (
    AuthorizedUser,
    ChangeLogEntry,
    LockInfo,
    OnePagerDocument,
    OnePagerHeader,
    OnePagerStatusRow,
    PreviewData,
    ReviewComment,
)
from onepagerapp.models.pagination import Pagination
from onepagerapp.models.registry import (
    REGISTRY_SORT_COLUMNS,
    RegistryFilter,
    RegistryPage,
    RegistryRow,
    RegistrySort,
    StatusRef,
)
from onepagerapp.models.use_cases import (
    PRIORITY_OPTIONS,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
    UseCasePage,
)
from onepagerapp.models.users import (
    CreateResult,
    CurrentUser,
    NewOnePagerInput,
    PersonRef,
    ValidationError,
)

__all__ = [
    "PRIORITY_OPTIONS",
    "REGISTRY_SORT_COLUMNS",
    "AuthorizedUser",
    "ChangeLogEntry",
    "CreateResult",
    "CurrentUser",
    "LockInfo",
    "NewOnePagerInput",
    "OnePagerDocument",
    "OnePagerHeader",
    "OnePagerStatusRow",
    "Pagination",
    "PersonRef",
    "PreviewData",
    "RegistryFilter",
    "RegistryPage",
    "RegistryRow",
    "RegistrySort",
    "ReviewComment",
    "StatusRef",
    "UseCase",
    "UseCaseFilter",
    "UseCaseInput",
    "UseCasePage",
    "ValidationError",
]
