from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
import yaml

from littrans.math_vision import (
    generate_math_candidates,
    set_http_transport,
)
from littrans.models import SemanticStatus, SourceUnit, UnitKind
from littrans.storage import sha256_file, sha256_text


def _project(tmp_path: Path, *, unit_count: int = 1) -> tuple[Path, bytes]:
    root = tmp_path / "project"
    (root / "derived").mkdir(parents=True)
    (root / "overrides").mkdir()
    (root / "source").mkdir()
    pdf = root / "source" / "source.pdf"
    document = fitz.open()
    page = document.new_page()
    units: list[SourceUnit] = []
    for index in range(unit_count):
        y = 80 + index * 70
        page.insert_text((80, y + 20), f"x_{index} = y_{index} + 1")
        units.append(
            SourceUnit(
                unit_id=f"p0001-u{index + 1:03d}-math",
                kind=UnitKind.EQUATION,
                page=1,
                bbox=(70, y, 180, y + 30),
                source_text=f"x_{index} = y_{index} + 1",
                source_hash=sha256_text(f"x_{index} = y_{index} + 1"),
                math_status=SemanticStatus.UNVERIFIED,
                confidence=0.5,
            )
        )
    document.save(pdf)
    document.close()
    (root / "derived" / "units.jsonl").write_text(
        "".join(unit.model_dump_json() + "\n" for unit in units), encoding="utf-8"
    )
    config = {
        "schema_version": 5,
        "project_id": "math-test",
        "title": "math test",
        "source_path": "source/source.pdf",
        "source_sha256": sha256_file(pdf),
        "source_pages": 1,
        "profile": "research-paper",
    }
    (root / "project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    units_bytes = (root / "derived" / "units.jsonl").read_bytes()
    return root, units_bytes


@pytest.fixture(autouse=True)
def reset_transport_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    set_http_transport(None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    yield
    set_http_transport(None)


# The historical backend is not a v6 production path. Preserve deterministic
# parsing/security primitives and assert that old project writes are rejected.

@pytest.mark.parametrize("options", [
    {}, {"allow_remote": True}, {"allow_remote": True, "force": True},
    {"allow_remote": True, "max_cost_usd": 0.0},
    {"allow_remote": True, "sampling": "random"},
])
def test_historical_backend_requires_rebuild_before_any_transport_or_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "historical-test-key")
    called = False
    def transport(**kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("Historical backend must never send a request")
    set_http_transport(transport)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="project rebuild OLD NEW"):
        generate_math_candidates(root, unit_ids=["p0001-u001-math"], **options)
    assert not called
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    from littrans.fidelity import prepare_source
    from littrans.project import rebuild_project
    rebuilt = tmp_path / "rebuilt"
    assert rebuild_project(root, rebuilt).schema_version == 6
    result = prepare_source(rebuilt, allow_missing_layout=True)
    assert result["requires_visual_review"]
    assert not (rebuilt / "evidence" / "math" / "candidates.jsonl").exists()


@pytest.mark.parametrize(("raw", "expected"), [("(2.11)", "2.11"), (" 7.3 ", "7.3"), ("((a))", "((a))"), ("", None), (None, None)])
def test_equation_number_normalization_is_offline(raw: Any, expected: str | None) -> None:
    from littrans.math_vision import _normalize_equation_number
    assert _normalize_equation_number(raw) == expected


@pytest.mark.parametrize("body", [b"", b"not-json", b'{"choices": []}'])
def test_invalid_saved_response_is_rejected_offline(body: bytes) -> None:
    from littrans.math_vision import _decode_provider_json
    with pytest.raises(ValueError):
        _decode_provider_json(body)


def test_saved_response_parser_preserves_outer_usage_and_candidate_content() -> None:
    from littrans.math_vision import _decode_provider_json, _usage
    candidate = {"candidates": [{"unit_id": "u1", "latex": "x=y+1"}]}
    raw = {"choices": [{"message": {"content": json.dumps(candidate)}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    decoded = _decode_provider_json(json.dumps(raw).encode())
    assert decoded["candidates"] == candidate["candidates"]
    usage = _usage(decoded, 1)
    assert usage.input_tokens == 11 and usage.output_tokens == 7


def test_redaction_primitives_remove_keys_and_image_transport_data() -> None:
    from littrans.math_vision import _redact_sensitive_text, _sanitize_response_body
    secret = "synthetic-secret-key"
    raw = f"Authorization: Bearer {secret}; image=data:image/png;base64,QUJDREVGRw=="
    for sanitized in (_redact_sensitive_text(raw, secret), _sanitize_response_body(raw.encode(), secret).decode()):
        assert secret not in sanitized
        assert "base64," not in sanitized
    non_utf8 = json.loads(_sanitize_response_body(b"\xff\xfe", secret))
    assert non_utf8["redacted_non_utf8_response"] is True
    assert len(non_utf8["original_sha256"]) == 64


def test_tiny_formula_context_padding_retains_structural_fragment_warning() -> None:
    from littrans.math_vision import _crop_padding, _looks_like_structural_fragment
    unit = SourceUnit(unit_id="tiny", kind=UnitKind.PARAGRAPH, page=1, bbox=(0, 0, 5, 10), source_text="(x)", source_hash="a" * 64, confidence=0.5)
    assert _looks_like_structural_fragment(unit)
    tight = _crop_padding(unit, 1)
    context = _crop_padding(unit, 2)
    assert context[0] > tight[0] and context[1] > tight[1]
    assert context[2] == "possible-structural-fragment"


def test_historical_crop_path_resolution_rejects_escape(tmp_path: Path) -> None:
    from littrans.math_vision import _safe_project_file
    (tmp_path / "original.png").write_bytes(b"fixture")
    assert _safe_project_file(tmp_path, "original.png") == tmp_path / "original.png"
    assert _safe_project_file(tmp_path, "../outside.png") is None


def test_stable_sampling_does_not_depend_on_input_order() -> None:
    from littrans.math_vision import _selected_units
    units = [SourceUnit(unit_id=f"u{i}", kind=UnitKind.EQUATION, page=1, bbox=(0, i * 30, 20, i * 30 + 20), source_text="x=y", source_hash="a" * 64, math_status=SemanticStatus.UNVERIFIED, confidence=0.5) for i in range(4)]
    first = _selected_units(units, {1}, "random", "b" * 64, None)
    second = _selected_units(reversed(units), {1}, "random", "b" * 64, None)
    assert [u.unit_id for u in first] == [u.unit_id for u in second]
