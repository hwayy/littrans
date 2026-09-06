"""A single evidence-first PDF preparation path, with explicit visual-review gates."""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import fitz

from littrans.extractor import parse_page_spec, protected_tokens
from littrans.fidelity_models import (
    FidelityAsset,
    FidelityFragment,
    asset_reference_ids,
    load_assets,
)
from littrans.layout_detector import detect_layout
from littrans.models import AssetRef, SemanticStatus, SourceUnit, UnitKind
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    restore_files,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_json,
    write_jsonl,
)

MATH_FONT = re.compile(r"cmmi|cmsy|cmex|msam|msbm|math|symbol|stix|cm[a-z]*sy", re.I)
MATH_CHAR = re.compile(r"[\u0370-\u03ff\u2100-\u214f\u2190-\u22ff\u27c0-\u27ef=<>^_|]")
KINDS = {"math", "figure", "table", "code", "mixed-region"}


def _hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _path(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes project")
    return result


def _page_path(root: Path, page: int) -> Path:
    return root / f"derived/fidelity-pages/p{page:04d}.json"


@contextmanager
def _authority_transaction(root: Path, pages: list[int]) -> Iterator[None]:
    paths = [root / "derived/units.jsonl", root / "derived/fidelity-assets.jsonl"]
    paths += [_page_path(root, p) for p in pages]
    paths += [root / f"evidence/pages/fidelity-p{p:04d}.review.json" for p in pages]
    paths += [root / "evidence/audits" / f"{p.parent.name}.invalidations.json" for p in (root / "batches").glob("*/manifest.yaml")]
    snapshots = snapshot_files(paths)
    try:
        yield
    except Exception:
        restore_files(snapshots)
        raise


def _box(values: Any) -> tuple[float, float, float, float]:
    return tuple(round(float(v), 4) for v in values)  # type: ignore[return-value]


def _union(boxes: list[Any]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _inside(glyph: dict[str, Any], bbox: Any) -> bool:
    b = glyph["bbox"]
    return bool(bbox[0] <= (b[0] + b[2]) / 2 <= bbox[2] and bbox[1] <= (b[1] + b[3]) / 2 <= bbox[3])


def _intersects(a: Any, b: Any) -> bool:
    return bool(max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]))


def _native(page: fitz.Page) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    glyphs, blocks = [], []
    for bi, block in enumerate(page.get_text("rawdict")["blocks"]):
        if block["type"] != 0:
            continue
        lines = []
        for li, line in enumerate(block["lines"]):
            ids = []
            for si, span in enumerate(line["spans"]):
                for ci, char in enumerate(span["chars"]):
                    gid = f"b{bi}-l{li}-s{si}-c{ci}"
                    glyphs.append({"id": gid, "text": char["c"], "bbox": _box(char["bbox"]), "font": span["font"], "size": span["size"], "origin": list(char["origin"]), "baseline": char["origin"][1], "line": f"b{bi}-l{li}"})
                    ids.append(gid)
            lines.append(ids)
        blocks.append({"id": f"b{bi}", "bbox": _box(block["bbox"]), "lines": lines})
    return glyphs, blocks


