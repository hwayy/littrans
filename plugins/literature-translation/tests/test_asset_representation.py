from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
import pytest

from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.models import AssetTranslation, ProjectConfig, SourceUnit, TranslationRecord
from littrans.rendering import _asset_companions
from littrans.representations import (
    build_asset_packet,
    candidate_validation,
    import_asset_review,
    mathjax_bootstrap,
    representation_status,
    resolve_asset_html,
    resolve_asset_markdown,
    submit_candidates,
    validate_asset_references,
    validate_asset_translations,
)
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    save_project,
    write_json,
    write_jsonl,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    original = root / "original.pdf"
    document = fitz.open()
    page = document.new_page(width=80, height=40)
    page.insert_text((12, 25), "x = 1", fontsize=12)
    document.save(original)
    page.get_pixmap(matrix=fitz.Matrix(3, 3)).save(root / "original.png")
    (root / "original.svg").write_text(page.get_svg_image(), encoding="utf-8")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    save_project(root, ProjectConfig(project_id="asset-test", title="Assets", source_path="original.pdf",
                                    source_sha256=digest, source_pages=1, profile="technical-book"))
    assets = []
    for key in ("a1", "a2"):
        assets.append(FidelityAsset(id=key, kind="math", source_sha256=digest, content_sha256=hashlib.sha256(key.encode()).hexdigest(),
                                    fragments=[FidelityFragment(page=1, bbox=(0, 0, 80, 40),
                                        width=80, height=40, png_path="original.png", svg_path="original.svg",
                                        pdf_path="original.pdf", file_sha256={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                        for name in ("original.png", "original.svg", "original.pdf")})]))
    write_jsonl(root / "derived/fidelity-assets.jsonl", assets)
    write_jsonl(root / "derived/units.jsonl", [SourceUnit(unit_id="u1", kind="paragraph", page=1,
        bbox=(0, 0, 80, 40), source_text="For {{asset:a1}} and {{asset:a2}}.", source_hash="source-one", confidence=1)])
    return root


def candidate_input(root: Path, content: str = "x=1") -> dict:
    packet = build_asset_packet(root, ["a1"])
    payload = {"packet_id": packet["packet_id"], "author_task_id": "transcriber-1",
               "model": "gpt-5.6-luna", "reasoning_effort": "max",
               "image_evidence": packet["required_images"], "usage": None,
               "candidates": [{"asset_id": "a1", "format": "latex", "content": content}]}
    write_json(root / "candidate.json", payload)
    return payload


def test_image_companion_cannot_bypass_separate_math_review(project: Path) -> None:
    companion = AssetTranslation(asset_id="a1", target_text=r"条件为 $x=-1$。")
    errors = validate_asset_translations(project, "{{asset:a1}}", [companion])
    assert any(item["code"] == "candidate-in-asset-translation" for item in errors)


def review_input(root: Path, verdict: str = "accept") -> dict:
    packet = build_asset_packet(root, ["a1"], "asset-audit")
    payload = {"packet_id": packet["packet_id"], "reviewer_task_id": "reviewer-2",
               "render_artifact_sha256": packet["render_artifact"]["sha256"],
               "image_evidence": packet["required_images"],
               "decisions": [{"asset_id": "a1", "candidate_sha256": packet["candidates"]["a1"]["candidate_sha256"],
                 "verdict": verdict, "visual_checked": True, "render_checked": True, "notes": "Compared all symbols."}]}
    write_json(root / "review.json", payload)
    return payload


def test_independent_acceptance_keeps_original_until_browser_success(project: Path) -> None:
    candidate_input(project)
    result = submit_candidates(project, project / "candidate.json")
    assert result["usage"] is None
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"
    assert "data-candidate-latex" not in resolve_asset_html(project, "{{asset:a1}}", project)
    review_input(project)
    import_asset_review(project, project / "review.json", True)
    assert representation_status(project)["assets"]["a1"]["state"] == "verified"
    rendered = resolve_asset_html(project, "{{asset:a1}}", project)
    assert 'data-candidate-latex="x=1"' in rendered
    assert '<span class="asset-original">' in rendered
    assert '<span class="asset-candidate" hidden>' in rendered
    assert "查看原式" in rendered
    assert "tex2svgPromise" in mathjax_bootstrap()
    assert "data-mml-node" in mathjax_bootstrap()
    assert "https://" not in mathjax_bootstrap()
    assert "![原式 a1]" in resolve_asset_markdown(project, "{{asset:a1}}", project)


