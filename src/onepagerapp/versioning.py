"""One Pager document versions: MAJOR.MINOR.PATCH (Requirements_and_Scope.md §7).

Save Draft raises MINOR (``bump_minor``) and an approval raises MAJOR
(``next_major``). PATCH is not used. Pure Python — no Streamlit.
"""

VERSION_PARTS = 3


def parse_version(version: str) -> tuple[int, int, int]:
    """Split ``MAJOR.MINOR.PATCH`` into its numbers.

    Raises:
        ValueError: If ``version`` is not MAJOR.MINOR.PATCH.

    """
    parts = version.split(".")
    if len(parts) != VERSION_PARTS or not all(p.isdigit() for p in parts):
        msg = f"Invalid version {version!r}"
        raise ValueError(msg)
    major, minor, patch = (int(p) for p in parts)
    return major, minor, patch


def bump_minor(version: str) -> str:
    """Next MINOR version (Requirements_and_Scope.md §7): 0.3.0 -> 0.4.0.

    PATCH is not used, so it is reset to 0.

    Raises:
        ValueError: If ``version`` is not MAJOR.MINOR.PATCH.

    """
    major, minor, _ = parse_version(version)
    return f"{major}.{minor + 1}.0"


def next_major(version: str) -> str:
    """Version of an approval (Requirements_and_Scope.md §7).

    The first approval gives ``1.0.0`` (from any ``0.x.y``); every later one
    the next MAJOR: ``1.2.0`` -> ``2.0.0``.

    Raises:
        ValueError: If ``version`` is not MAJOR.MINOR.PATCH.

    """
    major, _, _ = parse_version(version)
    return f"{major + 1}.0.0"