def _regions(page: fitz.Page, glyphs: list[dict[str, Any]], layout: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] != 0:
            continue
        bad = [g for g in glyphs if _inside(g, block["bbox"]) and ("\ufffd" in g["text"] or any(ord(c) < 32 and c not in "\t\r\n" for c in g["text"]) and not MATH_FONT.search(g["font"]))]
        if bad:
            regions.append({"kind": "mixed-region", "bbox": list(block["bbox"]), "provenance": ["invalid-native-text:recovery-required"], "display": True, "grouping_pending": True})
    for item in layout:
        label = item.get("label", "")
        kind = "math" if "formula" in label and "number" not in label else "figure" if label in {"image", "figure", "chart", "header_image", "footer_image"} else label if label in {"table", "code"} else None
        if kind:
            regions.append({"kind": kind, "bbox": [float(v) / 2 for v in item["bbox"]], "provenance": [f"PP-DocLayoutV2:{label}"], "display": label != "inline_formula", "grouping_pending": False})
    # Native font and character candidates retain small isolated variables.
    lines: dict[str, list[dict[str, Any]]] = {}
    # Some LaTeX builds use T1 SF fonts for prose and OT1 CMR fonts only in
    # mathematical mode; normal-font operators otherwise escape symbol rules.
    separate_roman_math = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * 0.2
    for glyph in glyphs:
        lines.setdefault(glyph["line"], []).append(glyph)
    for line in lines.values():
        run: list[dict[str, Any]] = []
        for position, glyph in enumerate([*line, {"text": "\u0000", "font": "", "bbox": [0, 0, 0, 0]}]):
            mathematical = bool(MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]) or (separate_roman_math and re.match(r"CMR\d", glyph["font"], re.I)))
            if mathematical or (run and glyph["text"] in "0123456789()[]{}+-*/., "):
                if mathematical and not run:
                    # Normal-font prefixes are common in U(N), diag(...), 2π.
                    prefix: list[dict[str, Any]] = []
                    for previous in reversed(line[:position]):
                        if previous["text"].isspace() or not re.fullmatch(r"[A-Za-z0-9([{}.*+/-]", previous["text"]):
                            break
                        prefix.insert(0, previous)
                    prefix_text = "".join(g["text"] for g in prefix)
                    if re.fullmatch(r"(?:[A-Za-z]|diag|rank|Tr|tr|sin|cos|tan|log|exp|lim|sup|inf|max|min|det|dim|ker|span)?[0-9([{}.*+/-]*", prefix_text):
                        run.extend(prefix)
                run.append(glyph)
            elif run:
                while run and run[-1]["text"] in " .,":
                    run.pop()
                if run:
                    regions.append({"kind": "math", "bbox": _union([g["bbox"] for g in run]), "provenance": ["native-math-glyphs"], "display": False, "grouping_pending": False})
                run = []
    for image in page.get_image_info():
        regions.append({"kind": "figure", "bbox": list(image["bbox"]), "provenance": ["native-image"], "display": True, "grouping_pending": False})
    # Vector-only diagrams, fraction bars and accents must not disappear from the ledger.
    for drawing in page.get_drawings():
        bbox = list(drawing["rect"])
        bbox = [bbox[0] - 0.5, bbox[1] - 0.5, bbox[2] + 0.5, bbox[3] + 0.5]
        regions.append({"kind": "mixed-region", "bbox": bbox, "provenance": ["native-vector"], "display": True, "grouping_pending": True})
    # Merge overlaps, including glyph extents, so no key glyph is cut into two assets.
    changed = True
    native_blocks = [b for b in page.get_text("rawdict")["blocks"] if b["type"] == 0]
    operators = {"sin", "cos", "tan", "log", "exp", "lim", "sup", "inf", "max", "min", "det", "rank", "diag", "span", "arg", "dim", "ker", "poly"}
    prose_words: list[list[dict[str, Any]]] = []
    for line in lines.values():
        word: list[dict[str, Any]] = []
        for glyph in [*line, {"text": " ", "font": ""}]:
            prose_font = not MATH_FONT.search(glyph["font"]) and not (separate_roman_math and re.match(r"CMR\d", glyph["font"], re.I))
            if prose_font and glyph["text"].isalpha():
                word.append(glyph)
            else:
                if len(word) >= 2:
                    prose_words.append(word)
                word = []
    while changed:
        changed = False
        for r in regions:
            owned_glyphs = [g for g in glyphs if _inside(g, r["bbox"])]
            owned = [g["bbox"] for g in owned_glyphs]
            if owned:
                r["bbox"] = _union([r["bbox"], *owned])
            normal_text = "".join(g["text"] if not MATH_FONT.search(g["font"]) else " " for g in owned_glyphs)
            words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", normal_text) if w.lower() not in operators]
            # Even one cut character from a prose word is a preservation failure.
            # Expanding merely to that glyph can leave 'n{{asset}}orm' in source.
            touched_words = [word for word in prose_words if any(_intersects(g["bbox"], r["bbox"]) for g in word)]
            prose_word_intersection = bool(touched_words)
            if r["kind"] in {"math", "mixed-region"} and (prose_word_intersection or len(words) >= 2 or any(len(w) >= 6 for w in words)):
                # A parser box spanning several text lines can consume pieces of words
                # in intervening lines. Preserve complete native blocks, not a false
                # precise formula with disconnected prose scattered around it.
                touched_glyphs = owned_glyphs + [g for word in touched_words for g in word]
                blocks_to_keep = [b["bbox"] for b in native_blocks if any(_inside(g, b["bbox"]) for g in touched_glyphs)]
                r["bbox"] = _union([r["bbox"], *blocks_to_keep])
                r["kind"] = "mixed-region"
                r["grouping_pending"] = True
                r["display"] = True
                r["provenance"] = sorted(set([*r["provenance"], "complete-prose-block-fallback"]))
        for i, left in enumerate(regions):
            for j in range(i + 1, len(regions)):
                right = regions[j]
                if _intersects(left["bbox"], right["bbox"]):
                    left["bbox"] = _union([left["bbox"], right["bbox"]])
                    if left["kind"] != right["kind"]:
                        if "table" in {left["kind"], right["kind"]}:
                            left["kind"] = "table"
                        elif "figure" in {left["kind"], right["kind"]}:
                            left["kind"] = "figure"
                        elif "math" in {left["kind"], right["kind"]} and "native-vector" in left["provenance"] + right["provenance"]:
                            left["kind"] = "math"
                        else:
                            left["kind"] = "mixed-region"
                    left["provenance"] = sorted(set(left["provenance"] + right["provenance"]))
                    left["grouping_pending"] = left["kind"] == "mixed-region"
                    left["display"] = left["display"] or right["display"]
                    regions.pop(j)
                    changed = True
                    break
            if changed:
                break
    return sorted(regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def _asset(root: Path, doc: fitz.Document, page_number: int, source_hash: str, region: dict[str, Any], glyphs: list[dict[str, Any]]) -> FidelityAsset:
    if "preserve_asset_id" in region:
        existing = load_assets(root)[region["preserve_asset_id"]]
        if existing.source_sha256 != source_hash or any(f.page != page_number for f in existing.fragments):
            raise ValueError("preserved asset belongs to different source/page")
        changes = {k: region[k] for k in ("kind", "display", "grouping_pending") if k in region}
        return FidelityAsset.model_validate({**existing.model_dump(mode="json"), **changes})
    if "fragments" in region:
        children = []
        for fragment in region["fragments"]:
            if fragment.get("page", page_number) != page_number:
                raise ValueError("page review fragments must remain on their reviewed page; use continuation links across pages")
            child = {k: v for k, v in region.items() if k not in {"fragments", "id"}}
            child["bbox"] = fragment["bbox"]
            if "glyph_ids" in fragment:
                child["glyph_ids"] = fragment["glyph_ids"]
            children.append(_asset(root, doc, page_number, source_hash, child, glyphs))
        if not children:
            raise ValueError("region fragments must not be empty")
        content = children[0].content_sha256 if len(children) == 1 else _hash([a.content_sha256 for a in children])
        return FidelityAsset(id=region.get("id") or f"a-p{page_number:04d}-{content[:12]}", kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[f for a in children for f in a.fragments], grouping_pending=region.get("grouping_pending", False), display=region.get("display", True), provenance=region.get("provenance", ["visual-multifragment-correction"]))
    page = doc[page_number - 1]
    rect = fitz.Rect(region["bbox"]) & page.rect
    if rect.is_empty:
        raise ValueError("empty or out-of-page source region")
    if "glyph_ids" in region:
        selected = region["glyph_ids"]
        if not isinstance(selected, list) or len(selected) != len(set(selected)) or any(gid not in {g["id"] for g in glyphs} for gid in selected):
            raise ValueError("explicit glyph ownership must contain unique existing page glyph IDs")
        owned = [g for g in glyphs if g["id"] in selected]
    else:
        owned = [g for g in glyphs if _inside(g, rect)]
    rect = (fitz.Rect(_union([list(rect), *[g["bbox"] for g in owned]])) + (-0.5, -0.5, 0.5, 0.5)) & page.rect
    export_method: Literal["raw-region", "explicit-glyph-paths-v2"] = "explicit-glyph-paths-v2" if "glyph_ids" in region else "raw-region"
    owned_svg = None
    if export_method != "raw-region":
        from littrans.glyph_export import build_owned_fragment
        owned_svg, geometry = build_owned_fragment(page, owned, list(rect))
        rect = fitz.Rect(geometry["bbox"])
    identity = {"source_sha256": source_hash, "page": page_number, "bbox": _box(rect), "glyph_ids": [g["id"] for g in owned]}
    if export_method != "raw-region":
        identity["export_method"] = export_method
    content = _hash(identity)
    aid = region.get("id") or f"a-p{page_number:04d}-{content[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", aid):
        raise ValueError("invalid region asset ID")
    base = root / f"derived/assets/fidelity/{content}"
    base.mkdir(parents=True, exist_ok=True)
    fragment_pdf = base / "original.pdf"
    fragment_svg = base / "original.svg"
    fragment_png = base / "original.png"
    cache_receipt = base / "evidence.json"
    dpi = 450 if any(g["size"] < 8 for g in owned) else 300
    if cache_receipt.is_file():
        recorded = read_json(cache_receipt)
        for name, expected in recorded["files"].items():
            candidate = base / name
            if not candidate.is_file() or sha256_file(candidate) != expected:
                raise ValueError(f"immutable original asset cache is corrupt: {candidate}")
    if not all(p.is_file() for p in (fragment_pdf, fragment_svg, fragment_png)):
        with fitz.open() as clipped:
            target = clipped.new_page(width=rect.width, height=rect.height)
            target.show_pdf_page(target.rect, doc, page_number - 1, clip=rect)
            clipped.save(fragment_pdf)
            atomic_write_text(fragment_svg, target.get_svg_image(text_as_path=True))
            target.get_pixmap(dpi=dpi, alpha=False).save(fragment_png)
    if owned_svg is not None and not cache_receipt.is_file():
        atomic_write_text(fragment_svg, owned_svg)
        with fitz.open("svg", owned_svg.encode()) as rendered:
            rendered[0].get_pixmap(dpi=dpi, alpha=False).save(fragment_png)
    if not cache_receipt.is_file():
        write_json(cache_receipt, {"identity": identity, "files": {p.name: sha256_file(p) for p in (fragment_pdf, fragment_svg, fragment_png)}})
    paths = {name: str(p.relative_to(root)).replace("\\", "/") for name, p in {"png": fragment_png, "svg": fragment_svg, "pdf": fragment_pdf}.items()}
    fragment = FidelityFragment(page=page_number, bbox=_box(rect), png_path=paths["png"], svg_path=paths["svg"], pdf_path=paths["pdf"], glyph_ids=[g["id"] for g in owned], width=rect.width, height=rect.height, baseline=(max(g["baseline"] for g in owned) - rect.y0) if owned else None, dpi=dpi, export_method=export_method, file_sha256={paths[k]: sha256_file(root / paths[k]) for k in paths})
    return FidelityAsset(id=aid, kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[fragment], grouping_pending=region.get("grouping_pending", False), display=region.get("display", False), provenance=region.get("provenance", ["visual-region-correction"]))


