"""Source-bound, document-specific preparation observations and handling guidance.

This is an agent-facing profile, never an automatic source approval or override.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

import fitz
from pydantic import BaseModel, ConfigDict, Field

from littrans.extractor import parse_page_spec
from littrans.storage import load_project, read_json, sha256_file, write_json

PROFILE_PATH = Path('context/source-structure.json')


class StructureProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    pages: list[int] = Field(min_length=1)
    status: Literal['draft', 'reviewed'] = 'draft'
    observations: list[dict[str, Any]] = Field(default_factory=list)
    # Open-ended rule categories support books without mathematical statements.
    handling_rules: dict[str, str] = Field(default_factory=dict)
    inspected_pages: list[int] = Field(default_factory=list)
    review_notes: str = ''


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
    if profile.status == 'reviewed' and (not profile.inspected_pages or not profile.handling_rules or not profile.review_notes.strip()):
        raise ValueError('Reviewed structure profile needs inspected pages, rules and review notes')
    return {'path': PROFILE_PATH.as_posix(), 'sha256': sha256_file(path),
            'profile': profile.model_dump(mode='json'),
            'authority': 'Preparation and review guidance only; not source approval. Apply corrections through source import-review.'}


def probe_structure(root: Path, page_spec: str = 'all') -> dict[str, Any]:
    """Inspect native layout without extracting assets or touching source units."""
    root = root.resolve()
    config = load_project(root)
    if sha256_file(config.source(root)) != config.source_sha256:
        raise ValueError('Source PDF changed; rebuild before probing')
    target = root / PROFILE_PATH
    if target.exists():
        raise FileExistsError('Structure profile already exists; review/update it without overwriting prior rules')
    pages = parse_page_spec(page_spec, config.source_pages)
    observations = []
    with fitz.open(config.source(root)) as doc:
        for number in pages:
            page = doc[number - 1]
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
            observations.append({'page': number, 'size': list(page.rect),
                'native_line_count': len(lines), 'raster_count': len(page.get_images()),
                'font_character_counts': dict(fonts.most_common(8)),
                'size_character_counts': dict(sizes.most_common(8)),
                'line_start_counts': dict(starts.most_common(8)),
                # Excerpts are observations, not a fixed vocabulary classifier.
                'line_openings': [line[:160] for line in lines],
                'needs_visual_inspection': True})
    profile = StructureProfile(source_sha256=config.source_sha256, pages=pages,
        observations=observations,
        handling_rules={key: '' for key in (
            'headings', 'containers_and_endings', 'paragraphs_and_displays',
            'page_continuations', 'lists', 'figures_tables_code', 'footnotes_running_material',
            'exceptions_and_fallback')})
    write_json(target, profile.model_dump(mode='json'))
    return {'profile': str(target), 'pages': pages, 'status': 'draft',
            'next': 'Inspect representative original pages, fill document-specific handling rules and evidence notes, then prepare and independently verify source. Probe statistics do not establish boundaries.'}
