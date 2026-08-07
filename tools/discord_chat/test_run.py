"""Pure-function tests (no network)."""
from run import chunks


def test_chunks_respect_limit_and_lines():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(40))
    parts = chunks(text, size=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_chunks_hard_split_long_line():
    parts = chunks("y" * 4200, size=1900)
    assert [len(p) for p in parts] == [1900, 1900, 400]


def test_chunks_short_text_is_one_part():
    assert chunks("hello") == ["hello"]
