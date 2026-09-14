"""Preserve explicitly reviewed PDF glyph paths; never infer a character/LaTeX.

This narrow adapter supports MuPDF glyph uses and horizontal rules.
Unrelated grouped drawings are measured with their transforms and clipping intact.
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

import pymupdf as fitz

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


def _rectangle_path(d: str) -> tuple[float, float, float, float] | None:
    """Corners (left, top, right, bottom) of an axis-aligned ``M x y H x1 V y1 H x Z`` path."""
    filled = re.fullmatch(
        "M(" + NUMBER + ") (" + NUMBER + ")H(" + NUMBER + ")V(" + NUMBER + ")H(" + NUMBER + ")Z?", d
    )
    if not filled:
        return None
    left, top, right, bottom, back = (float(v) for v in filled.groups())
    if abs(back - left) > .01:
        return None
    return left, top, right, bottom


def _horizontal_rule(node: ET.Element, matrix: list[float]) -> tuple[float, float, float] | None:
    """Recognize a fraction bar/underline drawn as a stroked line or a thin filled rectangle.

    Returns page-space (x0, x1, y) for an axis-aligned rule, or None for any other path.
    """
    if any(abs(matrix[i] - v) > .0001 for i, v in enumerate((1, 0, 0))) or abs(abs(matrix[3]) - 1) > .0001:
        return None
    d = node.get("d", "")
    stroked = re.fullmatch("M0 0H(" + NUMBER + ")", d)
    if stroked:
        return matrix[4], matrix[4] + float(stroked[1]), matrix[5]
    corners = _rectangle_path(d)
    if corners is None:
        return None
    left, top, right, bottom = corners
    if abs(bottom - top) > 1.5:
        return None
    x0, x1 = sorted((left + matrix[4], right + matrix[4]))
    y = matrix[5] + matrix[3] * (top + bottom) / 2
    return x0, x1, y


def _clip_covers_page(source: ET.Element, reference: str, page_rect: Any) -> bool:
    """Whether ``clip-path="url(#id)"`` is one axis-aligned rectangle containing the page."""
    match = re.fullmatch(r"url\(#([^)]+)\)", reference.strip())
    if not match:
        return False
    clip = next((node for node in source.iter(f"{{{SVG}}}clipPath") if node.get("id") == match[1]), None)
    if clip is None or len(clip) != 1 or clip[0].tag.split("}")[-1] != "path":
        return False
    try:
        matrix = _matrix(clip[0])
    except ValueError:
        return False
    corners = _rectangle_path(clip[0].get("d", ""))
    if corners is None:
        return False
    a, b, c, d, e, f = matrix
    xs = [a * x + c * y + e for x in corners[::2] for y in corners[1::2]]
    ys = [b * x + d * y + f for x in corners[::2] for y in corners[1::2]]
    tolerance = .05
    return bool(min(xs) <= page_rect.x0 + tolerance and min(ys) <= page_rect.y0 + tolerance
                and max(xs) >= page_rect.x1 - tolerance and max(ys) >= page_rect.y1 - tolerance)


def _page_content(source: ET.Element, page_rect: Any) -> list[ET.Element]:
    """Drawing nodes of the page SVG in page space.

    MuPDF wraps the whole page in ``<g clip-path>`` whenever the PDF CropBox differs
    from its MediaBox; that wrapper is clipping only, so its children keep page
    coordinates. Any group with a transform, a smaller clip or other attributes is
    returned intact for the callers' fail-closed group handling.
    """
    nodes: list[ET.Element] = []

    def collect(parent: ET.Element) -> None:
        for node in parent:
            if (node.tag.split("}")[-1] == "g" and set(node.attrib) == {"clip-path"}
                    and _clip_covers_page(source, node.get("clip-path", ""), page_rect)):
                collect(node)
            else:
                nodes.append(node)

    collect(source)
    return nodes


def glyph_ink_boxes(page: fitz.Page, glyphs: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Measure original glyph paths for grouping; font metrics are not ink bounds.

    This supplies geometry only, never decoded text or formula candidates.
    Unsupported SVG structures leave the caller's native geometry intact.
    """
    source = ET.fromstring(page.get_svg_image(text_as_path=True))
    definitions = source.find(f"{{{SVG}}}defs")
    if definitions is None:
        return {}
    cache: dict[tuple[Any, ...], fitz.Rect] = {}
    result = {}
    for node in _page_content(source, page.rect):
        if node.tag.split("}")[-1] != "use":
            continue
        try:
            matrix = _matrix(node)
        except ValueError:
            continue
        matches = [g for g in glyphs if abs(g["origin"][0] - matrix[4]) < .03 and abs(g["origin"][1] - matrix[5]) < .03]
        if not matches:
            continue
        href = node.get(f"{{{XLINK}}}href", "")
        key = (href, *matrix[:4])
        if key not in cache:
            probe_svg = ET.Element(f"{{{SVG}}}svg", {"width": "200", "height": "200"})
            probe_svg.append(copy.deepcopy(definitions))
            use = copy.deepcopy(node)
            use.set("transform", "matrix(" + " ".join(map(str, [*matrix[:4], 100, 100])) + ")")
            probe_svg.append(use)
            with fitz.open("svg", ET.tostring(probe_svg)) as probe:
                drawings = probe[0].get_drawings()
            if not drawings:
                continue
            box = fitz.Rect(drawings[0]["rect"])
            for drawing in drawings[1:]:
                box |= drawing["rect"]
            cache[key] = box + (-100, -100, -100, -100)
        box = cache[key] + (matrix[4], matrix[5], matrix[4], matrix[5])
        for glyph in matches:
            result[glyph["id"]] = list(box)
    return result