def _make_unit(page: int, uid: str, text: str, bbox: Any, assets: dict[str, FidelityAsset], kind: str = "paragraph", **extra: Any) -> SourceUnit:
    refs = asset_reference_ids(text)
    hashes = {aid: assets[aid].content_sha256 for aid in refs}
    extra.setdefault("protected_tokens", protected_tokens(re.sub(r"\{\{asset:[^}]+\}\}", "", text)))
    payload = {"page": page, "text": text, "bbox": _box(bbox), "asset_content_hashes": hashes, "kind": kind, **extra}
    pure_math = bool(refs) and not re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip() and all(assets[aid].kind == "math" for aid in refs)
    if pure_math:
        kind = "equation"
        payload["kind"] = kind
    return SourceUnit(unit_id=uid, page=page, kind=UnitKind(kind), bbox=_box(bbox), source_text=text, source_markdown=text, source_hash=_hash(payload), confidence=0, latex=None, translatable=not pure_math, asset_content_hashes=hashes, asset_refs=[AssetRef(kind="fidelity", path=f.png_path, bbox=f.bbox) for aid in dict.fromkeys(refs) for f in assets[aid].fragments], **extra)


def _page_prepare(root: Path, doc: fitz.Document, number: int, source_hash: str, layout: dict[str, Any], override: dict[str, Any] | None = None) -> tuple[list[SourceUnit], list[FidelityAsset], dict[str, Any]]:
    page = doc[number - 1]
    glyphs, blocks = _native(page)
    image_path = root / f"evidence/pages/fidelity-p{number:04d}.png"
    regions = override["regions"] if override and "regions" in override else _regions(page, glyphs, layout.get("pages", {}).get(str(image_path.resolve()), []))
    if not glyphs and not regions and page.get_images():
        regions = [{"kind": "mixed-region", "bbox": list(page.rect), "grouping_pending": True, "display": True, "provenance": ["no-text-layer"]}]
    assets = [_asset(root, doc, number, source_hash, r, glyphs) for r in regions]
    by_id = {a.id: a for a in assets}
    if len(by_id) != len(assets):
        raise ValueError("duplicate region asset IDs")
    owners: dict[str, str] = {}
    for asset in assets:
        for gid in [gid for fragment in asset.fragments for gid in fragment.glyph_ids]:
            if gid in owners:
                raise ValueError(f"overlapping asset glyph ownership on page {number}: {gid}")
            owners[gid] = asset.id
    edge_spaces = {}
    for asset in assets:
        owned_glyphs = [g for g in glyphs if owners.get(g["id"]) == asset.id]
        edge_spaces[asset.id] = (bool(owned_glyphs and owned_glyphs[0]["text"].isspace()), bool(owned_glyphs and owned_glyphs[-1]["text"].isspace()))
    units, emitted = [], set()
    glyph_by_id = {g["id"]: g for g in glyphs}
    for block in blocks:
        lines = []
        for line in block["lines"]:
            chars = []
            for gid in line:
                aid = owners.get(gid)
                if aid:
                    if aid not in emitted:
                        before, after = edge_spaces[aid]
                        chars.append((" " if before else "") + "{{asset:" + aid + "}}" + (" " if after else ""))
                        emitted.add(aid)
                else:
                    chars.append(glyph_by_id[gid]["text"])
            if "".join(chars).strip():
                lines.append("".join(chars).strip())
        text = " ".join(lines)
        if text:
            kind = "paragraph"
            semantic_labels = {"doc_title": "heading", "paragraph_title": "heading", "title": "heading", "figure_caption": "caption", "table_caption": "caption", "footnote": "footnote", "reference": "bibliography", "list": "list_item"}
            for item in layout.get("pages", {}).get(str(image_path.resolve()), []):
                if item.get("label") in semantic_labels and _inside({"bbox": block["bbox"]}, [v / 2 for v in item["bbox"]]):
                    kind = semantic_labels[item["label"]]
                    break
            units.append(_make_unit(number, f"p{number:04d}-{block['id']}", text, block["bbox"], by_id, kind=kind))
    for asset in assets:
        if asset.id not in emitted:
            units.append(_make_unit(number, f"p{number:04d}-visual-{asset.id}", "{{asset:" + asset.id + "}}", asset.fragments[0].bbox, by_id, kind="figure" if asset.kind == "figure" else "paragraph"))
    # Native block order preserves PDF column flow and inline continuations;
    # y-sorting interleaves columns and moves tall formula suffixes before prose.
    native_order = {f"p{number:04d}-{block['id']}": index for index, block in enumerate(blocks)}
    def order(unit: SourceUnit) -> float:
        if unit.unit_id in native_order:
            return float(native_order[unit.unit_id])
        previous = [index for index, block in enumerate(blocks) if block["bbox"][3] <= unit.bbox[1]]
        return max(previous, default=-1) + 0.5
    units.sort(key=order)
    if override and "units" in override:
        units = []
        for item in override["units"]:
            if item.get("latex") is not None:
                raise ValueError("source preservation cannot import LaTeX")
            extra = {k: item[k] for k in ("equation_number", "footnote_number", "footnote_refs", "parent_id", "continues_from_previous", "continued_to_next") if k in item}
            units.append(_make_unit(number, item["unit_id"], item["source_markdown"], item["bbox"], by_id, item.get("kind", "paragraph"), **extra))
        refs = Counter(aid for unit in units for aid in asset_reference_ids(unit.source_text))
        if refs != Counter({aid: 1 for aid in by_id}):
            raise ValueError("unit overrides must reference each page asset exactly once")
    ledger = {"schema_version": 6, "page": number, "source_sha256": source_hash, "width": page.rect.width, "height": page.rect.height, "page_image": str(image_path.relative_to(root)).replace("\\", "/"), "page_image_sha256": sha256_file(image_path), "layout_status": layout["status"], "layout_reason": layout.get("reason"), "layout_fingerprint": layout.get("fingerprint"), "glyphs": [{**g, "owner": owners.get(g["id"], "native-text")} for g in glyphs], "vector_regions": [_box(d["rect"]) for d in page.get_drawings()], "raster_regions": [_box(i["bbox"]) for i in page.get_image_info()], "asset_ids": list(by_id), "unit_ids": [u.unit_id for u in units], "grouping_pending": [a.id for a in assets if a.grouping_pending], "reading_order_review_required": True, "source_overrides": override}
    ledger["fingerprint"] = _hash({"ledger": ledger, "units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in units], "assets": [a.model_dump(mode="json") for a in assets]})
    return units, assets, ledger


def _invalidate(root: Path, old: list[SourceUnit], new: list[SourceUnit]) -> None:
    old_map = {u.unit_id: u.source_hash for u in old}
    new_map = {u.unit_id: u.source_hash for u in new}
    changed = {uid for uid in old_map.keys() | new_map.keys() if old_map.get(uid) != new_map.get(uid)}
    from littrans.evidence import record_audit_invalidation
    from littrans.storage import read_yaml

    for path in (root / "batches").glob("*/manifest.yaml"):
        manifest = read_yaml(path)
        affected = set(manifest["unit_ids"]) & changed
        if affected:
            record_audit_invalidation(root, manifest["batch_id"], affected)


def prepare_source(root: Path, page_spec: str = "all", replace: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    source = config.source(root)
    digest = sha256_file(source)
    if digest != config.source_sha256:
        raise ValueError("source PDF changed; rebuild project before preparing")
    pages = parse_page_spec(page_spec, config.source_pages)
    with project_write_lock(root), _authority_transaction(root, pages), fitz.open(source) as doc:
        old = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        registry = load_assets(root)
        needed = [p for p in pages if replace or not _page_path(root, p).is_file()]
        if not needed:
            return {"pages": pages, "prepared_pages": [], "cached_pages": pages, "assets": len(registry), "requires_visual_review": True}
        images = []
        for number in needed:
            image = root / f"evidence/pages/fidelity-p{number:04d}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            doc[number - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image)
            images.append(image)
        layout = detect_layout(images, root / f"derived/fidelity-layout/{_hash([digest, needed])}.json")
        units = [u for u in old if u.page not in needed]
        registry = {aid: a for aid, a in registry.items() if not any(f.page in needed for f in a.fragments)}
        ledgers = []
        for number in needed:
            page_units, page_assets, ledger = _page_prepare(root, doc, number, digest, layout)
            units.extend(page_units)
            registry.update({a.id: a for a in page_assets})
            ledgers.append(ledger)
        _invalidate(root, old, units)
        units.sort(key=lambda u: u.page)
        write_jsonl(root / "derived/units.jsonl", units)
        write_jsonl(root / "derived/fidelity-assets.jsonl", registry.values())
        for ledger in ledgers:
            write_json(_page_path(root, ledger["page"]), ledger)
            (root / f"evidence/pages/fidelity-p{ledger['page']:04d}.review.json").unlink(missing_ok=True)
    packet = build_source_review_packet(root, ",".join(map(str, pages)))
    return {"pages": pages, "prepared_pages": needed, "cached_pages": [p for p in pages if p not in needed], "assets": len(registry), "layout_status": layout["status"], "requires_visual_review": True, "review_packet": packet["packet_path"], "visual_report": packet["visual_report"]}


def _current_page(root: Path, number: int) -> dict[str, Any]:
    ledger = read_json(_page_path(root, number))
    all_units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    if len({u.unit_id for u in all_units}) != len(all_units):
        raise ValueError("duplicate source unit IDs anywhere in project")
    unit_map = {u.unit_id: u for u in all_units}
    for unit in all_units:
        if len(unit.footnote_refs) != len(set(unit.footnote_refs)) or any(
            ref not in unit_map or unit_map[ref].kind != UnitKind.FOOTNOTE for ref in unit.footnote_refs
        ):
            raise ValueError(f"invalid or duplicated footnote relationship: {unit.unit_id}")
    units = [u for u in all_units if u.page == number]
    assets = load_assets(root)
    selected = [assets[aid] for aid in ledger["asset_ids"]]
    files = {ledger["page_image"]: sha256_file(_path(root, ledger["page_image"]))}
    if files[ledger["page_image"]] != ledger["page_image_sha256"]:
        raise ValueError("original page image changed")
    for asset in selected:
        if asset.source_sha256 != ledger["source_sha256"]:
            raise ValueError("asset PDF fingerprint does not match page")
        fragment_hashes = [_hash({"source_sha256": asset.source_sha256, "page": f.page, "bbox": f.bbox, "glyph_ids": f.glyph_ids,
                                 **({"export_method": f.export_method} if f.export_method != "raw-region" else {})}) for f in asset.fragments]
        expected_content = fragment_hashes[0] if len(fragment_hashes) == 1 else _hash(fragment_hashes)
        if asset.content_sha256 != expected_content:
            raise ValueError("asset region content fingerprint does not match original provenance")
        for fragment in asset.fragments:
            for relative, expected in fragment.file_sha256.items():
                path = _path(root, relative)
                if not path.is_file() or sha256_file(path) != expected:
                    raise ValueError(f"missing or changed original crop: {relative}")
                files[relative] = expected
    from littrans.evidence import page_evidence_units

    dependencies = [u for u in page_evidence_units(number, all_units) if u.page != number]
    payload = {"ledger": ledger, "units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in units], "dependency_units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in dependencies], "assets": [a.model_dump(mode="json") for a in selected], "files": files}
    return {"page": number, "fingerprint": _hash(payload), **payload}


def build_source_review_packet(root: Path, page_spec: str = "all") -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    if sha256_file(config.source(root)) != config.source_sha256:
        raise ValueError("source PDF changed; rebuild before reviewing")
    pages = parse_page_spec(page_spec, config.source_pages)
    from littrans.evidence import page_evidence_units

    all_units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    pages = sorted(set(pages) | {u.page for page in pages for u in page_evidence_units(page, all_units)})
    payload: dict[str, Any] = {"schema_version": 6, "kind": "source-fidelity-review", "source_sha256": sha256_file(config.source(root)), "pages": [_current_page(root, p) for p in pages]}
    packet_id = "source-" + _hash(payload)[:20]
    directory = root / "packets" / packet_id
    directory.mkdir(parents=True, exist_ok=True)
    packet = directory / "packet.json"
    write_json(packet, payload)
    review_template = {"packet_id": packet_id, "packet_sha256": sha256_file(packet), "reviewer": "", "pages": [{"page": p["page"], "fingerprint": p["fingerprint"], "viewed_original": False, "coverage_complete": False, "boundaries_complete": False, "reading_order_correct": False, "grouping_checked": False, "layout_fallback_checked": False, "issues": [], "notes": ""} for p in payload["pages"]]}
    write_json(directory / "review-template.json", review_template)
    sections = []
    for p in payload["pages"]:
        ledger = p["ledger"]
        boxes = "".join(f'<rect x="{f["bbox"][0]}" y="{f["bbox"][1]}" width="{f["width"]}" height="{f["height"]}" fill="none" stroke="red" stroke-width="0.6"><title>{html.escape(a["id"])}</title></rect>' for a in p["assets"] for f in a["fragments"])
        image_uri = _path(root, ledger["page_image"]).as_uri()
        source = "\n\n".join(u["source_text"] for u in p["units"])
        sections.append(f'<section><h2>PDF page {p["page"]}</h2><p>Layout: {html.escape(ledger["layout_status"])}; visual review required.</p><svg viewBox="0 0 {ledger["width"]} {ledger["height"]}"><image href="{image_uri}" width="{ledger["width"]}" height="{ledger["height"]}"/>{boxes}</svg><pre>{html.escape(source)}</pre></section>')
    report = directory / "coverage.html"
    atomic_write_text(report, '<!doctype html><meta charset="utf-8"><title>Source fidelity review</title><style>body{font:16px sans-serif;max-width:1500px;margin:auto}svg{width:65%;vertical-align:top}pre{white-space:pre-wrap;display:inline-block;width:33%;font:14px sans-serif}</style><h1>Original page, region boundaries and reading sequence</h1>' + "".join(sections))
    return {"packet_id": packet_id, "packet_path": str(packet), "packet_sha256": sha256_file(packet), "review_template": str(directory / "review-template.json"), "visual_report": str(report), "pages": pages}


def import_source_review(root: Path, input_file: Path, confirm_visual_review: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    if not confirm_visual_review:
        raise ValueError("source review import requires explicit confirmation of actual visual review")
    review = read_json(Path(input_file))
    packet_id = review["packet_id"]
    if not re.fullmatch(r"source-[a-f0-9]{20}", packet_id):
        raise ValueError("invalid source packet ID")
    packet_path = root / "packets" / packet_id / "packet.json"
    if sha256_file(packet_path) != review["packet_sha256"]:
        raise ValueError("source packet hash mismatch")
    if not review.get("reviewer", "").strip():
        raise ValueError("source review requires reviewer identity")
    packet = read_json(packet_path)
    config = load_project(root)
    if sha256_file(config.source(root)) != packet["source_sha256"]:
        raise ValueError("source PDF changed since packet creation")
    by_page = {p["page"]: p for p in packet["pages"]}
    decisions = review["pages"]
    if len({d["page"] for d in decisions}) != len(decisions):
        raise ValueError("duplicate page review")
    for decision in decisions:
        p = decision["page"]
        if p not in by_page or decision["fingerprint"] != by_page[p]["fingerprint"] or _current_page(root, p)["fingerprint"] != decision["fingerprint"]:
            raise ValueError(f"stale or out-of-packet page review: {p}")
    changed, approved = [], []
    with project_write_lock(root), _authority_transaction(root, [d["page"] for d in decisions]):
        for decision in decisions:
            if _current_page(root, decision["page"])["fingerprint"] != decision["fingerprint"]:
                raise ValueError("source page changed while waiting for project write lock")
        units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        assets = load_assets(root)
        for decision in decisions:
            p = decision["page"]
            if decision.get("override"):
                with fitz.open(config.source(root)) as doc:
                    layout = {"status": by_page[p]["ledger"]["layout_status"], "reason": by_page[p]["ledger"].get("layout_reason"), "pages": {}}
                    new_units, new_assets, ledger = _page_prepare(root, doc, p, config.source_sha256, layout, decision["override"])
                old_units = units
                units = [u for u in units if u.page != p] + new_units
                _invalidate(root, old_units, units)
                assets = {aid: a for aid, a in assets.items() if not any(f.page == p for f in a.fragments)}
                assets.update({a.id: a for a in new_assets})
                write_json(_page_path(root, p), ledger)
                (root / f"evidence/pages/fidelity-p{p:04d}.review.json").unlink(missing_ok=True)
                changed.append(p)
                continue
            fields = ["viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked"]
            if by_page[p]["ledger"]["layout_status"] != "ok":
                fields.append("layout_fallback_checked")
            passed = all(decision.get(key) is True for key in fields) and decision.get("issues") == []
            if passed:
                approved.append(p)
            write_json(root / f"evidence/pages/fidelity-p{p:04d}.review.json", {"fingerprint": decision["fingerprint"], "source_sha256": config.source_sha256, "passed": passed, "reviewer": review["reviewer"], "packet_sha256": review["packet_sha256"], "decision": decision})
        decision_pages = {d["page"] for d in decisions}
        for unit in units:
            if unit.page in decision_pages:
                unit.verification_status = SemanticStatus.VERIFIED if unit.page in approved else SemanticStatus.UNVERIFIED
        units.sort(key=lambda u: u.page)
        write_jsonl(root / "derived/units.jsonl", units)
        write_jsonl(root / "derived/fidelity-assets.jsonl", assets.values())
    return {"approved_pages": approved, "changed_pages": changed, "requires_new_packet": bool(changed)}


def verify_fidelity(root: Path, page_spec: str = "all") -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    pages = parse_page_spec(page_spec, config.source_pages)
    requested_pages = list(pages)
    from littrans.evidence import page_evidence_units

    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    required_pages = set(pages)
    for page in requested_pages:
        required_pages.update(unit.page for unit in page_evidence_units(page, units))
    pages = sorted(required_pages)
    errors: list[dict[str, Any]] = []
    verified = []
    source_current = sha256_file(config.source(root)) == config.source_sha256
    for p in pages:
        try:
            if not source_current:
                raise ValueError("source PDF changed")
            current = _current_page(root, p)
            receipt_path = root / f"evidence/pages/fidelity-p{p:04d}.review.json"
            receipt = read_json(receipt_path) if receipt_path.is_file() else {}
            if not receipt.get("passed") or receipt.get("fingerprint") != current["fingerprint"]:
                raise ValueError("page requires current visual coverage and boundary review")
            refs = Counter(aid for u in current["units"] for aid in asset_reference_ids(u["source_markdown"] or u["source_text"]))
            if refs != Counter({a["id"]: 1 for a in current["assets"]}):
                raise ValueError("asset references are missing or duplicated")
            verified.append(p)
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"page": p, "code": "fidelity-source-unverified", "message": str(exc)})
    return {"passed": not errors, "errors": errors, "verified_pages": verified, "requested_pages": requested_pages, "dependency_pages": sorted(required_pages - set(requested_pages)), "visual_report": None}
