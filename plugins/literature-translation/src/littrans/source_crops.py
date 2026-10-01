"""Reviewed, exact page crops without changing original glyph ownership."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pymupdf as fitz

from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.source_structure import inked_glyph
from littrans.storage import atomic_write_text, sha256_file, sha256_text


def bind_crops(crops: Any, assets: dict[str, FidelityAsset], page: int) -> list[dict[str, Any]]:
    if not isinstance(crops, list) or not crops:
        raise ValueError("asset_crops must be a nonempty list")
    bound = []
    seen = set()
    for item in crops:
        if not isinstance(item, dict):
            raise ValueError("asset_crops entries must be objects")
        aid, index = item.get("asset_id"), item.get("fragment_index")
        if not isinstance(aid, str) or aid not in assets:
            raise ValueError(f"asset_crops target no longer exists: {aid}; create a fresh packet")
        asset = assets[aid]
        if type(index) is not int or not 0 <= index < len(asset.fragments):
            raise ValueError(f"invalid fragment_index for {aid}")
        fragment = asset.fragments[index]
        if fragment.page != page or any(f.page != page for f in asset.fragments):
            raise ValueError("asset_crops must remain on the reviewed page")
        if (aid, index) in seen:
            raise ValueError("duplicate asset_crops fragment")
        seen.add((aid, index))
        reason, box = item.get("reason"), item.get("bbox")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("asset_crops requires a visual correction reason")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("asset_crops bbox must contain four finite coordinates with positive area")
        binding = {"source_sha256": asset.source_sha256, "glyph_ids": fragment.glyph_ids,
                   "fragment_count": len(asset.fragments)}
        if any(key in item and item[key] != value for key, value in binding.items()):
            raise ValueError(f"asset_crops target changed: {aid}; create a fresh packet")
        bound.append({**item, **binding})
    return bound


def apply_crops(root: Path, doc: fitz.Document, page: int, source_hash: str,
                assets: dict[str, FidelityAsset], crops: Any,
                glyphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from littrans.glyph_export import glyph_ink_boxes

    bound = bind_crops(crops, assets, page)
    canvas = doc[page - 1].rect
    for item in bound:
        if item["source_sha256"] != source_hash:
            raise ValueError("asset_crops source PDF changed")
        if not canvas.contains(fitz.Rect(item["bbox"])):
            raise ValueError("asset_crops bbox must stay within the effective page canvas")
    try:
        ink = glyph_ink_boxes(doc[page - 1], glyphs)
    except (ValueError, RuntimeError, SyntaxError):
        ink = {}
    diagnostics = []
    for item in bound:
        asset = assets[item["asset_id"]]
        index = item["fragment_index"]
        old = asset.fragments[index]
        rect = fitz.Rect(item["bbox"])
        identity = {**item, "page": page, "export": "reviewed-page-crop-v1"}
        import json
        digest = sha256_text(json.dumps(identity, sort_keys=True, ensure_ascii=False))
        base = root / "derived/assets/fidelity" / digest
        base.mkdir(parents=True, exist_ok=True)
        png, svg = base / "original.png", base / "original.svg"
        with fitz.open() as clipped:
            target = clipped.new_page(width=rect.width, height=rect.height)
            target.show_pdf_page(target.rect, doc, page - 1, clip=rect)
            svg_text = target.get_svg_image(text_as_path=True)
            png_bytes = target.get_pixmap(dpi=old.dpi, alpha=False).tobytes("png")
        # A repeated declaration must reproduce immutable evidence, never overwrite it.
        from littrans.storage import atomic_write_bytes
        for path, data in ((png, png_bytes), (svg, svg_text.encode("utf-8"))):
            if path.exists() and path.read_bytes() != data:
                raise ValueError(f"immutable reviewed crop differs: {path}")
            if not path.exists():
                atomic_write_bytes(path, data)
        paths = [p.relative_to(root).as_posix() for p in (png, svg)]
        fragment = FidelityFragment(
            page=page, bbox=tuple(item["bbox"]), png_path=paths[0], svg_path=paths[1],
            glyph_ids=list(old.glyph_ids), width=rect.width, height=rect.height,
            baseline=None if old.baseline is None else old.baseline + old.bbox[1] - rect.y0,
            dpi=old.dpi, file_sha256={p: sha256_file(root / p) for p in paths},
        )
        asset.fragments[index] = fragment
        prefix = f"reviewed-page-crop:{index}:"
        marker = prefix + digest
        asset.provenance = [p for p in asset.provenance if not p.startswith(prefix)]
        if marker not in asset.provenance:
            asset.provenance.append(marker)
        visible = [g["id"] for g in glyphs if g["id"] in old.glyph_ids and inked_glyph(g)]
        diagnostics.append({"asset_id": asset.id, "fragment_index": index,
                            "unmeasured_glyphs": [gid for gid in visible if gid not in ink],
                            "ink_outside_crop": [gid for gid in visible if gid in ink
                                                 and not rect.contains(fitz.Rect(ink[gid]))],
                            "reason": item["reason"]})
    return diagnostics


def preview_review(root: Path, input_file: Path, output: Path) -> dict[str, Any]:
    """Prepare corrections in a separate scratch tree; never publish source authority."""
    import html
    import shutil

    from littrans.fidelity import (
        _cached_layout,
        _complete_override,
        _current_page,
        _load_source_packet,
        _page_prepare,
    )
    from littrans.fidelity_models import load_assets
    from littrans.storage import load_project, read_json, staging_directory, write_json
    from littrans.structure_profile import guidance_difference, structure_context

    root, output = root.resolve(), output.resolve()
    # A preview can be inside output/, but never overwrite authority or an existing run.
    if output == root or root.is_relative_to(output) or output.exists():
        raise ValueError("preview output must be a new independent directory")
    if output.is_relative_to(root) and not output.is_relative_to(root / "output"):
        raise ValueError("in-project previews must stay below output/")
    review = read_json(input_file)
    packet = _load_source_packet(root, review["packet_id"], review["packet_sha256"])
    config = load_project(root)
    if sha256_file(config.source(root)) != packet["source_sha256"]:
        raise ValueError("source PDF changed since packet creation")
    pages = {p["page"]: p for p in packet["pages"]}
    decisions = review["pages"]
    if len({d["page"] for d in decisions}) != len(decisions):
        raise ValueError("duplicate page review")
    guidance = structure_context(root)
    prepared = []
    for decision in decisions:
        p = decision["page"]
        if (p not in pages or decision["fingerprint"] != pages[p]["fingerprint"]
                or _current_page(root, p)["fingerprint"] != decision["fingerprint"]):
            raise ValueError("stale or out-of-packet preview")
        if guidance_difference(packet.get("document_structure"), guidance, p):
            raise ValueError("source guidance changed; create a fresh packet")
        if decision.get("override"):
            prepared.append((p, _complete_override(root, p, decision["override"])))
    if not prepared:
        raise ValueError("preview requires at least one correction")
    output.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(output.parent, prefix=".preview-") as stage:
        for name in ("project.yaml", "settings.yaml", "settings.local.yaml", "context", "glossary"):
            source = root / name
            if source.is_dir():
                shutil.copytree(source, stage / name)
            elif source.is_file():
                shutil.copyfile(source, stage / name)
        registry = load_assets(root)
        import yaml
        local_path = stage / "settings.local.yaml"
        local = yaml.safe_load(local_path.read_text(encoding="utf-8")) if local_path.exists() else {"schema_version": 1}
        local["source_path"] = str(config.source(root).resolve())
        atomic_write_text(local_path, yaml.safe_dump(local, allow_unicode=True))
        selected = {p for p, _ in prepared}
        paths = {"derived/fidelity-assets.jsonl", "derived/project-state.json"}
        for asset in registry.values():
            if any(f.page in selected for f in asset.fragments):
                paths.update(path for f in asset.fragments for path in f.file_sha256)
        for p in selected:
            paths.update({f"derived/fidelity-pages/p{p:04d}.json", f"evidence/pages/fidelity-p{p:04d}.png"})
        for relative in paths:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        reports = []
        sections = []
        for p, override in prepared:
            layout = _cached_layout(root, pages[p]["ledger"])
            if layout["status"] != pages[p]["ledger"]["layout_status"]:
                raise ValueError("recorded layout missing; restore it before preview")
            with fitz.open(config.source(root)) as doc:
                units, assets, ledger = _page_prepare(stage, doc, p, config.source_sha256, layout, override)
            reports.append({"page": p, "units": [u.model_dump(mode="json") for u in units],
                            "assets": [a.model_dump(mode="json") for a in assets], "ledger": ledger})
            boxes = "".join(f'<rect x="{f.bbox[0]}" y="{f.bbox[1]}" width="{f.width}" height="{f.height}" '
                            'fill="none" stroke="red" stroke-width="0.5"/>' for a in assets for f in a.fragments)
            sections.append(f'<h2>Page {p}</h2><svg viewBox="0 0 {ledger["width"]} {ledger["height"]}" width="700">'
                            f'<image href="{ledger["page_image"]}" width="{ledger["width"]}" height="{ledger["height"]}"/>{boxes}</svg>')
            for asset in assets:
                sections.append(f'<h3>{html.escape(asset.id)}</h3>')
                for fragment in asset.fragments:
                    sections.append(f'<img src="{fragment.png_path}" style="max-width:100%">'
                                    f'<a href="{fragment.svg_path}">SVG</a>')
        write_json(stage / "preview.json", {"packet_id": review["packet_id"], "pages": reports, "approved": False})
        atomic_write_text(stage / "index.html", '<!doctype html><meta charset="utf-8"><title>Source correction preview</title>'
                          '<h1>Preview only — new independent review required</h1>' + "".join(sections))
        for p in selected:
            if _current_page(root, p)["fingerprint"] != pages[p]["fingerprint"]:
                raise ValueError("source changed during preview; create a fresh packet")
        shutil.copytree(stage, output)
    return {"preview": str(output / "index.html"), "report": str(output / "preview.json"),
            "pages": sorted(selected), "approved_pages": []}
