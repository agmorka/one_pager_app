"""ID formats and conflict-safe allocation from a sequence counter."""

from types import SimpleNamespace

import pytest

from onepagerapp.id_generator import (
    MAX_ATTEMPTS,
    IdGenerationError,
    format_id,
    next_id,
)


def _sequences(value: int, conflicts: int = 0) -> SimpleNamespace:
    """Return a stand-in for the two sequence methods of ``DataAccess``.

    The first ``conflicts`` compare-and-set calls lose to another writer, who
    advances the counter. ``cas_calls`` counts the compare-and-set calls.
    """
    fake = SimpleNamespace(value=value, conflicts=conflicts, cas_calls=0)

    def compare_and_set_sequence(_id_type: str, expected: int, new: int) -> bool:
        fake.cas_calls += 1
        if fake.conflicts > 0:
            fake.conflicts -= 1
            fake.value += 1
            return False
        if fake.value != expected:
            return False
        fake.value = new
        return True

    fake.get_sequence_value = lambda _id_type: fake.value
    fake.compare_and_set_sequence = compare_and_set_sequence
    return fake


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
def test__type_and_value__format_id__zero_padded_per_type(
    id_type: str, value: int, expected: str
) -> None:
    """OP IDs have four digits, UC and BR IDs three."""
    # When
    result = format_id(id_type, value)

    # Then
    assert result == expected


@pytest.mark.unit
def test__unknown_type__format_id__raises() -> None:
    """Only the known ID types are formatted."""
    # When / Then
    with pytest.raises(ValueError, match="Unknown ID type"):
        format_id("XX", 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("id_type", "value"), [("OP", 0), ("OP", 10000), ("UC", 1000), ("BR", 1000)]
)
def test__value_out_of_range__format_id__raises(id_type: str, value: int) -> None:
    """Values that do not fit the digits are refused."""
    # When / Then
    with pytest.raises(ValueError, match="outside"):
        format_id(id_type, value)


@pytest.mark.unit
def test__counter_at_6__next_id_twice__consecutive_ids() -> None:
    """Each call claims the next value."""
    # Given
    fake = _sequences(value=6)

    # When
    ids = [next_id(fake, "OP"), next_id(fake, "OP")]

    # Then
    assert ids == ["OP-0007", "OP-0008"]


@pytest.mark.unit
def test__two_concurrent_writers__next_id__retries_without_collision() -> None:
    """Two other writers took 7 and 8; this call gets 9 on the third try."""
    # Given
    fake = _sequences(value=6, conflicts=2)

    # When
    result = next_id(fake, "OP")

    # Then
    assert result == "OP-0009"
    assert fake.cas_calls == 3


@pytest.mark.unit
def test__conflict_on_every_attempt__next_id__gives_up() -> None:
    """After MAX_ATTEMPTS lost races the allocation fails."""
    # Given
    fake = _sequences(value=1, conflicts=MAX_ATTEMPTS)

    # When / Then
    with pytest.raises(IdGenerationError):
        next_id(fake, "OP")
    assert fake.cas_calls == MAX_ATTEMPTS


@pytest.mark.unit
def test__counter_at_maximum__next_id__raises_without_consuming() -> None:
    """An overflow is detected before the counter is moved."""
    # Given
    fake = _sequences(value=999)

    # When / Then
    with pytest.raises(ValueError, match="outside"):
        next_id(fake, "UC")
    assert (fake.value, fake.cas_calls) == (999, 0)
