from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from littrans.models import (
    PROJECT_SCHEMA_VERSION,
    BatchManifest,
    FigureLabel,
    ProjectStatus,
    SourceUnit,
    TranslationRecord,
    UnitKind,
    utc_now,
)
from littrans.project import (
    DEFAULT_REFERENCE_KIND,
    load_reference_terms,
    load_terms,
    translation_map,
)
from littrans.semantics import normalize_zh_caption
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    read_yaml,
    sha256_file,
    sha256_text,
    write_json,
)

TRANSLATION_PAYLOAD_FIELDS = {
    "asset_translations",
    "target_text",
    "target_table",
    "figure_labels",
    "reader_note",
    "term_proposals",
    "uncertainties",
}

SOURCE_SEMANTIC_FIELDS = {
    "kind",
    "page",
    "bbox",
    "source_text",
    "source_hash",
    "asset_content_hashes",
    "source_markdown",
    "parent_id",
    "sidebar_id",
    "sidebar_role",
    "callout_kind",
    "translatable",
    "render_policy",
    "protected_tokens",
    "asset_refs",
    "fragments",
    "latex",
    "equation_number",
    "footnote_number",
    "footnote_refs",
    "math_status",
    "code_language",
    "table",
    "continues_from_previous",
    "continued_to_next",
    "figure_labels",
    "visual_text_status",
    "verification_status",
}

