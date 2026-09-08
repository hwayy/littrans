import pytest

from littrans.models import SourceUnit, UnitKind
from littrans.quality import (
    NUMBER_RE,
    UNIT_RE,
    _localized_heading_token_present,
    _semantic_comparison_text,
    _semantic_token_present,
    _token_counts,
)


@pytest.mark.parametrize(("source", "target"), [
    ("1940s", "20世纪40年代"), ("2000s", "21 世纪 00 年代"),
    ("1990s and 1940s", "20世纪90年代和20世纪40年代"),
])
def test_explicit_decades_preserve_numeric_identity(source: str, target: str) -> None:
    for pattern in (NUMBER_RE, UNIT_RE):
        assert _token_counts(pattern, _semantic_comparison_text(source)) == _token_counts(
            pattern, _semantic_comparison_text(target))


@pytest.mark.parametrize("target", ["19世纪40年代", "20世纪50年代", "20世纪41年代"])
def test_wrong_decades_still_fail(target: str) -> None:
    assert _token_counts(NUMBER_RE, _semantic_comparison_text("1940s")) != _token_counts(
        NUMBER_RE, _semantic_comparison_text(target))


@pytest.mark.parametrize("source", ["1940 s", "10s", "1941s", "10 ms", "1940 kg"])
def test_actual_quantities_keep_their_units(source: str) -> None:
    assert _token_counts(UNIT_RE, _semantic_comparison_text(source)) == _token_counts(UNIT_RE, source)
    assert _token_counts(UNIT_RE, _semantic_comparison_text(source))


@pytest.mark.parametrize(("kind", "source", "token", "target", "expected"), [
    (UnitKind.HEADING, "CHAPTER 1", "CHAPTER", "第 1 章", True),
    (UnitKind.HEADING, "CHAPTER 1 Introduction", "CHAPTER", "第1章 导言", True),
    (UnitKind.HEADING, "CHAPTER 1", "CHAPTER", "第 2 章", False),
    (UnitKind.PARAGRAPH, "CHAPTER 1", "CHAPTER", "第 1 章", False),
    (UnitKind.HEADING, "CHAPTER API", "CHAPTER", "第 1 章", False),
    (UnitKind.HEADING, "CHAPTER 1 API", "API", "第 1 章", False),
    (UnitKind.HEADING, "CHAPTER 1", "CHAPTER", "本章", False),
])
def test_heading_equivalence_is_scoped(kind: UnitKind, source: str, token: str,
                                      target: str, expected: bool) -> None:
    unit = SourceUnit(unit_id="test", kind=kind, page=1, bbox=(0, 0, 1, 1),
                      source_text=source, source_hash="test", confidence=1)
    assert _localized_heading_token_present(unit, token, target) is expected
    assert not _semantic_token_present(token, target, _semantic_comparison_text(target))