def test_exact_image_receipt_and_independent_task_required(project: Path) -> None:
    payload = candidate_input(project)
    payload["image_evidence"] = {}
    write_json(project / "candidate.json", payload)
    with pytest.raises(ValueError, match="viewing receipt"):
        submit_candidates(project, project / "candidate.json")
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    payload = review_input(project)
    payload["reviewer_task_id"] = "transcriber-1"
    write_json(project / "review.json", payload)
    with pytest.raises(ValueError, match="Self-review"):
        import_asset_review(project, project / "review.json", True)
    with pytest.raises(ValueError, match="confirmation"):
        import_asset_review(project, project / "review.json")


def test_cache_replay_is_idempotent_and_changed_response_rejected(project: Path) -> None:
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    assert submit_candidates(project, project / "candidate.json")["replayed"]
    review_input(project)
    import_asset_review(project, project / "review.json", True)
    assert import_asset_review(project, project / "review.json", True)["replayed"]
    changed = read_json(project / "review.json")
    changed["decisions"][0]["verdict"] = "reject"
    write_json(project / "review.json", changed)
    with pytest.raises(ValueError, match="Conflicting replay"):
        import_asset_review(project, project / "review.json", True)
    changed = read_json(project / "candidate.json")
    changed["candidates"][0]["content"] = "x=2"
    write_json(project / "candidate.json", changed)
    with pytest.raises(ValueError, match="already cached"):
        submit_candidates(project, project / "candidate.json")


def test_concurrent_duplicate_submissions_and_crash_recovery(project: Path) -> None:
    candidate_input(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit_candidates, project, project / "candidate.json") for _ in range(2)]
        results = [future.result() for future in futures]
    assert sorted(result["replayed"] for result in results) == [False, True]
    # Simulate a crash after immutable response publication but before indexing.
    write_json(project / "evidence/representations/index.json", {"candidates": {}, "reviews": {}})
    submit_candidates(project, project / "candidate.json")
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"


def test_duplicate_candidate_coverage_and_context_tampering_rejected(project: Path) -> None:
    payload = candidate_input(project)
    payload["candidates"] *= 2
    write_json(project / "candidate.json", payload)
    with pytest.raises(ValueError, match="coverage"):
        submit_candidates(project, project / "candidate.json")
    payload = candidate_input(project)
    packet_path = project / "evidence/representations/packets" / f"{payload['packet_id']}.json"
    packet = read_json(packet_path)
    packet["instructions"] = "Altered prompt"
    write_json(packet_path, packet)
    with pytest.raises(ValueError, match="modified"):
        submit_candidates(project, project / "candidate.json")


def test_stale_source_and_candidate_are_not_accepted(project: Path) -> None:
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    payload = review_input(project)
    payload["decisions"][0]["candidate_sha256"] = "f" * 64
    write_json(project / "review.json", payload)
    with pytest.raises(ValueError, match="stale candidate"):
        import_asset_review(project, project / "review.json", True)
    (project / "original.png").write_bytes(b"modified source")
    assert representation_status(project)["assets"]["a1"]["state"] == "transcribe"
    with pytest.raises(ValueError, match="hash mismatch"):
        submit_candidates(project, project / "candidate.json")


def test_missing_multiline_delimiter_cannot_be_accepted(project: Path) -> None:
    candidate_input(project, "V_0=e^{i\\phi Z}\nV_d=e^{i\\phi Z}WV_{d-1}W")
    submit_candidates(project, project / "candidate.json")
    review_input(project)
    with pytest.raises(ValueError, match="Acceptance requires"):
        import_asset_review(project, project / "review.json", True)
    assert not candidate_validation("latex", r"\begin{aligned}V_0&=e^{i\phi Z}\\V_d&=WV_{d-1}W\end{aligned}")