STRUCTURE_FIELDS = {
    "footnote_number",
    "footnote_refs",
    "kind",
    "page",
    "bbox",
    "parent_id",
    "sidebar_id",
    "sidebar_role",
    "callout_kind",
    "translatable",
    "render_policy",
    "asset_refs",
    "fragments",
    "code_language",
    "continues_from_previous",
    "continued_to_next",
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def translation_payload(record: TranslationRecord) -> dict[str, Any]:
    """Return stored translation content, excluding workflow-only metadata."""
    return record.model_dump(
        mode="json", include=TRANSLATION_PAYLOAD_FIELDS, exclude_none=True
    )


def effective_figure_labels(
    unit: SourceUnit, record: TranslationRecord | None
) -> list[FigureLabel]:
    """Return the renderer-visible labels after validating any record override."""
    if record is None or not record.figure_labels:
        return unit.figure_labels
    expected = [label.source for label in unit.figure_labels]
    supplied = [label.source for label in record.figure_labels]
    if unit.kind is not UnitKind.FIGURE or supplied != expected:
        raise ValueError(
            f"Figure label mapping mismatch for {unit.unit_id}; "
            f"expected={expected}, supplied={supplied}"
        )
    return record.figure_labels


def effective_translation_payload(
    unit: SourceUnit, record: TranslationRecord
) -> dict[str, Any]:
    """Return renderer-aware semantic content for one source unit."""
    payload = translation_payload(record)
    try:
        rendered_figure_labels = effective_figure_labels(unit, record)
    except ValueError:
        # Keep malformed legacy records fingerprintable so deterministic QA can
        # report the mapping error instead of failing before it writes a report.
        rendered_figure_labels = record.figure_labels
    payload["figure_labels"] = [
        label.model_dump(mode="json", exclude_none=True)
        for label in rendered_figure_labels
    ]
    if unit.kind is UnitKind.CAPTION and "target_text" in payload:
        payload["target_text"] = normalize_zh_caption(payload["target_text"])
    return payload


def translations_semantically_equal(
    unit: SourceUnit, left: TranslationRecord, right: TranslationRecord
) -> bool:
    return effective_translation_payload(unit, left) == effective_translation_payload(
        unit, right
    )


def source_unit_fingerprint(unit: SourceUnit) -> str:
    return sha256_text(
        _canonical(
            unit.model_dump(
                mode="json", include=SOURCE_SEMANTIC_FIELDS, exclude_none=True
            )
        )
    )


def structure_unit_fingerprint(unit: SourceUnit) -> str:
    return sha256_text(
        _canonical(
            unit.model_dump(mode="json", include=STRUCTURE_FIELDS, exclude_none=True)
        )
    )


def translation_unit_fingerprint(
    unit: SourceUnit, record: TranslationRecord | None
) -> str:
    payload = {
        "source": source_unit_fingerprint(unit),
        "translation": (
            effective_translation_payload(unit, record)
            if record is not None
            else None
        ),
    }
    return sha256_text(_canonical(payload))


def project_units(root: Path) -> list[SourceUnit]:
    return read_jsonl(root / "derived" / "units.jsonl", SourceUnit)


def batch_unit_fingerprints(root: Path, batch_id: str) -> dict[str, str]:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    translations = translation_map(root)
    return {
        unit_id: translation_unit_fingerprint(units[unit_id], translations.get(unit_id))
        for unit_id in manifest.unit_ids
    }


def batch_source_fingerprint(root: Path, batch_id: str) -> str:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    return sha256_text(
        "\n".join(
            f"{unit_id}:{source_unit_fingerprint(units[unit_id])}"
            for unit_id in manifest.unit_ids
        )
    )


def batch_structure_fingerprint(root: Path, batch_id: str) -> str:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    return sha256_text(
        "\n".join(
            f"{unit_id}:{structure_unit_fingerprint(units[unit_id])}"
            for unit_id in manifest.unit_ids
        )
    )


def changed_units(
    current: dict[str, str], previous: dict[str, str]
) -> set[str]:
    return {
        unit_id
        for unit_id in set(current) | set(previous)
        if current.get(unit_id) != previous.get(unit_id)
    }


def continuation_neighbors(units: list[SourceUnit]) -> dict[str, set[str]]:
    """Connect real adjacent reading units, skipping omitted running material.

    A page-edge flag alone never bridges missing PDF pages.
    """
    body = [unit for unit in units if unit.render_policy.value == "include" and unit.kind.value != "footnote"]
    neighbors: dict[str, set[str]] = {}
    for left, right in zip(body, body[1:], strict=False):
        if (0 <= right.page - left.page <= 1
                and (left.continued_to_next or right.continues_from_previous)):
            neighbors.setdefault(left.unit_id, set()).add(right.unit_id)
            neighbors.setdefault(right.unit_id, set()).add(left.unit_id)
    return neighbors


def dependency_closure(
    root: Path,
    batch_ids: Iterable[str],
    changed: Iterable[str],
    *,
    all_units: list[SourceUnit] | None = None,
) -> list[str]:
    """Expand edits to local semantic dependencies and batch seams."""
    from littrans.batching import load_manifest

    units = project_units(root) if all_units is None else all_units
    positions = {unit.unit_id: index for index, unit in enumerate(units)}
    batch_scope = {
        unit_id
        for batch_id in batch_ids
        for unit_id in load_manifest(root, batch_id).unit_ids
    }
    selected = {unit_id for unit_id in changed if unit_id in positions}
    if not selected:
        return []

    # Immediate context is always reviewed for a changed unit.
    for unit_id in list(selected):
        index = positions[unit_id]
        if index and units[index - 1].unit_id in batch_scope:
            selected.add(units[index - 1].unit_id)
        if index + 1 < len(units) and units[index + 1].unit_id in batch_scope:
            selected.add(units[index + 1].unit_id)

    # Continued structures and sidebars may pull one another into the closure.
    while True:
        previous_size = len(selected)
        neighbors = continuation_neighbors(units)
        for unit_id in list(selected):
            selected.update(neighbors.get(unit_id, ()))
        parent_ids = {units[positions[uid]].parent_id for uid in selected if units[positions[uid]].parent_id}
        selected.update(unit.unit_id for unit in units if unit.parent_id in parent_ids)
        sidebar_ids = {
            units[positions[unit_id]].sidebar_id
            for unit_id in selected
            if units[positions[unit_id]].sidebar_id
        }
        selected.update(
            unit.unit_id for unit in units if unit.sidebar_id in sidebar_ids
        )
        for unit in units:
            if unit.unit_id in selected or selected.intersection(unit.footnote_refs):
                selected.add(unit.unit_id)
                selected.update(ref for ref in unit.footnote_refs if ref in positions)
        if len(selected) == previous_size:
            break

    # Cross a batch boundary only through a real semantic dependency (a
    # continuation or shared sidebar), handled by the closure loop above.
    return [unit.unit_id for unit in units if unit.unit_id in selected]


def record_audit_invalidation(
    root: Path, batch_id: str, changed_unit_ids: Iterable[str]
) -> dict[str, list[str]]:
    """Invalidate all lens coverage for the edit dependency closure."""
    closure = set(dependency_closure(root, [batch_id], changed_unit_ids))
    invalidated: dict[str, list[str]] = {}
    timestamp = utc_now()
    batch_root = root / "batches"
    for path in batch_root.iterdir():
        if not path.is_dir() or not (path / "manifest.yaml").is_file():
            continue
        from littrans.batching import load_manifest

        manifest = load_manifest(root, path.name)
        affected = [unit_id for unit_id in manifest.unit_ids if unit_id in closure]
        if not affected:
            continue
        invalidation_path = (
            root / "evidence" / "audits" / f"{manifest.batch_id}.invalidations.json"
        )
        payload = read_json(invalidation_path) if invalidation_path.is_file() else {}
        entries = payload.get("units", {})
        if not isinstance(entries, dict):
            entries = {}
        for unit_id in affected:
            entries[unit_id] = timestamp
        write_json(
            invalidation_path,
            {"batch_id": manifest.batch_id, "units": entries, "updated_at": timestamp},
        )
        invalidated[manifest.batch_id] = affected
    return invalidated


def page_evidence_units(page: int, units: list[SourceUnit]) -> list[SourceUnit]:
    """Return the source units that determine one page's verification receipt."""
    selected_indices = {index for index, unit in enumerate(units) if unit.page == page}
    while True:
        previous_size = len(selected_indices)
        parent_ids = {units[index].parent_id for index in selected_indices if units[index].parent_id}
        selected_indices.update(index for index, unit in enumerate(units) if unit.parent_id in parent_ids)
        sidebar_ids = {
            units[index].sidebar_id
            for index in selected_indices
            if units[index].sidebar_id
        }
        selected_indices.update(
            index for index, unit in enumerate(units) if unit.sidebar_id in sidebar_ids
        )
        selected_ids = {units[index].unit_id for index in selected_indices}
        referenced = {ref for index in selected_indices for ref in units[index].footnote_refs}
        selected_indices.update(index for index, unit in enumerate(units)
                                if unit.unit_id in referenced or selected_ids.intersection(unit.footnote_refs))
        neighbors = continuation_neighbors(units)
        positions = {unit.unit_id: index for index, unit in enumerate(units)}
        for index in list(selected_indices):
            selected_indices.update(positions[uid] for uid in neighbors.get(units[index].unit_id, ()))
        if len(selected_indices) == previous_size:
            break
    return [units[index] for index in sorted(selected_indices)]


def page_evidence_fingerprints(
    root: Path, page: int, units: list[SourceUnit]
) -> tuple[str, str]:
    page_units = page_evidence_units(page, units)
    unit_fingerprint = sha256_text(
        "\n".join(
            f"{unit.unit_id}:{source_unit_fingerprint(unit)}" for unit in page_units
        )
    )
    assets: list[str] = []
    for unit in page_units:
        for asset in unit.asset_refs:
            path = root / asset.path
            digest = sha256_file(path) if path.is_file() else "missing"
            assets.append(f"{asset.path}:{digest}")
    return unit_fingerprint, sha256_text("\n".join(sorted(assets)))


def source_representation_text(unit: SourceUnit) -> str:
    """Return every source representation whose content is translated or reviewed."""
    parts = [unit.source_text]
    if unit.source_markdown and unit.source_markdown != unit.source_text:
        parts.append(unit.source_markdown)
    if unit.table:
        parts.extend(cell for row in unit.table.rows for cell in row)
    parts.extend(label.source for label in unit.figure_labels)
    return "\n".join(part for part in parts if part)


# Characters that mark notation in an equation unit's text: TeX commands and
# delimiters, relation/operator ASCII, digits, Greek, letterlike symbols, arrows, operators.
_NOTATION_CHAR = re.compile(r"[\\$^_=<>|+*/{}\dͰ-Ͽ℀-⅏←-⋿⟀-⟯]")


def equation_is_notation(unit: SourceUnit) -> bool:
    """Whether an equation unit without asset placeholders carries mathematical notation.

    Preserved sources never import LaTeX, so a displayed line the preparation kept as
    native text (an upright ``Prob`` operator, an ``otherwise.`` case label) has plain
    words as its text; typesetting those as a symbol sequence spaces and slants them.
    """
    if unit.latex:
        return True
    return _NOTATION_CHAR.search(unit.source_text) is not None


def equation_markdown(unit: SourceUnit, text: str | None = None) -> str:
    """Return the exact display-math representation emitted in formal Markdown.

    ``text`` substitutes the translation of a native-text display line; notation
    always renders from the source (``latex`` or the preserved text).
    """
    if unit.kind is not UnitKind.EQUATION:
        raise ValueError(f"Unit is not an equation: {unit.unit_id}")
    if not equation_is_notation(unit):
        body = text if text is not None else unit.source_text
        number = f" ({unit.equation_number})" if unit.equation_number and f"({unit.equation_number})" not in body else ""
        return body + number
    number = f" \\tag{{{unit.equation_number}}}" if unit.equation_number else ""
    return f"$$\n{unit.latex or unit.source_text}{number}\n$$"


# PDF text extraction often prints a TeX accent as a spacing character before its
# base letter ("L´evy", "H¨older"); NFKD alone would keep it as a stray space.
_SPACING_ACCENTS = str.maketrans(
    "", "", "´¨ˆ˜¯˘˙˚˝¸ˇ"
)
_PUNCTUATION_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ",
})


