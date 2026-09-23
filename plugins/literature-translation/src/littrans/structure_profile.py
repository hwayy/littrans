"""Source-bound, document-specific preparation observations and handling guidance.

This is an agent-facing profile, never an automatic source approval or override.

A review packet embeds the profile, and a page receipt binds the guidance that applied to
its page: the base ``handling_rules`` followed by every ``page_rules`` block covering that
page, read per key. Extending the profile for a new chapter (new observations, pages, a
scoped block) leaves earlier pages' guidance — and their receipts — untouched; editing a
base rule changes every page's guidance. Digests are computed from the profile's content,
never from the file's bytes, so line endings and formatting are not guidance.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pymupdf as fitz
from pydantic import BaseModel, ConfigDict, Field

from littrans.extractor import parse_page_spec
from littrans.storage import (
    load_project,
    project_write_lock,
    read_json,
    sha256_file,
    sha256_text,
    write_json,
)

PROFILE_PATH = Path('context/source-structure.json')
AUTHORITY = 'Preparation and review guidance only; not source approval. Apply corrections through source import-review.'


class PageRules(BaseModel):
    """Handling rules that hold only for some of the profile's pages (a chapter, an appendix)."""

    model_config = ConfigDict(extra='forbid')
    label: str = ''  # Descriptive only; never part of the guidance.
    pages: str  # A page spec inside the profile scope ("52-67", "52,54-60"); never "all".
    handling_rules: dict[str, str] = Field(min_length=1)


class StructureProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    pages: list[int] = Field(min_length=1)
    status: Literal['draft', 'reviewed'] = 'draft'
    observations: list[dict[str, Any]] = Field(default_factory=list)
    # Open-ended rule categories support books without mathematical statements.
    handling_rules: dict[str, str] = Field(default_factory=dict)
    # Rules scoped to a page range; the base rules stay in force for every page.
    page_rules: list[PageRules] = Field(default_factory=list)
    inspected_pages: list[int] = Field(default_factory=list)
    review_notes: str = ''


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def profile_digest(profile: Mapping[str, Any]) -> str:
    """Content digest of a profile dump: formatting and line endings do not change it."""
    return sha256_text(_canonical(dict(profile)))


def _block_pages(block: Mapping[str, Any], profile: Mapping[str, Any]) -> set[int]:
    return set(parse_page_spec(str(block['pages']), max(int(p) for p in profile['pages'])))


def page_guidance(profile: Mapping[str, Any], page: int) -> dict[str, str]:
    """The handling rules a reviewer of ``page`` reads, per key.

    The base rule comes first and every covering ``page_rules`` block follows it in profile
    order, joined by a line break: a base rule split at a line boundary into a scoped block
    reads the same. Blank texts are skipped and keys without text are absent, so an empty
    placeholder category is no guidance. A profile without ``page_rules`` (a packet written
    before scoped blocks existed) is read as having none.
    """
    texts: dict[str, list[str]] = {}
    scopes: list[Mapping[str, Any]] = [profile.get('handling_rules', {})]
    for block in profile.get('page_rules', []) or []:
        if page in _block_pages(block, profile):
            scopes.append(block.get('handling_rules', {}))
    for rules in scopes:
        for key, text in rules.items():
            if str(text).strip():
                texts.setdefault(str(key), []).append(str(text))
    return {key: '\n'.join(parts) for key, parts in texts.items()}


def guidance_digest(profile: Mapping[str, Any], page: int) -> str:
    return sha256_text(_canonical(page_guidance(profile, page)))


def guidance_difference(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None, page: int) -> str | None:
    """How the guidance for ``page`` differs between two ``document_structure`` contexts.

    ``None`` when a reviewer of that page would read the same rules; otherwise a phrase
    naming the page and what changed, for an error message.
    """
    if before is None and after is None:
        return None
    if before is None:
        return f'for page {page} (profile added)'
    if after is None:
        return f'for page {page} (profile removed)'
    old = page_guidance(before['profile'], page)
    new = page_guidance(after['profile'], page)
    changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    if not changed:
        return None
    return f'for page {page} (handling_rules: {", ".join(changed)})'


