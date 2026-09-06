"""Preserve explicitly reviewed PDF glyph paths; never infer a character/LaTeX.

This narrow adapter supports ungrouped MuPDF glyph uses and horizontal rules.
It fails closed on unknown structure instead of silently dropping drawing paths.
The raw PDF region remains separate original evidence.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
NUMBER = r"[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)


def _matrix(node: ET.Element) -> list[float]:
    transform = node.get("transform", "")
    if not transform.startswith("matrix("):
        raise ValueError("Unsupported PDF drawing transform; retain raw region")
    numbers = [float(n) for n in re.findall(NUMBER, transform)]
    if len(numbers) != 6:
        raise ValueError("Unsupported PDF drawing matrix; retain raw region")
    return numbers


def build_owned_fragment(page: fitz.Page, owned: list[dict[str, Any]],
                         bbox: list[float] | None = None) -> tuple[str, dict[str, Any]]:
    glyphs = [g for g in owned if str(g["text"]).strip()]
    if not glyphs or any("origin" not in g for g in glyphs):
        raise ValueError("Explicit source glyph origins are required")
    if len({g["id"] for g in glyphs}) != len(glyphs):
        raise ValueError("Duplicate explicit source glyph ownership")
    box = fitz.Rect(bbox) if bbox is not None else fitz.Rect(
        min(g["bbox"][0] for g in glyphs) - .5, min(g["bbox"][1] for g in glyphs) - .5,
        max(g["bbox"][2] for g in glyphs) + .5, max(g["bbox"][3] for g in glyphs) + .5,
    )
    source = ET.fromstring(page.get_svg_image(text_as_path=True))
    target = ET.Element(f"{{{SVG}}}svg", {
        "width": str(page.rect.width), "height": str(page.rect.height),
        "viewBox": f"0 0 {page.rect.width} {page.rect.height}",
    })
    definitions = ET.SubElement(target, f"{{{SVG}}}defs")
    used: set[str] = set()
    matched: set[str] = set()
    paths = 0
    for node in source:
        tag = node.tag.split("}")[-1]
        if tag == "defs":
            continue
        if tag == "use":
            matrix = _matrix(node)
            matches = [g for g in glyphs if abs(g["origin"][0] - matrix[4]) < .03
                       and abs(g["origin"][1] - matrix[5]) < .03]
            if not matches:
                continue
            href = node.get(f"{{{XLINK}}}href", "")
            if not href.startswith("#"):
                raise ValueError("Nonlocal SVG glyph reference")
            matched.update(g["id"] for g in matches)
            used.add(href[1:])
            target.append(copy.deepcopy(node))
        elif tag == "path":
            matrix = _matrix(node)
            rule = re.fullmatch("M0 0H(" + NUMBER + ")", node.get("d", ""))
            if not rule or any(abs(matrix[i] - v) > .0001 for i, v in enumerate((1, 0, 0))) or abs(abs(matrix[3]) - 1) > .0001:
                raise ValueError("Unsupported PDF vector primitive; retain raw region")
            x0, y = matrix[4:]
            x1 = x0 + float(rule[1])
            nearby = any(g["bbox"][0] < x1 + 1 and g["bbox"][2] > x0 - 1
                         and g["bbox"][1] - 2 <= y <= g["bbox"][3] + 2 for g in glyphs)
            if nearby and box.x0 - 2 <= x0 <= x1 <= box.x1 + 2 and box.y0 - 2 <= y <= box.y1 + 2:
                target.append(copy.deepcopy(node))
                paths += 1
        else:
            raise ValueError(f"Unsupported PDF SVG structure {tag}; retain raw region")
    missing = {g["id"] for g in glyphs} - matched
    if missing:
        raise ValueError(f"Unmapped explicit source glyphs: {sorted(missing)}")
    source_defs = source.find(f"{{{SVG}}}defs")
    if source_defs is None:
        raise ValueError("Missing original PDF glyph definitions")
    for node in source_defs:
        if node.get("id") in used:
            definitions.append(copy.deepcopy(node))
    if {node.get("id") for node in definitions} != used:
        raise ValueError("Missing original PDF glyph paths")
    # PDF font metrics can put an accent's reported box *below* its visible ink.
    # Measure the selected original paths on the full page before clipping them.
    with fitz.open("svg", ET.tostring(target)) as probe:
        drawings = probe[0].get_drawings()
        if not drawings:
            raise ValueError("No visible original glyph paths")
        ink = fitz.Rect(drawings[0]["rect"])
        for drawing in drawings[1:]:
            ink |= drawing["rect"]
    box |= ink + (-.5, -.5, .5, .5)
    target.set("width", str(box.width))
    target.set("height", str(box.height))
    target.set("viewBox", f"{box.x0} {box.y0} {box.width} {box.height}")
    baseline = Counter(round(g["origin"][1], 3) for g in glyphs).most_common(1)[0][0]
    return ET.tostring(target, encoding="unicode"), {"method": "explicit-pdf-glyph-paths-v2-ink-bounds", "owned_glyphs": len(glyphs),
            "matched_glyphs": len(matched), "retained_paths": paths,
            "bbox": list(box), "width": box.width, "height": box.height,
            "baseline": baseline - box.y0}


def export_owned_fragment(page: fitz.Page, owned: list[dict[str, Any]],
                          svg_path: Path, png_path: Path, dpi: int,
                          bbox: list[float] | None = None) -> dict[str, Any]:
    svg, metadata = build_owned_fragment(page, owned, bbox)
    svg_path.write_text(svg, encoding="utf-8")
    with fitz.open("svg", svg.encode()) as document:
        document[0].get_pixmap(dpi=dpi, alpha=False).save(png_path)
    return {**metadata, "dpi": dpi}
