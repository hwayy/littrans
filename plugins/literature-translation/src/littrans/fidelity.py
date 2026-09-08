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
MATH_OPERATORS = {"sin", "cos", "tan", "log", "ln", "exp", "lim", "sup", "inf", "max", "min", "det", "rank", "diag", "span", "arg", "dim", "ker", "poly", "tr"}
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


def _prose_boundary_ids(glyphs: list[dict[str, Any]]) -> set[str]:
    """Protect evidenced prose delimiters/citation keys, not unbalanced math.

    Work across native lines in each block. A normal-font delimiter is not
    sufficient evidence alone: mathematical intervals and continued expressions
    commonly use the same font as prose.
    """
    protected: set[str] = set()
    separate_roman_math = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * 0.2

    def math_font(glyph: dict[str, Any]) -> bool:
        return bool(MATH_FONT.search(glyph["font"]) or (separate_roman_math and re.match(r"CM(?:R|BX)\d", glyph["font"], re.I)))

    blocks: dict[str, list[dict[str, Any]]] = {}
    for glyph in glyphs:
        blocks.setdefault(glyph["line"].split("-l")[0], []).append(glyph)
    for block in blocks.values():
        text = "".join(g["text"] for g in block)
        # Native glyphs normally encode one character, but retain offset mapping
        # for ligatures/multi-character recovery values as well.
        offsets = [g for g in block for _ in g["text"]]
        for match in re.finditer(r"\[(?:[A-Za-z]{2,}\+?\d{2,4}[a-z]?)(?:[,;]\s*[A-Za-z]{2,}\+?\d{2,4}[a-z]?)*\]", text):
            candidate = offsets[match.start():match.end()]
            # A superscript citation '+' may use CMR even with SF prose.
            # Mathematical letters in a similar-looking expression veto this.
            if all(not math_font(g) for g in candidate if g["text"] != "+"):
                protected.update(g["id"] for g in candidate)
        stack: list[int] = []
        for index, glyph in enumerate(block):
            char = glyph["text"]
            if char in {"(", "[", "{"}:
                stack.append(index)
            elif char in {")", "]", "}"} and stack:
                start = stack.pop()
                opening = block[start]
                if opening["text"] != {")": "(", "]": "[", "}": "{"}[char]:
                    stack.clear()
                    continue
                # Attached function arguments, including textual labels such as
                # Cost(classical), remain mathematical candidate evidence.
                if start and not block[start - 1]["text"].isspace():
                    continue
                if math_font(opening) or math_font(glyph):
                    continue
                inside = "".join(g["text"] if not math_font(g) else " " for g in block[start + 1:index])
                words = re.findall(r"[A-Za-z]{2,}", inside)
                if any(word.lower() not in MATH_OPERATORS for word in words) or re.search(r"\b(?:i\.e\.|e\.g\.)", inside, re.I):
                    protected.update((opening["id"], glyph["id"]))
    return protected


