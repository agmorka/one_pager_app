"""Which identity each LakehouseAccess statement runs as (identity plan Phase 4).

Reads run as the signed-in user so Unity Catalog group grants apply; writes,
and the ID-sequence read that is part of a write, run as the service
principal (Architecture.md §8, Decision_Log §19).
"""

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st

from onepagerapp.config import AppConfig
from onepagerapp.data_access import connection as connection_module
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.connection import (
    USER_TOKEN_HEADER,
    Identity,
    MissingUserTokenError,
)
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.documents import OnePagerDocumentStore
from tests.helpers import (
    FIXTURES_DIR,
    LAKEHOUSE_ACTOR_WRITES,
    LAKEHOUSE_OTHER_WRITES,
    LAKEHOUSE_READS,
    LAKEHOUSE_READS_AS_APP,
    answer_every_statement,
    failing,
    fake_connection,
    is_write,
    lakehouse_access,
)

WRITES = {
    **LAKEHOUSE_ACTOR_WRITES,
    **{name: call for name, (_, call) in LAKEHOUSE_OTHER_WRITES.items()},
}


def _statements(
    call: Callable[[LakehouseAccess], object],
) -> list[tuple[str, Identity]]:
    """Run ``call`` on a fake connection; return each statement and its identity."""
    conn = fake_connection(handler=answer_every_statement)
    access = lakehouse_access(conn)
    access._document_store = OnePagerDocumentStore(FIXTURES_DIR)
    # The fake's empty results may not parse; the statements ran regardless.
    with suppress(RuntimeError, KeyError, ValueError, AttributeError):
        call(access)
    return [
        (s, identity)
        for (s, _), identity in zip(conn.calls, conn.identities, strict=True)
    ]


def _deployed_access(tmp_path: Path) -> LakehouseAccess:
    """Return a ``LakehouseAccess`` in databricks mode."""
    return lakehouse.LakehouseAccess(
        AppConfig(APP_MODE="databricks", ONE_PAGER_APP_VOLUME_PATH=str(tmp_path)),
        OnePagerDocumentStore(tmp_path),
    )


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(LAKEHOUSE_READS))
def test__read_method__call__runs_as_the_user_and_never_writes(method: str) -> None:
    """Reads run as the signed-in user, so Unity Catalog grants apply."""
    # When
    statements = _statements(LAKEHOUSE_READS[method])

    # Then
    assert statements, f"{method} ran no statement"
    assert {identity for _, identity in statements} == {Identity.USER}
    assert not [s for s, _ in statements if is_write(s)], f"{method} wrote data"


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(LAKEHOUSE_READS_AS_APP))
def test__read_that_is_part_of_a_write__call__runs_as_the_service_principal(
    method: str,
) -> None:
    """A read inside a write must see what the writer sees."""
    # When
    statements = _statements(LAKEHOUSE_READS_AS_APP[method])

    # Then
    assert statements
    assert {identity for _, identity in statements} == {Identity.APP}


@pytest.mark.unit
@pytest.mark.parametrize("method", sorted(WRITES))
def test__write_method__call__runs_as_the_service_principal(method: str) -> None:
    """Writes run as the app's service principal."""
    # When
    statements = _statements(WRITES[method])

    # Then
    assert statements, f"{method} ran no statement"
    assert {identity for _, identity in statements} == {Identity.APP}


@pytest.mark.unit
def test__lakehouse_access__public_methods__each_has_an_expected_identity() -> None:
    """A new public LakehouseAccess method must be added to one of the lists."""
    # When
    public = {
        name
        for name in vars(LakehouseAccess)
        if not name.startswith("_") and callable(getattr(LakehouseAccess, name))
    }

    # Then
    classified = (set(LAKEHOUSE_READS) | set(LAKEHOUSE_READS_AS_APP) | set(WRITES)) | {
        "read_document"
    }
    assert public == classified


@pytest.mark.unit
def test__deployed_without_user_token__read__refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No fallback to the service principal when the token header is missing."""
    # Given
    service_principal = SimpleNamespace(
        config=SimpleNamespace(host="https://adb.example"),
        statement_execution=SimpleNamespace(
            execute_statement=failing("must not run as the service principal")
        ),
    )
    monkeypatch.setattr(connection_module, "WorkspaceClient", lambda: service_principal)
    monkeypatch.setattr(st, "context", SimpleNamespace(headers={}))

    # When / Then
    with pytest.raises(MissingUserTokenError):
        _deployed_access(tmp_path).get_one_pager_status_row("OP-0001")


@pytest.mark.unit
def test__deployed_with_user_token__read__client_built_with_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The forwarded user token authenticates the read."""
    # Given
    built: list[dict[str, object]] = []

    def workspace_client(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        return SimpleNamespace(
            config=SimpleNamespace(host="https://adb.example"),
            statement_execution=SimpleNamespace(
                execute_statement=lambda **_: SimpleNamespace(
                    status=None, manifest=None, result=None
                )
            ),
        )

    monkeypatch.setattr(connection_module, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(
        st, "context", SimpleNamespace(headers={USER_TOKEN_HEADER: "user-token"})
    )

    # When
    row = _deployed_access(tmp_path).get_one_pager_status_row("OP-0001")

    # Then
    assert row is None
    assert built[-1]["token"] == "user-token"  # noqa: S105 - a test value
