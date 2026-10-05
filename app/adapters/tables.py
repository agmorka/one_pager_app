"""Clickable tables: ``st.dataframe`` with a single-row selection."""

import pandas as pd


def selected_value(event: object, frame: pd.DataFrame, column: str) -> str | None:
    """``column`` of the row the user clicked in ``frame``, or None.

    ``event`` is what ``st.dataframe(..., on_select="rerun")`` returns.
    """
    rows = getattr(getattr(event, "selection", None), "rows", None) or []
    if not rows or rows[0] >= len(frame):
        return None
    return str(frame.iloc[rows[0]][column])