def _boundary_diagnostics(glyphs: list[dict[str, Any]], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    protected = _prose_boundary_ids(glyphs)
    diagnostics = []
    for asset in assets:
        if asset["kind"] != "math":
            continue
        ids = {gid for fragment in asset["fragments"] for gid in fragment["glyph_ids"]}
        contaminated = [g["id"] for g in glyphs if g["id"] in ids & protected]
        if contaminated:
            diagnostics.append({"code": "prose-boundary-in-math", "asset_id": asset["id"], "glyph_ids": contaminated,
                                "action": "Inspect original prose/citation context and correct glyph ownership before approval."})
    return diagnostics


def _regions(page: fitz.Page, glyphs: list[dict[str, Any]], layout: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Use original visible paths when available: TeX accents and radicals often
    # have misleading native metric rectangles on an adjacent line.
    if hasattr(page, "get_svg_image"):
        from littrans.glyph_export import glyph_ink_boxes
        ink = glyph_ink_boxes(page, glyphs)
        glyphs = [{**g, "bbox": ink.get(g["id"], g["bbox"])} for g in glyphs]
    regions: list[dict[str, Any]] = []
    # A damaged character does not make the surrounding paragraph opaque.
    for glyph in glyphs:
        if "\ufffd" in glyph["text"] or (any(ord(c) < 32 and c not in "\t\r\n" for c in glyph["text"]) and not MATH_FONT.search(glyph["font"])):
            regions.append({"kind": "mixed-region", "bbox": list(glyph["bbox"]), "glyph_ids": [glyph["id"]], "provenance": ["invalid-native-text:recovery-required"], "display": False, "grouping_pending": True})
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
    protected_prose = _prose_boundary_ids(glyphs)
    for line in lines.values():
        run: list[dict[str, Any]] = []
        for position, glyph in enumerate([*line, {"text": "\u0000", "font": "", "bbox": [0, 0, 0, 0]}]):
            mathematical = bool(MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]) or (separate_roman_math and re.match(r"CM(?:R|BX)\d", glyph["font"], re.I)))
            if glyph.get("id") not in protected_prose and (mathematical or (run and glyph["text"] in "0123456789()[]{}+-*/., ")):
                if mathematical and not run and not glyph["text"].isspace():
                    # Normal-font prefixes are common in U(N), diag(...), 2π.
                    prefix: list[dict[str, Any]] = []
                    for previous in reversed(line[:position]):
                        if previous["id"] in protected_prose:
                            break
                        if previous["text"].isspace() and not prefix:
                            continue
                        if previous["text"].isspace() or not re.fullmatch(r"[A-Za-z0-9([{}.*+/-]", previous["text"]):
                            break
                        prefix.insert(0, previous)
                    prefix_text = "".join(g["text"] for g in prefix)
                    # A preceding prose article is not a mathematical prefix.
                    if prefix and line[position - 1]["text"].isspace() and glyph["text"] not in "=<>±×÷":
                        prefix = []
                        prefix_text = ""
                    if re.fullmatch(r"(?:[A-Za-z]|diag|rank|Tr|tr|sin|cos|tan|log|exp|lim|sup|inf|max|min|det|dim|ker|span)?[0-9([{}.*+/-]*", prefix_text):
                        run.extend(prefix)
                run.append(glyph)
            elif run:
                while run and run[-1]["text"] in " .,":
                    run.pop()
                if run:
                    regions.append({"kind": "math", "bbox": _union([g["bbox"] for g in run]), "glyph_ids": [g["id"] for g in run], "provenance": ["native-math-glyphs"], "display": False, "grouping_pending": False})
                run = []
    for image in page.get_image_info():
        regions.append({"kind": "figure", "bbox": list(image["bbox"]), "provenance": ["native-image"], "display": True, "grouping_pending": False})
    # Vector-only diagrams, fraction bars and accents must not disappear from the ledger.
    for drawing in page.get_drawings():
        bbox = list(drawing["rect"])
        bbox = [bbox[0] - 0.5, bbox[1] - 0.5, bbox[2] + 0.5, bbox[3] + 0.5]
        regions.append({"kind": "mixed-region", "bbox": bbox, "provenance": ["native-vector"], "display": True, "grouping_pending": True})
    # Detector rectangles are proposals, not authority to consume prose. Font
    # metrics overlap adjacent words even when the visible ink is disjoint.
    native_ids = {gid for r in regions for gid in r.get("glyph_ids", [])}
    prose_ids: set[str] = set(protected_prose)
    for line in lines.values():
        word: list[dict[str, Any]] = []
        for glyph in [*line, {"text": " "}]:
            if glyph["text"].isalpha() and glyph.get("id") not in native_ids:
                word.append(glyph)
            else:
                if len(word) >= 2 and "".join(g["text"] for g in word).lower() not in MATH_OPERATORS:
                    prose_ids.update(g["id"] for g in word)
                word = []
    clean = []
    for region in regions:
        if "glyph_ids" not in region:
            owned = [g for g in glyphs if g["text"].strip() and _inside(g, region["bbox"])]
            if region["kind"] == "math" and (not owned or any(g["id"] in prose_ids for g in owned)):
                # Fall back to the finer native math runs, not the whole block.
                continue
            if region["kind"] in {"math", "mixed-region"} and owned:
                region["glyph_ids"] = [g["id"] for g in owned if g["id"] not in prose_ids]
                if not region["glyph_ids"]:
                    # A vector crossing ordinary text still needs original evidence.
                    if "native-vector" not in region["provenance"]:
                        continue
                    region.pop("glyph_ids")
        clean.append(region)
    regions = clean
    # Merge formula components and their rules, but never expand to a PDF text
    # block. Explicit ownership prevents overlapping font boxes stealing prose.
    changed = True
    while changed:
        changed = False
        for i, left in enumerate(regions):
            for j in range(i + 1, len(regions)):
                right = regions[j]
                shared = set(left.get("glyph_ids", [])) & set(right.get("glyph_ids", []))
                overlap = _intersects(left["bbox"], right["bbox"])
                detector_math = left["kind"] == right["kind"] == "math" and any(p.startswith("PP-DocLayoutV2:") for p in left["provenance"] + right["provenance"])
                left_g = [g for g in glyphs if g["id"] in left.get("glyph_ids", []) and g["text"].strip()]
                right_g = [g for g in glyphs if g["id"] in right.get("glyph_ids", []) and g["text"].strip()]
                near_baseline = not left_g or not right_g or min(abs(a.get("baseline", 0) - b.get("baseline", 0)) for a in left_g for b in right_g) < max(g.get("size", 10) for g in left_g + right_g) * .7
                if shared or (overlap and not detector_math and near_baseline):
                    display = any(r["display"] and any(p != "native-vector" for p in r["provenance"]) for r in (left, right))
                    left["bbox"] = _union([left["bbox"], right["bbox"]])
                    if "glyph_ids" in left or "glyph_ids" in right:
                        ids = set(left.get("glyph_ids", [])) | set(right.get("glyph_ids", []))
                        left["glyph_ids"] = [g["id"] for g in glyphs if g["id"] in ids]
                    if left["kind"] != right["kind"]:
                        kinds = {left["kind"], right["kind"]}
                        left["kind"] = "table" if "table" in kinds else "figure" if "figure" in kinds else "math" if "math" in kinds and "native-vector" in left["provenance"] + right["provenance"] else "mixed-region"
                    if left["kind"] in {"figure", "table", "code"}:
                        left.pop("glyph_ids", None)
                    left["provenance"] = sorted(set(left["provenance"] + right["provenance"]))
                    left["grouping_pending"] = left["kind"] == "mixed-region"
                    # A fraction bar/accent alone does not turn inline math into display math.
                    left["display"] = display
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
        changes = {k: region[k] for k in ("kind", "display", "grouping_pending", "formula_conditions") if k in region}
        if "formula_conditions" in changes:
            hashes = [_hash({"source_sha256": existing.source_sha256, "page": f.page, "bbox": f.bbox, "glyph_ids": f.glyph_ids,
                             **({"export_method": f.export_method} if f.export_method != "raw-region" else {})}) for f in existing.fragments]
            original = hashes[0] if len(hashes) == 1 else _hash(hashes)
            changes["content_sha256"] = _hash({"original_content": original, "formula_conditions": changes["formula_conditions"]}) if changes["formula_conditions"] else original
        return FidelityAsset.model_validate({**existing.model_dump(mode="json"), **changes})
    if "fragments" in region:
        children = []
        for fragment in region["fragments"]:
            if fragment.get("page", page_number) != page_number:
                raise ValueError("page review fragments must remain on their reviewed page; use continuation links across pages")
            child = {k: v for k, v in region.items() if k not in {"fragments", "id", "formula_conditions"}}
            child["bbox"] = fragment["bbox"]
            if "glyph_ids" in fragment:
                child["glyph_ids"] = fragment["glyph_ids"]
            children.append(_asset(root, doc, page_number, source_hash, child, glyphs))
        if not children:
            raise ValueError("region fragments must not be empty")
        content = children[0].content_sha256 if len(children) == 1 else _hash([a.content_sha256 for a in children])
        if region.get("formula_conditions"):
            content = _hash({"original_content": content, "formula_conditions": region["formula_conditions"]})
        return FidelityAsset(id=region.get("id") or f"a-p{page_number:04d}-{content[:12]}", kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[f for a in children for f in a.fragments], grouping_pending=region.get("grouping_pending", False), display=region.get("display", True), provenance=region.get("provenance", ["visual-multifragment-correction"]), formula_conditions=region.get("formula_conditions", []))
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
        try:
            owned_svg, geometry = build_owned_fragment(page, owned, list(rect))
            rect = fitz.Rect(geometry["bbox"])
        except ValueError as exc:
            # Keep original evidence available, but require boundary correction.
            # Do not claim a raw crop is an isolated mathematical expression.
            export_method = "raw-region"
            region = {**region, "kind": "mixed-region", "grouping_pending": True,
                      "provenance": [*region.get("provenance", []), "precise-export-unavailable:" + str(exc)]}
    identity = {"source_sha256": source_hash, "page": page_number, "bbox": _box(rect), "glyph_ids": [g["id"] for g in owned]}
    if export_method != "raw-region":
        identity["export_method"] = export_method
    content = _hash(identity)
    if region.get("formula_conditions"):
        content = _hash({"original_content": content, "formula_conditions": region["formula_conditions"]})
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
    return FidelityAsset(id=aid, kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[fragment], grouping_pending=region.get("grouping_pending", False), display=region.get("display", False), provenance=region.get("provenance", ["visual-region-correction"]), formula_conditions=region.get("formula_conditions", []))


