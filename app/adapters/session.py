"""Streamlit session helpers."""

import uuid

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

_SESSION_ID_KEY = "app_session_id"


def current_session_id() -> str:
    """ID of this browser session (tab), used to tell edit locks apart.

    Streamlit gives every browser tab its own session. The ID is remembered in
    session state so it stays the same for the whole session even when no
    script-run context is available (e.g. in tests).
    """
    if _SESSION_ID_KEY not in st.session_state:
        ctx = get_script_run_ctx()
        session_id = ctx.session_id if ctx is not None else ""
        st.session_state[_SESSION_ID_KEY] = session_id or uuid.uuid4().hex
    return str(st.session_state[_SESSION_ID_KEY])
