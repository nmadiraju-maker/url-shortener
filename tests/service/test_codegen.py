import pytest

from urlshort.codegen import ALPHABET, random_code


@pytest.mark.parametrize("length", [4, 7, 12])
def test_codes_use_base62_and_requested_length(length: int) -> None:
    code = random_code(length)
    assert len(code) == length and set(code) <= set(ALPHABET)


def test_codes_are_not_repeated_in_practice() -> None:
    assert len({random_code(7) for _ in range(2000)}) == 2000


def test_too_short_lengths_are_refused() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        random_code(3)
