"""Caches of the lists and the reference data (UI_Design.md §6).

The Registry list, its status counts and the Use Case lists are cached for
``LIST_CACHE_TTL_SECONDS`` and cleared after every write that can change them.
Wrap each such write in ``writes_data()``. Lock status, the review queue and
editor content are never cached.

Cache entries are keyed by ``DataAccess.cache_scope``: sessions that read the
same tables share them, and a write in one session clears them for all.
Other app instances catch up when the TTL expires.

The status reference tables (badge colours, legends) change only through the
Admin page, which clears every cache; they are kept for
``REFERENCE_CACHE_TTL_SECONDS`` so other app instances catch up too.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import astuple

import pandas as pd
import streamlit as st

from adapters.theme import get_dp_status_colors, get_op_status_colors
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import (
    RegistryFilter,
    RegistryPage,
    RegistrySort,
    UseCaseFilter,
    UseCasePage,
)

LIST_CACHE_TTL_SECONDS = 30
REFERENCE_CACHE_TTL_SECONDS = 3600


# Arguments with a leading underscore are not hashed by st.cache_data; the
# hashed ``scope`` and ``key`` stand in for them.


@st.cache_data(ttl=LIST_CACHE_TTL_SECONDS, show_spinner=False)
def _registry(
    scope: str,  # noqa: ARG001 - cache key
    key: tuple,  # noqa: ARG001 - cache key
    _data_access: DataAccess,
    _filter: RegistryFilter,
    _sort: RegistrySort,
    page: int,
    page_size: int,
) -> RegistryPage:
    return _data_access.get_registry(_filter, page, page_size, _sort)


@st.cache_data(ttl=LIST_CACHE_TTL_SECONDS, show_spinner=False)
def _registry_status_counts(
    scope: str,  # noqa: ARG001 - cache key
    key: tuple,  # noqa: ARG001 - cache key
    _data_access: DataAccess,
    _filter: RegistryFilter,
) -> dict[str, int]:
    return _data_access.get_registry_status_counts(_filter)


@st.cache_data(ttl=LIST_CACHE_TTL_SECONDS, show_spinner=False)
def _use_cases(
    scope: str,  # noqa: ARG001 - cache key
    key: tuple,  # noqa: ARG001 - cache key
    _data_access: DataAccess,
    _filter: UseCaseFilter,
    page: int,
    page_size: int,
) -> UseCasePage:
    return _data_access.get_use_cases(_filter, page, page_size)


def get_registry(
    data_access: DataAccess,
    filter: RegistryFilter,  # noqa: A002 - matches DataAccess.get_registry
    page: int,
    page_size: int,
    sort: RegistrySort | None = None,
) -> RegistryPage:
    """Return ``DataAccess.get_registry``, cached."""
    sort = sort or RegistrySort()
    key = (astuple(filter), astuple(sort))
    return _registry(
        data_access.cache_scope, key, data_access, filter, sort, page, page_size
    )


def get_registry_status_counts(
    data_access: DataAccess,
    filter: RegistryFilter,  # noqa: A002 - matches DataAccess
) -> dict[str, int]:
    """Return ``DataAccess.get_registry_status_counts``, cached."""
    return _registry_status_counts(
        data_access.cache_scope, astuple(filter), data_access, filter
    )


def get_use_cases(
    data_access: DataAccess,
    filter: UseCaseFilter,  # noqa: A002 - matches DataAccess.get_use_cases
    page: int,
    page_size: int,
) -> UseCasePage:
    """Return ``DataAccess.get_use_cases``, cached."""
    return _use_cases(
        data_access.cache_scope, astuple(filter), data_access, filter, page, page_size
    )


@st.cache_data(ttl=REFERENCE_CACHE_TTL_SECONDS, show_spinner=False)
def _ref_op_status(scope: str, _data_access: DataAccess) -> pd.DataFrame:  # noqa: ARG001 - cache key
    return _data_access.get_ref_op_status()


@st.cache_data(ttl=REFERENCE_CACHE_TTL_SECONDS, show_spinner=False)
def _ref_dp_status(scope: str, _data_access: DataAccess) -> pd.DataFrame:  # noqa: ARG001 - cache key
    return _data_access.get_ref_dp_status()


@st.cache_data(ttl=REFERENCE_CACHE_TTL_SECONDS, show_spinner=False)
def _op_status_colors(scope: str, _data_access: DataAccess) -> dict[str, str]:  # noqa: ARG001 - cache key
    return get_op_status_colors(_data_access)


@st.cache_data(ttl=REFERENCE_CACHE_TTL_SECONDS, show_spinner=False)
def _dp_status_colors(scope: str, _data_access: DataAccess) -> dict[str, str]:  # noqa: ARG001 - cache key
    return get_dp_status_colors(_data_access)


def get_ref_op_status(data_access: DataAccess) -> pd.DataFrame:
    """Return ``DataAccess.get_ref_op_status``, cached."""
    return _ref_op_status(data_access.cache_scope, data_access)


def get_ref_dp_status(data_access: DataAccess) -> pd.DataFrame:
    """Return ``DataAccess.get_ref_dp_status``, cached."""
    return _ref_dp_status(data_access.cache_scope, data_access)


def op_status_colors(data_access: DataAccess) -> dict[str, str]:
    """Return ``theme.get_op_status_colors``, cached."""
    return _op_status_colors(data_access.cache_scope, data_access)


def dp_status_colors(data_access: DataAccess) -> dict[str, str]:
    """Return ``theme.get_dp_status_colors``, cached."""
    return _dp_status_colors(data_access.cache_scope, data_access)


def invalidate_list_caches() -> None:
    """Drop every cached Registry and Use Case list."""
    _registry.clear()
    _registry_status_counts.clear()
    _use_cases.clear()


@contextmanager
def writes_data() -> Iterator[None]:
    """Clear the list caches after the wrapped write, even when it fails.

    A failed write may have changed some rows before it rolled back, so the
    caches are cleared either way.
    """
    try:
        yield
    finally:
        invalidate_list_caches()