def fold_term_text(text: str) -> str:
    """Fold source text and glossary sources to one comparable form.

    Accents (precomposed, combining or TeX spacing marks), ligatures, curly quotes,
    dash variants, whitespace runs and case are all normalized so that a glossary
    source such as ``Hölder`` still gates the extracted ``H¨older``.
    """
    text = text.translate(_SPACING_ACCENTS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.translate(_PUNCTUATION_FOLD)
    return re.sub(r"\s+", " ", text).casefold()


_REGEX_ESCAPE = re.compile(r"\\(?:N\{[^}]*\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-9]{1,3}|.)", re.S)
_REGEX_GROUP_HEADER = re.compile(r"\(\?(?:P<[^>]+>|P=[^)]+\)|\([^)]+\)|[aiLmsux]*(?:-[imsx]+)?[:)])")
_REGEX_FLAGS = re.compile(r"\(\?([aiLmsux]*)(?:-([imsx]+))?([:)])")


@lru_cache(maxsize=512)
def fold_regex_pattern(pattern: str) -> str:
    r"""Fold literals while preserving Python regex syntax and group identifiers.

    Escapes are opaque, including complete Unicode escapes. Group headers and
    comments are syntax, not terminology. Verbose mode is tracked per group so a
    comment's terminating newline cannot disappear during whitespace folding.
    Validate first: normalization must never repair an invalid user pattern.
    """
    re.compile(pattern, re.I)
    parts: list[str] = []
    verbose = [False]
    in_class = False
    class_start = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            escape = _REGEX_ESCAPE.match(pattern, index)
            assert escape is not None  # the original pattern compiled above
            parts.append(escape[0])
            index = escape.end()
            continue
        if in_class:
            if char == "]" and index != class_start:
                in_class = False
                parts.append(char)
            elif char == "^" and index == class_start - 1:
                # Only an original leading caret negates the class. Dropping an
                # accent before a literal caret must not turn it into negation.
                parts.append(char)
            elif char == "-" and index != class_start and pattern[index + 1:index + 2] != "]":
                parts.append(char)  # an original range operator
            else:
                # Folding a fullwidth bracket or a dash must not introduce syntax.
                parts.append(re.escape(fold_term_text(char)))
            index += 1
            continue
        if verbose[-1] and char == "#":
            end = pattern.find("\n", index)
            end = len(pattern) if end < 0 else end + 1
            parts.append(pattern[index:end])
            index = end
            continue
        if pattern.startswith("(?#", index):
            comment = re.match(r"\(\?\#(?:\\.|[^\\)])*\)", pattern[index:], re.S)
            assert comment is not None
            parts.append(comment[0])
            index += comment.end()
            continue
        if char == "(":
            header = _REGEX_GROUP_HEADER.match(pattern, index)
            if header:
                token = header[0]
                flags = _REGEX_FLAGS.fullmatch(token)
                if flags:
                    enabled = (verbose[-1] or "x" in flags[1]) and "x" not in (flags[2] or "")
                    if flags[3] == ")":
                        verbose[-1] = enabled
                    else:
                        verbose.append(enabled)
                elif not token.startswith("(?P="):
                    verbose.append(verbose[-1])
                parts.append(token)
                index = header.end()
                continue
            verbose.append(verbose[-1])
        elif char == ")":
            verbose.pop()
        elif char == "[":
            in_class = True
            class_start = index + 1
            if pattern[class_start:class_start + 1] == "^":
                class_start += 1
        elif char == "{":
            repeat = re.match(r"\{[0-9]*(?:,[0-9]*)?\}", pattern[index:])
            if repeat:
                parts.append(repeat[0])
                index += repeat.end()
                continue
        if char in ".^$*+?[]()|":
            parts.append(char)
        elif char.isspace():
            end = index + 1
            while end < len(pattern) and pattern[end].isspace():
                end += 1
            parts.append(pattern[index:end] if verbose[-1] else " ")
            index = end
            continue
        else:
            folded = fold_term_text(char)
            # Keep ordinary spaces readable; re.escape protects any new operators
            # and a normalized '#' when the group uses verbose mode.
            parts.append(re.escape(folded).replace(r"\ ", " " if not verbose[-1] else r"\ "))
        index += 1
    result = "".join(parts)
    re.compile(result, re.I)
    return result


# Words a title leaves in lower case; every other word of a quoted title is capitalised.
TITLE_MINOR_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or over the to via vs with".split()
)


