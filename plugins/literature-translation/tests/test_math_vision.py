from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz
import pytest
import yaml

from littrans.math_vision import (
    MAX_PILOT_UNITS,
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


def _success_body(unit_ids: list[str]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "candidates": [
                                {
                                    "unit_id": unit_id,
                                    "classification": "display",
                                    "latex": r"x=y+1",
                                }
                                for unit_id in unit_ids
                            ]
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def test_remote_authorization_and_key_gate_do_not_touch_authoritative_files(
    tmp_path: Path,
) -> None:
    root, units_bytes = _project(tmp_path)
    with pytest.raises(ValueError, match="non-empty sequence of exact unit_ids"):
        generate_math_candidates(root)
    with pytest.raises(ValueError, match="at least one non-empty exact unit ID"):
        generate_math_candidates(root, unit_ids=[])
    with pytest.raises(ValueError, match=f"limited to {MAX_PILOT_UNITS}"):
        generate_math_candidates(
            root,
            unit_ids=[f"p0001-u{index:03d}-math" for index in range(MAX_PILOT_UNITS + 1)],
        )
    with pytest.raises(PermissionError, match="allow_remote"):
        generate_math_candidates(root, unit_ids=["p0001-u001-math"])
    with pytest.raises(PermissionError, match="DEEPSEEK_API_KEY"):
        generate_math_candidates(
            root,
            allow_remote=True,
            unit_ids=["p0001-u001-math"],
        )
    assert (root / "derived" / "units.jsonl").read_bytes() == units_bytes
    assert not (root / "evidence" / "math" / "candidates.jsonl").exists()
    assert not (root / ".littrans" / "work" / "math-vision").exists()


def test_request_shape_crop_evidence_and_idempotency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, units_bytes = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        calls.append(kwargs)
        body = kwargs["json"]
        content = body["messages"][1]["content"]
        assert body["model"] == "deepseek-v4-flash-vision-exp"
        assert body["max_tokens"] == 2048
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert "outer parentheses" in body["messages"][0]["content"]
        assert "complete `source_markdown`" in body["messages"][0]["content"]
        assert "target_region_px" in content[0]["text"]
        assert "existing_latex" not in content[0]["text"]
        assert content[1]["image_url"]["detail"] == "original"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return 200, _success_body(["p0001-u001-math"]), {}

    set_http_transport(transport)
    first = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    second = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    forced = generate_math_candidates(
        root,
        allow_remote=True,
        force=True,
        unit_ids=["p0001-u001-math"],
    )
    assert first["generated"] == first["written"] == 2
    assert second["generated"] == 0 and second["reused"] == 1
    assert forced["generated"] == 0 and forced["reused"] == 1
    assert len(calls) == 2
    records = [
        json.loads(line)
        for line in (root / "evidence" / "math" / "candidates.jsonl").read_text().splitlines()
    ]
    assert {record["pass_index"] for record in records} == {1, 2}
    assert all(record["prompt_version"].startswith("math-vision-v2:") for record in records)
    assert len({record["request_sha256"] for record in records}) == 2
    crop_sizes: dict[int, tuple[int, int]] = {}
    for record in records:
        crop_path = root / record["crop_path"]
        raw_path = root / record["raw_response_path"]
        assert crop_path.is_file() and raw_path.is_file()
        assert crop_path.read_bytes().startswith(b"\x89PNG")
        assert "secret" not in raw_path.read_text(encoding="utf-8")
        with fitz.open(crop_path) as crop_document:
            crop_sizes[record["pass_index"]] = (
                crop_document[0].get_pixmap().width,
                crop_document[0].get_pixmap().height,
            )
    assert crop_sizes[2][0] > crop_sizes[1][0]
    assert crop_sizes[2][1] > crop_sizes[1][1]
    with fitz.open(root / "source" / "source.pdf") as source_document:
        full_width = source_document[0].rect.width * 2.5
        full_height = source_document[0].rect.height * 2.5
    assert all(
        width / full_width <= 0.85
        and height / full_height <= 0.85
        and (width * height) / (full_width * full_height) <= 0.5
        for width, height in crop_sizes.values()
    )
    prompts = [call["json"]["messages"][1]["content"][0]["text"] for call in calls]
    assert "pass 1" in prompts[0]
    assert "pass 2" in prompts[1]
    assert "x=y+1" not in prompts[1]
    assert (root / "derived" / "units.jsonl").read_bytes() == units_bytes
    assert not list((root / "overrides").iterdir())
    assert not (root / "derived" / "verification.json").exists()


def test_pass_two_marks_tiny_targets_as_possible_structural_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    fragment = SourceUnit(
        unit_id="p0001-u001-math",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(78, 90, 90, 102),
        source_text="X",
        source_hash=sha256_text("X"),
        source_markdown="$X$",
        math_status=SemanticStatus.UNVERIFIED,
        confidence=0.5,
    )
    (root / "derived" / "units.jsonl").write_text(
        fragment.model_dump_json() + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    metadata_by_pass: dict[int, dict[str, Any]] = {}

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        prompt = kwargs["json"]["messages"][1]["content"][0]["text"]
        pass_index = 1 if "pass 1" in prompt else 2
        metadata = json.loads(
            prompt.split("Input metadata:\n", 1)[1].split("\nRequired JSON shape:", 1)[0]
        )
        metadata_by_pass[pass_index] = metadata["units"][0]
        return (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "unit_id": fragment.unit_id,
                                            "classification": "needs-split",
                                            "latex": None,
                                            "source_markdown": None,
                                            "equation_number": None,
                                            "uncertainties": [
                                                "Target is only a glyph from a neighboring display."
                                            ],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
            {},
        )

    set_http_transport(transport)
    result = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])

    assert result["generated"] == 2
    assert metadata_by_pass[1]["context_profile"] == "tight-target"
    assert metadata_by_pass[2]["context_profile"] == "possible-structural-fragment"
    assert metadata_by_pass[2]["ocr_locator_hint_non_authoritative"] == "X"
    assert metadata_by_pass[2]["target_region_px"] != [0, 0, *metadata_by_pass[2]["crop_size_px"]]
    assert all(
        left < right
        for left, right in zip(
            metadata_by_pass[2]["target_region_px"][:2],
            metadata_by_pass[2]["target_region_px"][2:],
            strict=True,
        )
    )


def test_equation_numbers_are_persisted_without_outer_parentheses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        return (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "unit_id": "p0001-u001-math",
                                            "classification": "display",
                                            "latex": "x=y+1",
                                            "equation_number": "(2.11)",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
            {},
        )

    set_http_transport(transport)
    result = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])

    assert result["generated"] == 2
    records = [
        json.loads(line)
        for line in (root / "evidence" / "math" / "candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {record["equation_number"] for record in records} == {"2.11"}
    assert any(
        "(2.11)" in path.read_text(encoding="utf-8")
        for path in (root / ".littrans" / "work" / "math-vision").glob("response-*.json")
    )


@pytest.mark.parametrize(
    "responses",
    [
        [(200, b"", {})],
        [(200, b"not-json", {})],
        [(429, {"error": "busy"}, {}), (500, {"error": "down"}, {})],
    ],
)
def test_empty_invalid_and_retryable_responses_are_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[tuple[int, Any, dict[str, str]]],
) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls = 0
    response_count = len(responses)

    def transport(**kwargs: Any) -> tuple[int, Any, dict[str, str]]:
        nonlocal calls
        calls += 1
        if responses:
            status, body, headers = responses.pop(0)
            if isinstance(body, (dict, list)):
                return status, body, headers
            return status, body, headers
        return 200, _success_body(["p0001-u001-math"]), {}

    set_http_transport(transport)
    result = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert result["generated"] == 2
    assert calls == response_count + 2


def test_budget_stops_before_remote_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls = 0

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        nonlocal calls
        calls += 1
        return 200, _success_body(["p0001-u001-math"]), {}

    set_http_transport(transport)
    result = generate_math_candidates(
        root,
        allow_remote=True,
        max_cost_usd=0.000001,
        unit_ids=["p0001-u001-math"],
    )
    assert result["budget_exhausted"] is True
    assert result["generated"] == 0
    assert calls == 0
    assert not (root / "evidence" / "math" / "candidates.jsonl").exists()


def test_network_error_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls = 0

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("connection reset")
        return 200, _success_body(["p0001-u001-math"]), {}

    set_http_transport(transport)
    result = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert result["generated"] == 2
    assert calls == 4


def test_response_and_exception_evidence_redacts_credentials_and_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    secret = "highly-secret-token"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    calls = 0

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            image = kwargs["json"]["messages"][1]["content"][1]["image_url"]["url"]
            raise TimeoutError(f"Authorization: Bearer {secret}; request={image}")
        response = _success_body(["p0001-u001-math"])
        response["echoed_authorization"] = f"Bearer {secret}"
        response["echoed_image"] = "data:image/png;base64,QUJDREVGRw=="
        return 200, response, {}

    set_http_transport(transport)
    result = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert result["generated"] == 2
    serialized_result = json.dumps(result)
    assert secret not in serialized_result
    assert "base64," not in serialized_result
    for path in (root / ".littrans" / "work" / "math-vision").glob("response-*.json"):
        evidence = path.read_text(encoding="utf-8")
        assert secret not in evidence
        assert "base64," not in evidence


def test_exact_unit_ids_and_verified_units_are_strictly_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, units_before = _project(tmp_path, unit_count=2)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls: list[list[str]] = []

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        metadata = json.loads(
            kwargs["json"]["messages"][1]["content"][0]["text"]
            .split("Input metadata:\n", 1)[1]
            .split("\nRequired JSON shape:", 1)[0]
        )
        ids = [item["unit_id"] for item in metadata["units"]]
        calls.append(ids)
        return 200, _success_body(ids), {}

    set_http_transport(transport)
    result = generate_math_candidates(
        root,
        allow_remote=True,
        unit_ids=["p0001-u002-math"],
    )
    assert result["selected_units"] == 1
    assert calls == [["p0001-u002-math"], ["p0001-u002-math"]]
    assert (root / "derived" / "units.jsonl").read_bytes() == units_before
    with pytest.raises(ValueError, match="unknown unit_ids"):
        generate_math_candidates(root, unit_ids=["missing"])

    records = [
        json.loads(line) for line in (root / "derived" / "units.jsonl").read_text().splitlines()
    ]
    records[0]["math_status"] = "verified"
    (root / "derived" / "units.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="already verified"):
        generate_math_candidates(
            root,
            allow_remote=True,
            unit_ids=["p0001-u001-math"],
        )


def test_resume_reissues_only_the_stale_independent_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    observed_passes: list[int] = []

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        prompt = kwargs["json"]["messages"][1]["content"][0]["text"]
        observed_passes.append(1 if "pass 1" in prompt else 2)
        return 200, _success_body(["p0001-u001-math"]), {}

    set_http_transport(transport)
    generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert observed_passes == [1, 2]
    records = [
        json.loads(line)
        for line in (root / "evidence" / "math" / "candidates.jsonl").read_text().splitlines()
    ]
    pass_two = next(record for record in records if record["pass_index"] == 2)
    (root / pass_two["raw_response_path"]).write_text("tampered", encoding="utf-8")

    observed_passes.clear()
    resumed = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert resumed["pending_units"] == 1
    assert resumed["pending_candidates"] == 1
    assert resumed["generated"] == 1
    assert observed_passes == [2]

    observed_passes.clear()
    final = generate_math_candidates(root, allow_remote=True, unit_ids=["p0001-u001-math"])
    assert final["pending_units"] == 0
    assert final["reused"] == 1
    assert observed_passes == []


def test_full_page_or_oversized_crop_is_refused_before_remote_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _project(tmp_path)
    with fitz.open(root / "source" / "source.pdf") as document:
        page_rect = document[0].rect
    unit = SourceUnit(
        unit_id="p0001-u001-math",
        kind=UnitKind.EQUATION,
        page=1,
        bbox=tuple(page_rect),
        source_text="x_0 = y_0 + 1",
        source_hash=sha256_text("x_0 = y_0 + 1"),
        math_status=SemanticStatus.UNVERIFIED,
        confidence=0.5,
    )
    (root / "derived" / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    calls = 0

    def transport(**kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
        nonlocal calls
        calls += 1
        return 200, _success_body([unit.unit_id]), {}

    set_http_transport(transport)
    with pytest.raises(ValueError, match="privacy-bounded local crop"):
        generate_math_candidates(
            root,
            allow_remote=True,
            unit_ids=[unit.unit_id],
        )

    assert calls == 0
    assert not (root / "evidence" / "math" / "candidates.jsonl").exists()
