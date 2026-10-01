"""Small geometric decisions shared by extraction and layout diagnostics."""
from __future__ import annotations

import re
from typing import Any


def fraction_components(glyphs: list[dict[str, Any]], rules: list[list[float]],
                        math_ids: set[str]) -> list[set[str]]:
    """Find short bars with notation above and below, never table-wide rules."""
    from littrans.source_structure import MATH_FONT, inked_glyph
    result = []
    for x0, y0, x1, y1 in rules:
        candidates = [g for g in glyphs if inked_glyph(g)
                      and x0 - .6 <= (g["bbox"][0] + g["bbox"][2]) / 2 <= x1 + .6
                      and abs((g["bbox"][1] + g["bbox"][3]) / 2 - y0) < g["size"] * 1.2
                      and (g["text"].isdigit() or MATH_FONT.search(g["font"])
                           or g["text"] in "+−-=()[]")]
        above = [g for g in candidates if g["bbox"][3] <= y0 + .7]
        below = [g for g in candidates if g["bbox"][1] >= y1 - .7]
        if not above or not below:
            continue
        size = max(g["size"] for g in candidates)
        if x1 - x0 > size * 4:
            continue
        ids = {g["id"] for g in above + below}
        adjacent = [g for g in glyphs if g["id"] in math_ids
                    and 0 <= x0 - g["bbox"][2] <= size
                    and y0 - size <= g.get("baseline", g["bbox"][3]) <= y0 + size]
        if not ids.intersection(math_ids) and not adjacent:
            continue
        result.append(ids | {g["id"] for g in adjacent})
    return result


def possessive_ids(lines: list[list[dict[str, Any]]]) -> set[str]:
    """An upright baseline apostrophe-s after notation is English, not a derivative."""
    from littrans.source_structure import MATH_FONT
    result: set[str] = set()
    for line in lines:
        for index in range(1, len(line) - 1):
            before, quote, suffix = line[index - 1:index + 2]
            following = line[index + 2] if index + 2 < len(line) else None
            if (quote["text"] in {"’", "'"} and suffix["text"] == "s"
                    and not MATH_FONT.search(suffix["font"])
                    and (before["text"] in ")]}" or MATH_FONT.search(before["font"]))
                    and abs(suffix.get("baseline", 0) - before.get("baseline", 0)) < .25 * before["size"]
                    and (following is None or not following["text"].isalpha())):
                result.update((quote["id"], suffix["id"]))
    return result


def consecutive_labels(left: str, right: str) -> bool:
    pattern = r"^[*\s]*\(?([a-z]+|\d+)\)"
    a, b = re.match(pattern, left), re.match(pattern, right)
    if not a or not b:
        return False
    if a[1].isdigit() and b[1].isdigit():
        return int(b[1]) == int(a[1]) + 1
    roman = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    if a[1] in roman and b[1] in roman:
        return roman.index(b[1]) == roman.index(a[1]) + 1
    return len(a[1]) == len(b[1]) == 1 and ord(b[1]) == ord(a[1]) + 1


def absorb_formula_row_labels(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]]) -> None:
    """Recover aligned consecutive row labels only beside wholly mathematical rows."""
    from littrans.fidelity import _union, _visual_lines
    from littrans.source_structure import inked_glyph
    candidates = []
    math = [r for r in regions if r["kind"] == "math" and r.get("display")]
    for line in _visual_lines([g for g in glyphs if inked_glyph(g)]):
        text = "".join(g["text"] for g in line)
        match = re.match(r"\([a-z]+\)", text)
        if not match:
            continue
        label = line[:len(match[0])]
        rest = line[len(label):]
        targets = [r for r in math if rest and all(g["id"] in r.get("glyph_ids", []) for g in rest)]
        if len(targets) == 1 and rest[0]["bbox"][0] - label[-1]["bbox"][2] < label[0]["size"] * 4:
            candidates.append((match[0], label, targets[0]))
    for index, (text, label, region) in enumerate(candidates):
        peers = [other for j, other in enumerate(candidates) if j != index
                 and abs(other[1][0]["bbox"][0] - label[0]["bbox"][0]) < label[0]["size"] * .5
                 and abs(other[1][0]["bbox"][1] - label[0]["bbox"][1]) < label[0]["size"] * 4
                 and (consecutive_labels(text, other[0]) or consecutive_labels(other[0], text))]
        if not peers:
            continue
        ids = set(region.get("glyph_ids", [])) | {g["id"] for g in label}
        region["glyph_ids"] = [g["id"] for g in glyphs if g["id"] in ids]
        region["bbox"] = _union([region["bbox"], *[g["bbox"] for g in label]])
        region["provenance"] = [*region["provenance"], "consecutive-formula-row-labels"]