def _title_like(phrase: str) -> bool:
    words = re.findall(r"[^\W\d_][\w'’-]*", phrase)
    return (len(words) >= 2 and words[0][0].isupper()
            and all(word[0].isupper() for word in words[1:] if word.casefold() not in TITLE_MINOR_WORDS))


def without_quoted_titles(text: str, every_quote: bool = False) -> str:
    """Drop quoted titles: cited work names are not translated terminology.

    Quotation marks also set off a term or a phrase (“strict mode”, “discrete Itô
    formula”), which is exactly where terminology matters, so only a phrase set as a
    title (``“Binding Theory”``) is dropped; ``every_quote`` drops every quotation, for
    a bibliography entry whose quotes are cited titles in whatever case.
    """
    return re.sub(r'["“]([^"”]{2,})["”]',
                  lambda match: " " if every_quote or _title_like(match[1]) else match[0], text)


def term_source_text(unit: SourceUnit) -> str:
    """The folded source representation every terminology check matches against."""
    text = source_representation_text(unit)
    return fold_term_text(without_quoted_titles(text, every_quote=unit.kind is UnitKind.BIBLIOGRAPHY))


def term_source_forms(term: dict[str, Any]) -> list[str]:
    """The source forms an entry is located by: ``source`` plus any ``aliases``."""
    forms = [str(term.get("source", "")).strip()]
    forms.extend(str(alias).strip() for alias in (term.get("aliases") or []))
    return [form for form in forms if form]