def build_owned_fragment(page: fitz.Page, owned: list[dict[str, Any]],
                         bbox: list[float] | None = None) -> tuple[str, dict[str, Any]]:
    source = ET.fromstring(page.get_svg_image(text_as_path=True))
    # Some PDF math fonts encode visible stretch delimiters as a space.
    # Native Unicode whitespace is not evidence that the original path is blank.
    use_origins = []
    for node in _page_content(source, page.rect):
        if node.tag.split("}")[-1] == "use":
            use_origins.append(_matrix(node)[4:])
    glyphs = [g for g in owned if str(g["text"]).strip() or any(
        abs(g.get("origin", [float("inf"), float("inf")])[0] - xy[0]) < .03
        and abs(g.get("origin", [float("inf"), float("inf")])[1] - xy[1]) < .03
        for xy in use_origins)]
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
    for node in _page_content(source, page.rect):
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
            rule = _horizontal_rule(node, matrix)
            if rule is None:
                # Unrelated drawings elsewhere on the page do not prevent a
                # precise text fragment. Measure the original path before
                # excluding it; intersecting unsupported paths still fail closed.
                probe_svg = ET.Element(f"{{{SVG}}}svg", dict(target.attrib))
                probe_svg.append(copy.deepcopy(node))
                with fitz.open("svg", ET.tostring(probe_svg)) as probe:
                    drawing_boxes = [fitz.Rect(d["rect"]) + (-1, -1, 1, 1) for d in probe[0].get_drawings()]
                if drawing_boxes and all(not b.intersects(box) for b in drawing_boxes):
                    continue
                raise ValueError("Unsupported PDF vector primitive; retain raw region")
            x0, x1, y = rule
            nearby = any(g["bbox"][0] < x1 + 1 and g["bbox"][2] > x0 - 1
                         and g["bbox"][1] - 2 <= y <= g["bbox"][3] + 2 for g in glyphs)
            if nearby and box.x0 - 2 <= x0 <= x1 <= box.x1 + 2 and box.y0 - 2 <= y <= box.y1 + 2:
                target.append(copy.deepcopy(node))
                paths += 1
        elif tag == "g":
            # Clip-only wrappers without any drawable descendant carry no ink;
            # some PDF producers emit an empty page-sized clip group per page.
            if not any(child.tag.split("}")[-1] != "g" for child in node.iter() if child is not node):
                continue
            # Figures elsewhere on a page may use transformed or clipped groups.
            # Preserve all definitions and measure the intact group, including
            # raster images. Never flatten transforms or discard an intersecting
            # group: its glyph/path ownership has not been established here.
            probe_svg = ET.Element(f"{{{SVG}}}svg", dict(target.attrib))
            original_defs = source.find(f"{{{SVG}}}defs")
            if original_defs is not None:
                probe_svg.append(copy.deepcopy(original_defs))
            probe_svg.append(copy.deepcopy(node))
            with fitz.open("svg", ET.tostring(probe_svg)) as probe:
                drawing_boxes = [fitz.Rect(entry[1]) + (-1, -1, 1, 1)
                                 for entry in probe[0].get_bboxlog()]
            if drawing_boxes and all(not bounds.intersects(box) for bounds in drawing_boxes):
                continue
            raise ValueError("Unsupported intersecting PDF SVG group; retain raw region")
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