def test_references_are_checked_with_multiplicity_per_block(project: Path) -> None:
    assert not validate_asset_references(project, "{{asset:a1}} then {{asset:a2}}", "{{asset:a2}} 与 {{asset:a1}}")
    assert validate_asset_references(project, "{{asset:a1}} twice {{asset:a1}}", "{{asset:a1}}")[0]["code"] == "asset-reference-mismatch"
    assert any(error["code"] == "unknown-asset-reference" for error in validate_asset_references(project, "{{asset:a1}}", "{{asset:unknown}}"))
    assert any(error["code"] == "candidate-in-translation" for error in validate_asset_references(project, "{{asset:a1}}", "{{asset:a1}} $x=1$"))
    assert any(error["code"] == "malformed-asset-reference" for error in validate_asset_references(project, "x", "{{asset:../bad}}"))


@pytest.mark.parametrize("latex", [r"\href{https://example.com}{x}", r"\input{secret}", r"\require{html}", r"\def\a{x}"])
def test_unsafe_latex_fails_closed(latex: str) -> None:
    assert candidate_validation("latex", latex)


def test_modified_render_invalidates_verified_display(project: Path) -> None:
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    payload = review_input(project)
    import_asset_review(project, project / "review.json", True)
    packet = read_json(project / "evidence/representations/packets" / f"{payload['packet_id']}.json")
    (project / packet["render_artifact"]["path"]).write_text("changed", encoding="utf-8")
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"
    assert "data-candidate-latex" not in resolve_asset_html(project, "{{asset:a1}}", project)


def test_semantic_uncertainty_blocks_translation_but_untranscribed_does_not(project: Path) -> None:
    payload = candidate_input(project)
    payload["candidates"][0]["semantic_uncertainty"] = "Unclear inequality direction; affects the sentence."
    write_json(project / "candidate.json", payload)
    submit_candidates(project, project / "candidate.json")
    assert any(error["code"] == "asset-semantic-uncertainty" for error in validate_asset_references(project, "{{asset:a1}}", "{{asset:a1}}"))
    assert not validate_asset_references(project, "{{asset:a2}}", "{{asset:a2}}")