def term_matches(term: dict[str, Any], folded_source: str) -> bool:
    """Whether a glossary entry's source (or one of its aliases) occurs in folded source text."""
    mode = str(term.get("match", "substring"))
    for source_term in term_source_forms(term):
        if mode == "regex":
            if re.search(fold_regex_pattern(source_term), folded_source, re.I) is not None:
                return True
            continue
        folded_term = fold_term_text(source_term)
        if mode == "word":
            if re.search(r"(?<!\w)" + re.escape(folded_term) + r"(?!\w)", folded_source) is not None:
                return True
        elif folded_term in folded_source:
            return True
    return False


def select_relevant(terms: Iterable[dict[str, Any]], units: Iterable[SourceUnit]) -> list[dict[str, Any]]:
    """The entries whose scope covers the units and whose source occurs in them, in order."""
    selected = list(units)
    source = "\n".join(term_source_text(unit) for unit in selected)
    pages = {unit.page for unit in selected}
    parents = {unit.parent_id for unit in selected if unit.parent_id}
    matches: list[dict[str, Any]] = []
    for term in terms:
        scope = str(term.get("scope", "document"))
        in_scope = (
            scope == "document"
            or scope in parents
            or any(scope == f"page:{page}" for page in pages)
        )
        if in_scope and term_matches(term, source):
            matches.append(term)
    return matches


