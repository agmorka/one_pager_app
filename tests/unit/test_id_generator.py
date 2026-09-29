import pytest

from onepagerapp.id_generator import (
    MAX_ATTEMPTS,
    IdGenerationError,
    format_id,
    next_id,
)


class _FakeSequences:
    """Minimal DataAccess stand-in for the two sequence primitives."""

    def __init__(self, value: int, conflicts: int = 0) -> None:
        self.value = value
        self.conflicts = conflicts
        self.cas_calls = 0

    def get_sequence_value(self, id_type: str) -> int:  # noqa: ARG002
        return self.value

    def compare_and_set_sequence(
        self,
        id_type: str,  # noqa: ARG002
        expected: int,
        new: int,
    ) -> bool:
        self.cas_calls += 1
        if self.conflicts > 0:
            # Another writer advanced the counter first.
            self.conflicts -= 1
            self.value += 1
            return False
        if self.value != expected:
            return False
        self.value = new
        return True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("id_type", "value", "expected"),
    [
        ("OP", 1, "OP-0001"),
        ("OP", 1234, "OP-1234"),
        ("UC", 7, "UC-007"),
        ("BR", 42, "BR-042"),
    ],
)
def test__format_id(id_type: str, value: int, expected: str) -> None:
    assert format_id(id_type, value) == expected


@pytest.mark.unit
def test__format_id__unknown_type() -> None:
    with pytest.raises(ValueError, match="Unknown ID type"):
        format_id("XX", 1)


@pytest.mark.unit
def test__next_id__increments() -> None:
    fake = _FakeSequences(value=6)
    assert next_id(fake, "OP") == "OP-0007"
    assert next_id(fake, "OP") == "OP-0008"


@pytest.mark.unit
def test__next_id__retries_after_conflict_without_collision() -> None:
    fake = _FakeSequences(value=6, conflicts=2)
    # Two other writers took 7 and 8; this call must get 9.
    assert next_id(fake, "OP") == "OP-0009"
    assert fake.cas_calls == 3


@pytest.mark.unit
def test__next_id__gives_up_after_max_attempts() -> None:
    fake = _FakeSequences(value=1, conflicts=MAX_ATTEMPTS)
    with pytest.raises(IdGenerationError):
        next_id(fake, "OP")
    assert fake.cas_calls == MAX_ATTEMPTS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("id_type", "value"), [("OP", 0), ("OP", 10000), ("UC", 1000), ("BR", 1000)]
)
def test__format_id__out_of_range(id_type: str, value: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        format_id(id_type, value)


@pytest.mark.unit
def test__next_id__overflow_does_not_consume_a_value() -> None:
    fake = _FakeSequences(value=999)
    with pytest.raises(ValueError, match="outside"):
        next_id(fake, "UC")
    assert fake.value == 999
    assert fake.cas_calls == 0