def _make_unit(page: int, uid: str, text: str, bbox: Any, assets: dict[str, FidelityAsset], kind: str = "paragraph", **extra: Any) -> SourceUnit:
    refs = asset_reference_ids(text)
    hashes = {aid: assets[aid].content_sha256 for aid in refs}
    extra.setdefault("protected_tokens", protected_tokens(re.sub(r"\{\{asset:[^}]+\}\}", "", text)))
    extra.setdefault("translatable", True)
    payload = {"page": page, "text": text, "bbox": _box(bbox), "asset_content_hashes": hashes, "kind": kind, **extra}
    pure_math = kind != "footnote" and bool(refs) and not re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip() and all(assets[aid].kind == "math" for aid in refs)
    if pure_math:
        kind = "equation"
        payload["kind"] = kind
        extra["translatable"] = any(assets[aid].formula_conditions for aid in refs)
        payload["translatable"] = extra["translatable"]
    return SourceUnit(unit_id=uid, page=page, kind=UnitKind(kind), bbox=_box(bbox), source_text=text, source_markdown=text, source_hash=_hash(payload), confidence=0, latex=None, asset_content_hashes=hashes, asset_refs=[AssetRef(kind="fidelity", path=f.png_path, bbox=f.bbox) for aid in dict.fromkeys(refs) for f in assets[aid].fragments], **extra)