def structure_context(root: Path) -> dict[str, Any] | None:
    """Load immutable-in-packet guidance; reject a profile for another source."""
    path = root / PROFILE_PATH
    if not path.exists():
        return None  # Existing projects remain compatible.
    profile = StructureProfile.model_validate(read_json(path))
    config = load_project(root)
    if (profile.source_sha256 != config.source_sha256
            or sha256_file(config.source(root)) != profile.source_sha256):
        raise ValueError('Source structure profile belongs to a changed or different PDF')
    if len(set(profile.pages)) != len(profile.pages) or any(p < 1 or p > config.source_pages for p in profile.pages):
        raise ValueError('Invalid source structure page scope')
    if not set(profile.inspected_pages) <= set(profile.pages):
        raise ValueError('Inspected pages must belong to the profile scope')
    for block in profile.page_rules:
        try:
            scoped = set(parse_page_spec(block.pages, config.source_pages)) if block.pages.strip().lower() != 'all' else None
        except ValueError:
            scoped = None
        if not scoped or not scoped <= set(profile.pages):
            raise ValueError('page_rules block pages must be a page spec inside the profile scope')
    if profile.status == 'reviewed' and (not profile.inspected_pages or not profile.handling_rules or not profile.review_notes.strip()):
        raise ValueError('Reviewed structure profile needs inspected pages, rules and review notes')
    dump = profile.model_dump(mode='json')
    return {'path': PROFILE_PATH.as_posix(), 'sha256': profile_digest(dump), 'profile': dump, 'authority': AUTHORITY}


def rescope_rules(root: Path, packet_id: str, page_spec: str, label: str = '', *, apply: bool = True) -> dict[str, Any]:
    """Move what was appended to the base rules since ``packet_id`` into a block for ``page_spec``.

    A profile whose base rules were extended for a later scope binds the earlier pages'
    receipts to the shorter text and the later pages' receipts to the longer. Restoring
    each base rule to the text the packet embeds and scoping the appended lines to the
    later pages reads the same for every page: the earlier receipts verify against the
    base text, the later ones against base plus block. A rule the packet does not know,
    or whose current text is not the packet's text extended after a line break, is
    refused rather than guessed at; a rule that did not grow needs no block entry.
    """
    root = root.resolve()
    if not apply:
        return _rescope_rules(root, packet_id, page_spec, label, apply=False)
    # The profile is read and rewritten under one lock, so a concurrent probe is not lost.
    with project_write_lock(root):
        return _rescope_rules(root, packet_id, page_spec, label, apply=True)


def _rescope_rules(root: Path, packet_id: str, page_spec: str, label: str, *, apply: bool) -> dict[str, Any]:
    from littrans.fidelity import _load_source_packet

    config = load_project(root)
    packet_path = root / 'packets' / packet_id / 'packet.json'
    packet = _load_source_packet(root, packet_id, sha256_file(packet_path))
    embedded = packet.get('document_structure')
    if not embedded:
        raise ValueError(f'source packet {packet_id} embeds no structure profile')
    target = root / PROFILE_PATH
    profile = StructureProfile.model_validate(read_json(target))
    if profile.source_sha256 != embedded['profile'].get('source_sha256'):
        raise ValueError('the packet belongs to another source PDF')
    pages = parse_page_spec(page_spec, config.source_pages)
    if page_spec.strip().lower() == 'all' or not set(pages) <= set(profile.pages):
        raise ValueError('page_rules block pages must be a page spec inside the profile scope')
    if set(pages) & {int(p['page']) for p in packet.get('pages', [])}:
        raise ValueError('the block pages must not include pages the packet reviews')
    base: dict[str, str] = dict(embedded['profile'].get('handling_rules', {}))
    block: dict[str, str] = {}
    unchanged: list[str] = []
    for key, text in profile.handling_rules.items():
        if key not in base:
            raise ValueError(f'handling rule {key!r} is not in the packet; add it as a page_rules block by hand')
        if text == base[key]:
            unchanged.append(key)
            continue
        if not text.startswith(base[key] + '\n'):
            raise ValueError(f'handling rule {key!r} was rewritten, not extended after a line break; restore it by hand')
        block[key] = text[len(base[key]) + 1:]
    missing = sorted(set(base) - set(profile.handling_rules))
    if missing:
        raise ValueError(f'handling rules the packet knows are gone from the profile: {", ".join(missing)}')
    if not block:
        raise ValueError('no base rule was extended since the packet; nothing to scope')
    entry = PageRules(label=label, pages=page_spec.strip(), handling_rules=block)
    updated = profile.model_copy(update={'handling_rules': base, 'page_rules': [*profile.page_rules, entry]})
    result = {'profile': str(target), 'packet_id': packet_id, 'pages': pages, 'scoped_rules': sorted(block),
              'unchanged_rules': unchanged, 'page_rules': len(updated.page_rules), 'applied': apply}
    if apply:
        write_json(target, updated.model_dump(mode='json'))
    return result