def test_cursor_requires_configured_model_without_imposing_codex_profile(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.storage import load_project

    monkeypatch.setenv("CURSOR_AGENT", "1")
    with pytest.raises(ValueError, match="Configure agent_models.cursor"):
        build_asset_packet(project, ["a1"])
    config = load_project(project)
    config.agent_models["cursor"] = {"transcribe": "host-configured-model", "reasoning_effort": "high"}
    save_project(project, config)
    packet = build_asset_packet(project, ["a1"])
    assert packet["host"] == "cursor"
    assert packet["model"] == "host-configured-model"
    payload = {"packet_id": packet["packet_id"], "author_task_id": "cursor-1", "model": "host-configured-model",
               "reasoning_effort": "high",
               "image_evidence": packet["required_images"], "candidates": [{"asset_id": "a1", "format": "latex", "content": "x=1"}]}
    write_json(project / "candidate.json", payload)
    assert submit_candidates(project, project / "candidate.json")["candidate_count"] == 1


def test_image_language_cannot_be_satisfied_by_preserving_only_the_marker(project: Path) -> None:
    from littrans.fidelity_models import load_assets

    assets = load_assets(project)
    assets["a1"].kind = "mixed-region"
    write_jsonl(project / "derived/fidelity-assets.jsonl", assets.values())
    errors = validate_asset_translations(project, "{{asset:a1}}", [], "{{asset:a1}}")
    assert errors[0]["code"] == "asset-language-unaccounted"
    supplement = AssetTranslation(asset_id="a1", target_text="这是原图中正文的完整译文。")
    assert not validate_asset_translations(project, "{{asset:a1}}", [supplement])
    with pytest.raises(ValueError):
        AssetTranslation(asset_id="a1", language_present=False)
    no_language = AssetTranslation(asset_id="a1", language_present=False, notes="Only mathematical symbols; technical auditor must confirm.")
    assert not validate_asset_translations(project, "{{asset:a1}}", [no_language])


def test_image_table_and_label_translations_are_rendered_beside_original() -> None:
    from littrans.models import FigureLabel, TableData

    record = TranslationRecord(unit_id="u1", target_text="{{asset:a1}}", source_hash="source-one",
        asset_translations=[AssetTranslation(asset_id="a1", target_table=TableData(rows=[["输入", "输出"]],
                             column_count=2, header_rows=1), figure_labels=[FigureLabel(source="Input", target="输入")])])
    markdown, rendered = _asset_companions(record)
    assert "输入" in markdown and "Input" in rendered
    assert "<table" in rendered and 'data-asset-id="a1"' in rendered


def test_explicit_revision_preserves_cache_and_invalidates_old_review(project: Path) -> None:
    baseline = candidate_input(project, "x=2")
    first = submit_candidates(project, project / "candidate.json")
    old_review = review_input(project, "reject")
    old_review["decisions"][0]["notes"] = "Original shows 1; candidate says 2."
    write_json(project / "review.json", old_review)
    import_asset_review(project, project / "review.json", True)
    packet = build_asset_packet(project, ["a1"], revision_notes="Correct the constant against the original.")
    assert packet["packet_id"] != baseline["packet_id"]
    assert packet["revision_context"]["a1"]["candidate_sha256"] == first["candidate_sha256"]["a1"]
    assert packet["revision_context"]["a1"]["review_sha256"]
    revised = {**baseline, "packet_id": packet["packet_id"], "author_task_id": "revision-worker",
               "candidates": [{"asset_id": "a1", "format": "latex", "content": "x=1"}]}
    write_json(project / "revision.json", revised)
    submitted = submit_candidates(project, project / "revision.json")
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"
    with pytest.raises(ValueError, match="stale candidate"):
        import_asset_review(project, project / "review.json", True)
    assert submit_candidates(project, project / "candidate.json")["replayed"]
    assert representation_status(project)["assets"]["a1"]["candidate_sha256"] == submitted["candidate_sha256"]["a1"]
    assert submit_candidates(project, project / "revision.json")["replayed"]
    index_path = project / "evidence/representations/index.json"
    idx = read_json(index_path)
    idx["candidates"]["a1"] = first["candidate_sha256"]["a1"]
    write_json(index_path, idx)
    assert submit_candidates(project, project / "revision.json")["replayed"]
    assert representation_status(project)["assets"]["a1"]["candidate_sha256"] == submitted["candidate_sha256"]["a1"]


def test_revision_packet_rejects_changed_feedback_and_empty_reason(project: Path) -> None:
    baseline = candidate_input(project, "x=2")
    submit_candidates(project, project / "candidate.json")
    with pytest.raises(ValueError, match="Nonempty"):
        build_asset_packet(project, ["a1"], revision_notes=" ")
    packet = build_asset_packet(project, ["a1"], revision_notes="Recheck x against the image.")
    review_input(project, "reject")
    import_asset_review(project, project / "review.json", True)
    revised = {**baseline, "packet_id": packet["packet_id"]}
    write_json(project / "revision.json", revised)
    with pytest.raises(ValueError, match="Revision packet is stale"):
        submit_candidates(project, project / "revision.json")


@pytest.mark.parametrize("location", ["text", "table", "label"])
def test_qa_forbidden_terms_include_image_translation_companions(project: Path, monkeypatch: pytest.MonkeyPatch, location: str) -> None:
    from littrans import quality
    from littrans.models import BatchManifest, FigureLabel, TableData
    from littrans.storage import write_yaml

    manifest = BatchManifest(batch_id="test-b001", project_id="asset-test", pages=[1], unit_ids=["u1"],
                             translatable_unit_ids=["u1"], source_words=3)
    write_yaml(project / "batches/test-b001/manifest.yaml", manifest.model_dump(mode="json"))
    write_yaml(project / "glossary/approved.yaml", {"terms": [{"source": "unrelated", "target": "准确用词", "forbidden": ["错误用词"]}]})
    supplement = AssetTranslation(asset_id="a1", target_text="完整译文")
    if location == "text":
        supplement.target_text = "错误用词"
    elif location == "table":
        supplement.target_table = TableData(rows=[["错误用词"]], column_count=1, header_rows=0)
    else:
        supplement.figure_labels = [FigureLabel(source="wrong label", target="错误用词")]
    packet = build_asset_packet(project, ["a1", "a2"])
    record = TranslationRecord(unit_id="u1", target_text="对于 {{asset:a1}} 和 {{asset:a2}}。",
        source_hash="source-one", image_evidence=packet["required_images"], asset_translations=[supplement])
    write_jsonl(project / "translations/current.jsonl", [record])
    monkeypatch.setattr(quality, "require_verified_extraction", lambda *_args: None)
    report = quality.run_qa(project, "test-b001")
    assert any(item.code == "forbidden-term" for item in report.errors)
    assert any(item.code == "image-content-visual-audit-required" for item in report.warnings)


def test_qa_uncertainty_fingerprint_only_invalidates_dependent_batch(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans import quality
    from littrans.models import BatchManifest
    from littrans.storage import read_jsonl, write_yaml

    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    units[0].source_text = "{{asset:a1}}"
    units.append(units[0].model_copy(update={"unit_id": "u2", "page": 10, "source_text": "{{asset:a2}}", "source_hash": "source-two"}))
    write_jsonl(project / "derived/units.jsonl", units)
    for number, unit in enumerate(units, 1):
        manifest = BatchManifest(batch_id=f"test-b{number:03d}", project_id="asset-test", pages=[unit.page], unit_ids=[unit.unit_id],
                                 translatable_unit_ids=[unit.unit_id], source_words=1)
        write_yaml(project / f"batches/{manifest.batch_id}/manifest.yaml", manifest.model_dump(mode="json"))
    # Pin an explicit disjoint semantic closure to test the QA cache scope contract.
    monkeypatch.setattr(quality, "dependency_closure", lambda _root, _batches, changed, **_kwargs: list(changed))
    before = [quality.current_qa_context_fingerprint(project, f"test-b{i:03d}") for i in (1, 2)]
    payload = candidate_input(project)
    payload["candidates"][0]["semantic_uncertainty"] = "Unclear inequality direction"
    write_json(project / "candidate.json", payload)
    submit_candidates(project, project / "candidate.json")
    after = [quality.current_qa_context_fingerprint(project, f"test-b{i:03d}") for i in (1, 2)]
    assert before[0] != after[0]
    assert before[1] == after[1]
    write_jsonl(project / "translations/current.jsonl", [TranslationRecord(
        unit_id="u2", source_hash="source-two", target_text="条件成立。",
        uncertainties=["Conjugation scope remains unclear."])])
    changed = [quality.current_qa_context_fingerprint(project, f"test-b{i:03d}") for i in (1, 2)]
    assert changed[0] == after[0]
    assert changed[1] != after[1]


@pytest.mark.parametrize(("defect", "wrong_latex"), [
    ("overline omitted", "z"),
    ("nested subscript flattened", "a_{ij}"),
    ("two recurrences joined into one equality chain", "V_0=IV_d=WV_{d-1}"),
])
def test_known_mathematical_defects_require_independent_rejection_gate_oracle(
    project: Path, defect: str, wrong_latex: str,
) -> None:
    """Gate oracle only: the test supplies rejection, not recognition-accuracy evidence."""
    # Retain a known original with the feature the valid-but-wrong LaTeX loses.
    with fitz.open() as document:
        page = document.new_page(width=140, height=65)
        if defect == "overline omitted":
            page.insert_text((20, 35), "z", fontsize=16)
            page.draw_line((19, 19), (29, 19), width=1)
        elif defect == "nested subscript flattened":
            page.insert_text((20, 30), "a", fontsize=16)
            page.insert_text((29, 34), "i", fontsize=10)
            page.insert_text((32, 38), "j", fontsize=7)
        else:
            page.insert_text((12, 24), "V_0 = I", fontsize=12)
            page.insert_text((12, 45), "V_d = W V_{d-1}", fontsize=12)
        document.save(project / "gate-original.pdf")
        page.get_pixmap(dpi=300).save(project / "gate-original.png")
        (project / "gate-original.svg").write_text(page.get_svg_image(), encoding="utf-8")
    files = {name: hashlib.sha256((project / name).read_bytes()).hexdigest()
             for name in ("gate-original.pdf", "gate-original.png", "gate-original.svg")}
    config = load_project(project)
    config.source_path = "gate-original.pdf"
    config.source_sha256 = files["gate-original.pdf"]
    save_project(project, config)
    assets = read_jsonl(project / "derived/fidelity-assets.jsonl", FidelityAsset)
    for asset in assets:
        asset.source_sha256 = config.source_sha256
        asset.content_sha256 = hashlib.sha256((asset.id + config.source_sha256).encode()).hexdigest()
        asset.fragments = [FidelityFragment(page=1, bbox=(0, 0, 140, 65), width=140, height=65,
            png_path="gate-original.png", svg_path="gate-original.svg", pdf_path="gate-original.pdf", file_sha256=files)]
    write_jsonl(project / "derived/fidelity-assets.jsonl", assets)
    assert candidate_validation("latex", wrong_latex) == []
    candidate_input(project, wrong_latex)
    submit_candidates(project, project / "candidate.json")
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"
    assert "data-candidate-latex" not in resolve_asset_html(project, "{{asset:a1}}", project)
    rejection = review_input(project, "reject")
    rejection["decisions"][0]["notes"] = "Independent gate oracle rejects: " + defect
    write_json(project / "review.json", rejection)
    import_asset_review(project, project / "review.json", True)
    assert representation_status(project)["assets"]["a1"]["state"] != "verified"
    rendered = resolve_asset_html(project, "{{asset:a1}}", project)
    assert "asset-original" in rendered and "转写未完成" in rendered
    assert "data-candidate-latex" not in rendered
    assert "![原式 a1]" in resolve_asset_markdown(project, "{{asset:a1}}", project)
    assert all(hashlib.sha256((project / name).read_bytes()).hexdigest() == expected for name, expected in files.items())


def test_originals_only_ignores_verified_candidates_without_changing_state(project):
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    review_input(project)
    import_asset_review(project, project / 'review.json', True)
    rendered = resolve_asset_html(project, '{{asset:a1}}', project, originals_only=True)
    markdown = resolve_asset_markdown(project, '{{asset:a1}}', project, originals_only=True)
    assert 'asset-original' in rendered
    assert 'asset-candidate' not in rendered
    assert 'data-candidate-latex' not in rendered
    assert 'original-image-link' in rendered and 'href=' in rendered
    assert 'asset-state' not in rendered and 'asset-original-links' not in rendered
    assert '$' not in markdown and '![' in markdown
    assert representation_status(project)['assets']['a1']['state'] == 'verified'


def test_unknown_asset_reference_is_reported_in_both_renderings(project: Path) -> None:
    text = "For {{asset:a-p0016-7d5e4044c658}}."
    for resolve in (resolve_asset_html, resolve_asset_markdown):
        with pytest.raises(ValueError) as raised:
            resolve(project, text, project)
        assert "a-p0016-7d5e4044c658" in str(raised.value)


def test_inline_mixed_region_stays_inline_in_reading_html(project: Path) -> None:
    """A raw-region fallback for an inline formula must not break the sentence into blocks."""
    from littrans.fidelity_models import load_assets
    from littrans.storage import write_jsonl

    assets = load_assets(project)
    inline = assets["a1"].model_copy(update={"kind": "mixed-region", "display": False})
    block = assets["a2"].model_copy(update={"kind": "mixed-region", "display": True})
    write_jsonl(project / "derived/fidelity-assets.jsonl", [inline, block])
    rendered = resolve_asset_html(project, "For {{asset:a1}} and {{asset:a2}}.", project)
    assert 'data-asset-id="a1" data-display="false"' in rendered
    assert 'data-asset-id="a2" data-display="true"' in rendered