def _separate_display_units(units: list[SourceUnit], assets: dict[str, FidelityAsset]) -> list[SourceUnit]:
    """Keep display formulas selectable and bind their printed equation labels."""
    result = []
    number_pattern = r"\(((?:[A-Z]\.)?\d+(?:\.\d+)*(?:[a-z])?)\)"
    for unit in units:
        text = unit.source_markdown or unit.source_text
        refs = asset_reference_ids(text)
        displays = [aid for aid in refs if assets[aid].kind == "math" and assets[aid].display]
        plain = re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip()
        label = re.fullmatch(number_pattern, plain)
        if len(refs) == 1 and displays and (not plain or label):
            result.append(_make_unit(unit.page, unit.unit_id, "{{asset:" + refs[0] + "}}", unit.bbox, assets, equation_number=label[1] if label else None))
        elif displays:
            chunks, pending = [], ""
            for part in re.split(r"(\{\{asset:[^}]+\}\})", text):
                part_ids = asset_reference_ids(part)
                if part_ids and part_ids[0] in displays:
                    if pending.strip():
                        chunks.append((pending.strip(), unit.bbox, unit.kind.value))
                    chunks.append((part, assets[part_ids[0]].fragments[0].bbox, "equation"))
                    pending = ""
                else:
                    pending += part
            if pending.strip():
                chunks.append((pending.strip(), unit.bbox, unit.kind.value))
            result.extend(_make_unit(unit.page, unit.unit_id if i == 0 else f"{unit.unit_id}-displaypart{i + 1}", body, box, assets, kind=kind) for i, (body, box, kind) in enumerate(chunks))
        else:
            result.append(unit)
    removed = set()
    for index, unit in enumerate(result):
        label = re.fullmatch(number_pattern, unit.source_text.strip())
        if not label:
            continue
        y = (unit.bbox[1] + unit.bbox[3]) / 2
        candidates = [(i, other) for i, other in enumerate(result) if other.kind == UnitKind.EQUATION and not other.equation_number and any(assets[aid].display and assets[aid].fragments[0].bbox[1] - 3 <= y <= assets[aid].fragments[0].bbox[3] + 3 for aid in asset_reference_ids(other.source_text))]
        if len(candidates) == 1:
            i, other = candidates[0]
            result[i] = _make_unit(other.page, other.unit_id, other.source_text, _union([unit.bbox, other.bbox]), assets, equation_number=label[1])
            removed.add(index)
    return [unit for i, unit in enumerate(result) if i not in removed]


