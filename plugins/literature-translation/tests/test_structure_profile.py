from pathlib import Path

import pymupdf as fitz
import pytest

from littrans.models import ProjectConfig
from littrans.storage import (
    initialize_project_dirs,
    read_json,
    save_project,
    sha256_file,
    write_json,
)
from littrans.structure_profile import probe_structure, structure_context


def project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    source = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), 'Case study 1. Native prose.')
        doc.save(source)
    save_project(tmp_path, ProjectConfig(project_id='profile', title='Profile',
        source_path='source.pdf', source_sha256=sha256_file(source), source_pages=1,
        profile='technical-book'))
    return tmp_path


def test_probe_is_non_destructive_and_supports_open_ended_rules(tmp_path):
    root = project(tmp_path)
    sentinel = root / 'derived/units.jsonl'
    sentinel.write_text('unchanged', encoding='utf-8')
    result = probe_structure(root, '1')
    assert sentinel.read_text(encoding='utf-8') == 'unchanged'
    path = Path(result['profile'])
    value = read_json(path)
    assert value['status'] == 'draft' and value['inspected_pages'] == []
    assert 'Case study' in value['observations'][0]['line_openings'][0]
    value.update(status='reviewed', inspected_pages=[1], review_notes='Inspected original case study.')
    value['handling_rules'] = {'case_studies': 'Keep the source closing label with its case.'}
    write_json(path, value)
    assert structure_context(root)['profile']['handling_rules'] == value['handling_rules']
    with pytest.raises(FileExistsError):
        probe_structure(root, '1')


def test_changed_pdf_and_empty_review_are_rejected(tmp_path):
    root = project(tmp_path)
    path = Path(probe_structure(root, '1')['profile'])
    value = read_json(path)
    value['status'] = 'reviewed'
    write_json(path, value)
    with pytest.raises(ValueError, match='inspected pages'):
        structure_context(root)
    value['status'] = 'draft'
    value['source_sha256'] = '0' * 64
    write_json(path, value)
    with pytest.raises(ValueError, match='different PDF'):
        structure_context(root)


def test_profile_change_rejects_old_review_packet(tmp_path):
    from littrans.fidelity import _hash, import_source_review
    root = project(tmp_path)
    profile_path = Path(probe_structure(root, '1')['profile'])
    packet = {'source_sha256': sha256_file(root / 'source.pdf'), 'pages': [],
              'document_structure': structure_context(root)}
    packet_id = 'source-' + _hash(packet)[:20]
    packet_path = root / 'packets' / packet_id / 'packet.json'
    write_json(packet_path, packet)
    review = root / 'review.json'
    write_json(review, {'packet_id': packet_id, 'packet_sha256': sha256_file(packet_path),
                        'reviewer': 'test', 'pages': []})
    changed = read_json(profile_path)
    changed['handling_rules']['new_form'] = 'Inspect the new original layout.'
    write_json(profile_path, changed)
    with pytest.raises(ValueError, match='guidance changed'):
        import_source_review(root, review, confirm_visual_review=True)

def test_batch_parent_cannot_be_split_by_intervening_footnote(tmp_path, monkeypatch):
    from littrans.batching import create_batches
    from littrans.fidelity import _make_unit
    from littrans.storage import write_jsonl
    root = project(tmp_path)
    units = [
        _make_unit(1, 'p0001-a', 'word ' * 90, [50, 50, 200, 60], {}, parent_id='case', footnote_refs=['p0001-note']),
        _make_unit(1, 'p0001-note', 'note ' * 90, [50, 500, 200, 510], {}, kind='footnote', footnote_number='1'),
        _make_unit(1, 'p0001-b', 'word ' * 90, [50, 70, 200, 80], {}, parent_id='case'),
        _make_unit(1, 'p0001-c', 'next ' * 90, [50, 90, 200, 100], {}, parent_id='next'),
    ]
    write_jsonl(root / 'derived/units.jsonl', units)
    monkeypatch.setattr('littrans.batching.require_verified_extraction', lambda *args: None)
    monkeypatch.setattr('littrans.batching._context_text', lambda *args: '')
    batches = create_batches(root, '1', max_words=100)
    assert batches[0].unit_ids == [u.unit_id for u in units[:3]]
    assert batches[1].unit_ids == [units[3].unit_id]


def test_probe_extends_reviewed_profile_with_new_pages_only(tmp_path):
    initialize_project_dirs(tmp_path)
    source = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 60), 'Case study 1. Native prose.')
        doc.new_page().insert_text((50, 60), 'Lemma 2. A later form.')
        doc.save(source)
    save_project(tmp_path, ProjectConfig(project_id='profile', title='Profile',
        source_path='source.pdf', source_sha256=sha256_file(source), source_pages=2,
        profile='technical-book'))
    path = Path(probe_structure(tmp_path, '1')['profile'])
    value = read_json(path)
    value.update(status='reviewed', inspected_pages=[1], review_notes='Inspected page 1.')
    value['handling_rules'] = {'case_studies': 'Keep the closing label with its case.'}
    write_json(path, value)
    result = probe_structure(tmp_path, '1-2')
    assert result['new_pages'] == [2] and result['status'] == 'draft'
    extended = read_json(path)
    assert extended['pages'] == [1, 2]
    assert [o['page'] for o in extended['observations']] == [1, 2]
    assert 'Lemma 2' in extended['observations'][1]['line_openings'][0]
    # Reviewed guidance survives; only the status asks for another look.
    assert extended['handling_rules'] == value['handling_rules']
    assert extended['review_notes'] == 'Inspected page 1.' and extended['inspected_pages'] == [1]
    with pytest.raises(FileExistsError):
        probe_structure(tmp_path, '2')