def _observe(page: fitz.Page, number: int) -> dict[str, Any]:
    fonts: Counter[str] = Counter()
    sizes: Counter[float] = Counter()
    starts: Counter[float] = Counter()
    lines = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                starts[round(line['bbox'][0], 1)] += 1
                lines.append(text)
            for span in line['spans']:
                fonts[span['font']] += len(span['text'])
                sizes[round(span['size'], 1)] += len(span['text'])
    return {'page': number, 'size': list(page.rect),
            'native_line_count': len(lines), 'raster_count': len(page.get_images()),
            'font_character_counts': dict(fonts.most_common(8)),
            'size_character_counts': dict(sizes.most_common(8)),
            'line_start_counts': dict(starts.most_common(8)),
            # Excerpts are observations, not a fixed vocabulary classifier.
            'line_openings': [line[:160] for line in lines],
            'needs_visual_inspection': True}


def probe_structure(root: Path, page_spec: str = 'all') -> dict[str, Any]:
    """Inspect native layout without extracting assets or touching source units.

    An existing profile is extended, never overwritten: observations for pages not
    yet probed are appended, the reviewed rules and notes stay, and the status
    returns to draft until the rules have been checked against the new pages. Rules
    that hold only for the new pages belong in a ``page_rules`` block; the base rules
    are every page's guidance, and editing one voids every source receipt.
    """
    root = root.resolve()
    with project_write_lock(root):
        return _probe_structure_locked(root, page_spec)


def _probe_structure_locked(root: Path, page_spec: str) -> dict[str, Any]:
    config = load_project(root)
    if sha256_file(config.source(root)) != config.source_sha256:
        raise ValueError('Source PDF changed; rebuild before probing')
    target = root / PROFILE_PATH
    pages = parse_page_spec(page_spec, config.source_pages)
    existing = StructureProfile.model_validate(read_json(target)) if target.exists() else None
    if existing is not None and existing.source_sha256 != config.source_sha256:
        raise ValueError('Source structure profile belongs to a changed or different PDF')
    observed = {o.get('page') for o in existing.observations} if existing else set()
    new_pages = [number for number in pages if number not in observed]
    if existing is not None and not new_pages:
        raise FileExistsError('Structure profile already covers these pages; review/update it without overwriting prior rules')
    observations = []
    with fitz.open(config.source(root)) as doc:
        for number in new_pages:
            observations.append(_observe(doc[number - 1], number))
    if existing is None:
        profile = StructureProfile(source_sha256=config.source_sha256, pages=pages,
            observations=observations,
            handling_rules={key: '' for key in (
                'headings', 'containers_and_endings', 'paragraphs_and_displays',
                'page_continuations', 'lists', 'figures_tables_code', 'footnotes_running_material',
                'exceptions_and_fallback')})
        next_step = ('Inspect representative original pages, fill document-specific handling rules and evidence notes, '
                     'then prepare and independently verify source. Probe statistics do not establish boundaries. '
                     'Rules that will hold only for a later scope (another chapter) go in a page_rules block when that scope is probed.')
    else:
        profile = existing.model_copy(update={
            'pages': sorted(set(existing.pages) | set(pages)),
            'observations': existing.observations + observations,
            'status': 'draft'})
        next_step = (f'Inspect the original renders of pages {new_pages}. Put rules that hold only for these pages in a '
                     f'page_rules block ({{"pages": "{page_spec}", "handling_rules": {{...}}}}); editing a base handling_rules '
                     'entry changes the guidance of every page and voids every existing source receipt. Extend the review '
                     'notes, add the pages to inspected_pages and set status back to reviewed before preparing.')
    write_json(target, profile.model_dump(mode='json'))
    return {'profile': str(target), 'pages': pages, 'new_pages': new_pages, 'status': profile.status, 'next': next_step}