def _expanded_native(page: fitz.Page, original_glyphs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retain original glyph identities when a wider canvas changes PDF blocks."""
    from collections import defaultdict, deque
    def key(glyph: dict[str, Any]) -> tuple[Any, ...]:
        return (glyph["text"], glyph["font"], round(glyph["size"], 6),
                *(round(value, 6) for value in glyph["origin"]))
    available: dict[tuple[Any, ...], Any] = defaultdict(deque)
    for glyph in original_glyphs:
        available[key(glyph)].append(glyph)
    glyphs, blocks = _native(page)
    remap, old_ids = {}, {g["id"] for g in original_glyphs}
    for glyph in glyphs:
        previous = available[key(glyph)].popleft() if available[key(glyph)] else None
        old_id = glyph["id"]
        glyph["id"] = previous["id"] if previous else "overflow-" + old_id
        if previous:
            glyph["line"] = previous["line"]
        else:
            glyph["line"] = "overflow-" + glyph["line"]
        remap[old_id] = glyph["id"]
    if any(available.values()) or len({g["id"] for g in glyphs}) != len(glyphs):
        raise ValueError("expanded canvas cannot preserve original glyph identities")
    used_blocks: set[str] = set()
    for block in blocks:
        block["lines"] = [[remap[gid] for gid in line] for line in block["lines"]]
        originals = [gid for line in block["lines"] for gid in line if gid in old_ids]
        preferred = originals[0].split("-", 1)[0] if originals else "overflow-" + block["id"]
        block["id"] = preferred if preferred not in used_blocks else "overflow-" + block["id"]
        used_blocks.add(block["id"])
    return glyphs, blocks


def _page_prepare(root: Path, doc: fitz.Document, number: int, source_hash: str, layout: dict[str, Any], override: dict[str, Any] | None = None) -> tuple[list[SourceUnit], list[FidelityAsset], dict[str, Any]]:
    page = doc[number - 1]
    original_page_bbox = list(page.rect)
    original_glyphs, original_blocks = _native(page)
    canvas = override.get("page_canvas_bbox") if override else None
    if canvas is not None:
        expanded = fitz.Rect(canvas)
        if (page.rotation or page.rect.x0 or page.rect.y0 or expanded.is_empty
                or expanded.x0 != 0 or expanded.y0 != 0
                or expanded.width < page.rect.width or expanded.height < page.rect.height):
            raise ValueError("page_canvas_bbox must expand an unrotated origin-zero source page")
        # Change only this in-memory PDF instance; the immutable source file is
        # never rewritten. This reveals existing content-stream ink, not new ink.
        page.set_mediabox(expanded)
        page.set_cropbox(expanded)
    glyphs, blocks = _expanded_native(page, original_glyphs) if canvas is not None else (original_glyphs, original_blocks)
    image_path = root / f"evidence/pages/fidelity-p{number:04d}.png"
    overflow_evidence = None
    if canvas is not None:
        if not any(g["bbox"][2] > original_page_bbox[2] or g["bbox"][3] > original_page_bbox[3] for g in glyphs):
            raise ValueError("expanded canvas must recover existing off-page native glyphs")
        overflow_path = root / f"evidence/pages/fidelity-p{number:04d}-overflow-{_hash(canvas)[:12]}.png"
        page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(overflow_path)
        overflow_evidence = {"path": overflow_path.relative_to(root).as_posix(), "sha256": sha256_file(overflow_path)}
    from littrans.source_structure import (
        assemble_structure,
        coalesce_inline_assets,
        plan_structure,
        styled_text,
    )
    items = layout.get("pages", {}).get(str(image_path.resolve()), [])
    structure = plan_structure(glyphs, blocks, items, page.rect.height)
    from littrans.structure_profile import structure_context
    profile_context = structure_context(root)
    if profile_context:
        structure["document_profile"] = {key: profile_context[key] for key in ("path", "sha256", "authority")}
    blocks = structure["blocks"]
    regions = override["regions"] if override and "regions" in override else _regions(page, [g for g in glyphs if g["id"] not in structure["markers"]], items)
    if not glyphs and not regions and page.get_images():
        regions = [{"kind": "mixed-region", "bbox": list(page.rect), "grouping_pending": True, "display": True, "provenance": ["no-text-layer"]}]
    assets = [_asset(root, doc, number, source_hash, r, glyphs) for r in regions]
    for asset in assets:
        _formula_condition_glyphs(asset.model_dump(mode="json"), glyphs)
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
        footnote_refs = []
        for line in block["lines"]:
            tokens = []
            for gid in line:
                aid = owners.get(gid)
                marker = structure["markers"].get(gid)
                font = glyph_by_id[gid]["font"]
                style = "***" if re.search(r"SFBI|BoldItalic|BoldOblique", font, re.I) else "**" if re.search(r"SFBX|Bold", font, re.I) else "*" if re.search(r"SFTI|Italic|Oblique", font, re.I) else ""
                if marker:
                    if not marker["definition"]:
                        tokens.append(("[^" + marker["number"] + "]", ""))
                        footnote_refs.append(f"p{number:04d}-" + marker["note_block"])
                elif aid:
                    if aid not in emitted:
                        before, after = edge_spaces[aid]
                        tokens.append(((" " if before else "") + "{{asset:" + aid + "}}" + (" " if after else ""), ""))
                        emitted.add(aid)
                else:
                    tokens.append((glyph_by_id[gid]["text"], style))
            rendered = styled_text(tokens).strip()
            if rendered:
                lines.append(rendered)
        text = " ".join(lines)
        text = re.sub(r"([a-z])-\s+([a-z])", r"\1\2", text)
        if text:
            kind = "paragraph"
            semantic_labels = {"doc_title": "heading", "paragraph_title": "heading", "title": "heading", "figure_caption": "caption", "table_caption": "caption", "footnote": "footnote", "reference": "bibliography", "list": "list_item"}
            for item in layout.get("pages", {}).get(str(image_path.resolve()), []):
                if item.get("label") in semantic_labels and _inside({"bbox": block["bbox"]}, [v / 2 for v in item["bbox"]]):
                    kind = semantic_labels[item["label"]]
                    break
            units.append(_make_unit(number, f"p{number:04d}-{block['id']}", text, block["bbox"], by_id, kind=kind, footnote_refs=list(dict.fromkeys(footnote_refs))))
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
    units = _separate_display_units(units, by_id)
    units = assemble_structure(units, by_id, structure, _make_unit)
    if not (override and "units" in override):
        units = coalesce_inline_assets(units, by_id, _make_unit, _hash)
    assets = list(by_id.values())
    owners = {gid:a.id for a in assets for f in a.fragments for gid in f.glyph_ids}
    if override and "units" in override:
        units = []
        for item in override["units"]:
            if item.get("latex") is not None:
                raise ValueError("source preservation cannot import LaTeX")
            extra = {k: item[k] for k in ("equation_number", "footnote_number", "footnote_refs", "parent_id", "continues_from_previous", "continued_to_next", "render_policy", "translatable") if k in item}
            units.append(_make_unit(number, item["unit_id"], item["source_markdown"], item["bbox"], by_id, item.get("kind", "paragraph"), **extra))
        refs = Counter(aid for unit in units for aid in asset_reference_ids(unit.source_text))
        if refs != Counter({aid: 1 for aid in by_id}):
            raise ValueError("unit overrides must reference each page asset exactly once")
    ledger = {"schema_version": 6, "page": number, "source_sha256": source_hash, "width": page.rect.width, "height": page.rect.height, "page_image": str(image_path.relative_to(root)).replace("\\", "/"), "page_image_sha256": sha256_file(image_path), "layout_status": layout["status"], "layout_reason": layout.get("reason"), "layout_fingerprint": layout.get("fingerprint"), "glyphs": [{**g, "owner": owners.get(g["id"], "native-text")} for g in glyphs], "vector_regions": [_box(d["rect"]) for d in page.get_drawings()], "raster_regions": [_box(i["bbox"]) for i in page.get_image_info()], "asset_ids": list(by_id), "unit_ids": [u.unit_id for u in units], "grouping_pending": [a.id for a in assets if a.grouping_pending], "reading_order_review_required": True, "source_overrides": override, "structure": {k: v for k, v in structure.items() if k != "blocks"}}
    if overflow_evidence:
        ledger["original_page_bbox"] = original_page_bbox
        ledger["overflow_evidence"] = overflow_evidence
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
    from littrans.structure_profile import structure_context
    profile_context = structure_context(root)
    with project_write_lock(root), _authority_transaction(root, pages), fitz.open(source) as doc:
        old = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        registry = load_assets(root)
        needed = [p for p in pages if replace or not _page_path(root, p).is_file()]
        if not needed:
            return {"pages": pages, "prepared_pages": [], "cached_pages": pages, "assets": len(registry), "requires_visual_review": True, "document_structure": profile_context}
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
    return {"pages": pages, "prepared_pages": needed, "cached_pages": [p for p in pages if p not in needed], "assets": len(registry), "layout_status": layout["status"], "requires_visual_review": True, "review_packet": packet["packet_path"], "visual_report": packet["visual_report"], "document_structure": profile_context}


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
    if ledger.get("overflow_evidence"):
        overflow = ledger["overflow_evidence"]
        files[overflow["path"]] = sha256_file(_path(root, overflow["path"]))
        if files[overflow["path"]] != overflow["sha256"]:
            raise ValueError("overflow canvas image changed")
    for asset in selected:
        if asset.source_sha256 != ledger["source_sha256"]:
            raise ValueError("asset PDF fingerprint does not match page")
        fragment_hashes = [_hash({"source_sha256": asset.source_sha256, "page": f.page, "bbox": f.bbox, "glyph_ids": f.glyph_ids,
                                 **({"export_method": f.export_method} if f.export_method != "raw-region" else {})}) for f in asset.fragments]
        expected_content = fragment_hashes[0] if len(fragment_hashes) == 1 else _hash(fragment_hashes)
        if asset.formula_conditions:
            expected_content = _hash({"original_content": expected_content, "formula_conditions": [c.model_dump(mode="json") for c in asset.formula_conditions]})
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
    from littrans.structure_profile import structure_context
    profile_context = structure_context(root)
    if profile_context:
        payload["document_structure"] = profile_context
    for page in payload["pages"]:
        page["boundary_diagnostics"] = _boundary_diagnostics(page["ledger"]["glyphs"], page["assets"])
    packet_id = "source-" + _hash(payload)[:20]
    directory = root / "packets" / packet_id
    directory.mkdir(parents=True, exist_ok=True)
    packet = directory / "packet.json"
    write_json(packet, payload)
    review_template = {"packet_id": packet_id, "packet_sha256": sha256_file(packet), "reviewer": "", "pages": [{"page": p["page"], "fingerprint": p["fingerprint"], "viewed_original": False, "coverage_complete": False, "boundaries_complete": False, "reading_order_correct": False, "grouping_checked": False, "layout_fallback_checked": False, "formula_conditions_checked": False, "overflow_canvas_checked": False, "issues": [], "notes": ""} for p in payload["pages"]]}
    write_json(directory / "review-template.json", review_template)
    sections = []
    if profile_context:
        sections.append('<h2>Document-specific structure guidance</h2><pre>' + html.escape(json.dumps(profile_context, ensure_ascii=False, indent=2)) + '</pre>')
    for p in payload["pages"]:
        ledger = p["ledger"]
        if p["boundary_diagnostics"]:
            sections.append("<h3>Prose/formula boundary diagnostics</h3><pre>" + html.escape(json.dumps(p["boundary_diagnostics"], ensure_ascii=False, indent=2)) + "</pre>")
        boxes = "".join(f'<rect x="{f["bbox"][0]}" y="{f["bbox"][1]}" width="{f["width"]}" height="{f["height"]}" fill="none" stroke="red" stroke-width="0.6"><title>{html.escape(a["id"])}</title></rect>' for a in p["assets"] for f in a["fragments"])
        image_uri = _path(root, ledger["page_image"]).as_uri()
        if ledger.get("overflow_evidence"):
            sections.append(f'<p>PDF page {p["page"]}: <a href="{image_uri}">original page canvas</a>. The overlay below uses the separately preserved expanded content-stream canvas.</p>')
            image_uri = _path(root, ledger["overflow_evidence"]["path"]).as_uri()
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
    from littrans.structure_profile import structure_context
    if packet.get("document_structure") != structure_context(root):
        raise ValueError("source structure guidance changed since packet creation; create a new packet")
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
            if by_page[p]["ledger"].get("overflow_evidence"):
                fields.append("overflow_canvas_checked")
            if any(asset.get("formula_conditions") for asset in by_page[p]["assets"]):
                fields.append("formula_conditions_checked")
            passed = all(decision.get(key) is True for key in fields) and decision.get("issues") == []
            opaque = _opaque_prose_assets(by_page[p])
            if opaque:
                passed = False
                decision = {**decision, "extraction_issues": [{"code": "recoverable-prose-in-image", "assets": opaque}]}
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


def _formula_condition_glyphs(asset: dict[str, Any], glyphs: list[dict[str, Any]]) -> set[str]:
    """Validate explicit language ownership without recognizing/transcribing math."""
    conditions = asset.get("formula_conditions", [])
    if not conditions:
        return set()
    if asset["kind"] != "math" or not asset.get("display"):
        raise ValueError("formula_conditions require a displayed math asset")
    owned = {gid for fragment in asset["fragments"] for gid in fragment["glyph_ids"]}
    declared: set[str] = set()
    for condition in conditions:
        ids = condition["glyph_ids"]
        selected = [g for g in glyphs if g["id"] in ids]
        if (not ids or len(ids) != len(set(ids)) or declared.intersection(ids)
                or not set(ids) <= owned or [g["id"] for g in selected] != ids):
            raise ValueError("formula condition glyph IDs must be unique, owned and in native order")
        if "".join(g["text"] for g in selected) != condition["source_text"]:
            raise ValueError("formula condition source_text must exactly match its native glyphs")
        if not re.search(r"[A-Za-z]{2,}", condition["source_text"]):
            raise ValueError("formula condition must identify native language")
        if max(g["baseline"] for g in selected) - min(g["baseline"] for g in selected) > max(g["size"] for g in selected) * .8:
            raise ValueError("each formula condition must stay on one visual line")
        declared.update(ids)
    return declared


def _opaque_prose_assets(current: dict[str, Any]) -> list[str]:
    """Image ownership is not textual coverage of recoverable paragraphs."""
    glyphs = current["ledger"]["glyphs"]
    separate_roman = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * .2
    opaque = []
    for asset in current["assets"]:
        if asset["kind"] not in {"math", "mixed-region"}:
            continue
        ids = {gid for f in asset["fragments"] for gid in f["glyph_ids"]} - _formula_condition_glyphs(asset, glyphs)
        prose = "".join(g["text"] if g["id"] in ids and not MATH_FONT.search(g["font"]) and not (separate_roman and re.match(r"CM(?:R|BX)\d", g["font"], re.I)) else " " for g in glyphs)
        if len([word for word in re.findall(r"[A-Za-z]{2,}", prose) if word.lower() not in MATH_OPERATORS]) >= 6:
            opaque.append(asset["id"])
    return opaque


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
            opaque = _opaque_prose_assets(current)
            if opaque:
                raise ValueError("recoverable prose remains inside image assets; re-prepare or split source regions: " + ", ".join(opaque))
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