def relevant_terms(root: Path, units: Iterable[SourceUnit]) -> list[dict[str, Any]]:
    """The gated (approved) entries a set of units needs."""
    return select_relevant(load_terms(root), units)


def relevant_reference_terms(root: Path, units: Iterable[SourceUnit]) -> list[dict[str, Any]]:
    """The binding-but-not-gated entries a set of units needs, filtered like approved terms."""
    return select_relevant(load_reference_terms(root), units)


def reference_terms_by_kind(terms: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group reference entries by ``kind`` in first-seen order, dropping the key from each entry."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        kind = str(term.get("kind") or DEFAULT_REFERENCE_KIND)
        grouped.setdefault(kind, []).append({k: v for k, v in term.items() if k != "kind"})
    return grouped


def reference_terms_yaml(terms: Iterable[dict[str, Any]]) -> str:
    """The YAML block packets show for reference entries; empty when nothing is relevant."""
    grouped = reference_terms_by_kind(terms)
    if not grouped:
        return ""
    return str(yaml.safe_dump({"reference_terms": grouped}, allow_unicode=True, sort_keys=False))


AUDIT_CONTEXT_PARTS = ("document-brief", "style-guide", "approved-terms", "reference-terms")


def audit_context_sections(root: Path, units: Iterable[SourceUnit]) -> dict[str, str]:
    """The shared context by part: the two context files whole, the terms filtered per unit."""
    selected = list(units)
    brief = (root / "context" / "document-brief.md").read_text(
        encoding="utf-8"
    ).strip()
    style = (root / "context" / "style-guide.md").read_text(
        encoding="utf-8"
    ).strip()
    terms = yaml.safe_dump(
        {"approved_terms": relevant_terms(root, selected)},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    reference = reference_terms_yaml(relevant_reference_terms(root, selected)).strip()
    return {"document-brief": brief, "style-guide": style, "approved-terms": terms, "reference-terms": reference}


def audit_context_text(root: Path, units: Iterable[SourceUnit]) -> str:
    """Return the exact shared instructions and terminology shown to auditors.

    The reference section is present only when an entry matches the units, so a project
    without reference entries keeps the audit context it had before the channel existed.
    """
    return _render_audit_context(audit_context_sections(root, units))


def _render_audit_context(parts: dict[str, str]) -> str:
    text = (
        f"# Document brief\n\n{parts['document-brief']}\n\n# Translation style\n\n{parts['style-guide']}\n\n"
        f"# Relevant approved terminology\n\n```yaml\n{parts['approved-terms']}\n```\n"
    )
    if parts["reference-terms"]:
        text += f"\n# Relevant reference terminology (not gated)\n\n```yaml\n{parts['reference-terms']}\n```\n"
    return text


def _summarize_audit_context(parts: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        part: {"sha256": sha256_text(text), "lines": len(text.splitlines()) if text else 0}
        for part, text in parts.items()
    }


def audit_context_parts(root: Path, units: Iterable[SourceUnit]) -> dict[str, dict[str, Any]]:
    """Per-part hash and size of the shared context, so staleness can name what grew."""
    return _summarize_audit_context(audit_context_sections(root, units))


def audit_context_fingerprint(root: Path, units: Iterable[SourceUnit]) -> str:
    return sha256_text(audit_context_text(root, units))


def audit_context_fingerprint_and_parts(
    root: Path, units: Iterable[SourceUnit]
) -> tuple[str, dict[str, dict[str, Any]]]:
    """``audit_context_fingerprint`` and ``audit_context_parts`` from one context build."""
    parts = audit_context_sections(root, units)
    return sha256_text(_render_audit_context(parts)), _summarize_audit_context(parts)


def translation_memory(
    root: Path, current_unit_ids: Iterable[str], limit: int = 6
) -> list[dict[str, str]]:
    if limit <= 0:
        return []
    current_ids = set(current_unit_ids)
    config = load_project(root)
    if config.schema_version != PROJECT_SCHEMA_VERSION:
        return []
    config_external = config.external_review
    units_path = root / "derived" / "units.jsonl"
    translations_path = root / "translations" / "current.jsonl"
    unit_stat = units_path.stat()
    translation_stat = translations_path.stat() if translations_path.is_file() else None
    units, candidates = _memory_index(
        str(root.resolve()),
        unit_stat.st_mtime_ns,
        unit_stat.st_size,
        translation_stat.st_mtime_ns if translation_stat else 0,
        translation_stat.st_size if translation_stat else 0,
        bool(config_external and config_external.enabled),
    )
    positions = {unit_id: index for index, (unit_id, _, _) in enumerate(units)}
    source_by_id = {unit_id: source for unit_id, source, _ in units}
    current_source = " ".join(
        source_by_id[unit_id] for unit_id in current_ids if unit_id in source_by_id
    )
    current_tokens = _memory_tokens(current_source)
    adjacent_ids: set[str] = set()
    for unit_id in current_ids:
        index = positions.get(unit_id)
        if index is None:
            continue
        if index:
            adjacent_ids.add(units[index - 1][0])
        if index + 1 < len(units):
            adjacent_ids.add(units[index + 1][0])

    ranked: list[tuple[float, int, str, str, str]] = []
    for unit_id, source, target, tokens in candidates:
        if unit_id in current_ids:
            continue
        adjacent = 1 if unit_id in adjacent_ids else 0
        candidate_tokens = set(tokens)
        union = current_tokens | candidate_tokens
        similarity = len(current_tokens & candidate_tokens) / len(union) if union else 0.0
        ranked.append((similarity + adjacent * 2.0, adjacent, unit_id, source, target))
    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    manifest_paths = sorted((root / "batches").glob("*/manifest.yaml"))
    manifest_state = _memory_state_token(manifest_paths)
    manifest_index = dict(
        _memory_manifest_index(str(root.resolve()), manifest_state)
    )
    shared_state = sha256_text(
        manifest_state
        + "|"
        + _memory_state_token(
            [
                root / "project.yaml",
                units_path,
                translations_path,
                root / "glossary" / "approved.yaml",
                root / "glossary" / "reference.yaml",
                root / "context" / "document-brief.md",
                root / "context" / "style-guide.md",
            ]
        )
    )
    evidence_state = _memory_state_token(
        path
        for batch_id in sorted({bid for ids in manifest_index.values() for bid in ids})
        for path in _memory_batch_evidence_paths(root, batch_id)
    )
    complete_batch_ids = _memory_complete_batch_ids(
        str(root.resolve()), shared_state, evidence_state
    )
    memories: list[dict[str, str]] = []
    for _, _, unit_id, source, target in ranked:
        batch_ids = manifest_index.get(unit_id, ())
        for batch_id in batch_ids:
            if batch_id in complete_batch_ids:
                memories.append(
                    {"unit_id": unit_id, "source": source, "target": target}
                )
                break
        if len(memories) >= limit:
            break
    return memories


def _memory_state_token(paths: Iterable[Path]) -> str:
    states: list[str] = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            states.append(f"{path}:missing")
        else:
            states.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return sha256_text("\n".join(states))


def _memory_batch_evidence_paths(root: Path, batch_id: str) -> list[Path]:
    return [
        root / "batches" / batch_id / "manifest.yaml",
        root / "qa" / f"{batch_id}.json",
        root / "reviews" / f"{batch_id}.issues.jsonl",
        root / "reviews" / f"{batch_id}.external-runs.jsonl",
        root / "evidence" / "audits" / f"{batch_id}.jsonl",
        root / "evidence" / "audits" / f"{batch_id}.invalidations.json",
    ]


@lru_cache(maxsize=8)
def _memory_manifest_index(
    root_text: str, manifest_state: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    del manifest_state
    root = Path(root_text)
    memberships: dict[str, list[str]] = {}
    for path in sorted((root / "batches").glob("*/manifest.yaml")):
        manifest = BatchManifest.model_validate(read_yaml(path))
        for unit_id in manifest.unit_ids:
            memberships.setdefault(unit_id, []).append(manifest.batch_id)
    return tuple(
        (unit_id, tuple(batch_ids))
        for unit_id, batch_ids in sorted(memberships.items())
    )


@lru_cache(maxsize=8)
def _memory_complete_batch_ids(
    root_text: str, shared_state: str, evidence_state: str
) -> frozenset[str]:
    """Resolve complete batch IDs from one current, evidence-keyed snapshot."""
    del shared_state, evidence_state
    from littrans.workflow import _batch_stage, _load_workflow_snapshot

    root = Path(root_text)
    try:
        snapshot = _load_workflow_snapshot(root)
    except (KeyError, OSError, ValueError):
        return frozenset()
    context_cache: dict[tuple[str, ...], tuple[str, dict[str, str]] | None] = {}
    complete: set[str] = set()
    for manifest in snapshot.manifests:
        try:
            if _batch_stage(root, manifest.batch_id, snapshot, context_cache) == "complete":
                complete.add(manifest.batch_id)
        except (KeyError, OSError, ValueError):
            continue
    return frozenset(complete)


def _memory_tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[\u3400-\u9fff]{2,}", text.casefold()))


@lru_cache(maxsize=8)
def _memory_index(
    root_text: str,
    units_mtime_ns: int,
    units_size: int,
    translations_mtime_ns: int,
    translations_size: int,
    external_enabled: bool,
) -> tuple[
    tuple[tuple[str, str, str], ...],
    tuple[tuple[str, str, str, tuple[str, ...]], ...],
]:
    del units_mtime_ns, units_size, translations_mtime_ns, translations_size
    root = Path(root_text)
    units = project_units(root)
    translations = translation_map(root)
    unit_entries = tuple(
        (unit.unit_id, unit.source_text, unit.source_hash) for unit in units
    )
    unit_map = {unit.unit_id: unit for unit in units}
    allowed = (
        {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
        if external_enabled
        else {
            ProjectStatus.MACHINE_REVIEWED,
            ProjectStatus.EXTERNAL_REVIEWED,
            ProjectStatus.HUMAN_APPROVED,
        }
    )
    candidates = tuple(
        (
            unit_id,
            unit_map[unit_id].source_text,
            record.target_text,
            tuple(sorted(_memory_tokens(unit_map[unit_id].source_text))),
        )
        for unit_id, record in translations.items()
        if unit_id in unit_map
        and record.status in allowed
        and record.source_hash == unit_map[unit_id].source_hash
    )
    return unit_entries, candidates
