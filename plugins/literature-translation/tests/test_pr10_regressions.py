from pathlib import Path

import pytest
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as asset_project

from littrans.fidelity_models import load_assets
from littrans.representations import (
    build_asset_packet,
    import_asset_review,
    representation_status,
    resolve_asset_markdown,
    submit_candidates,
)
from littrans.storage import read_json, write_json, write_jsonl

project = asset_project


@pytest.mark.parametrize('kind', ['figure', 'mixed-region'])
def test_unrepresentable_assets_stay_original(project: Path, kind: str) -> None:
    assets = load_assets(project)
    assets['a1'].kind = kind
    write_jsonl(project / 'derived/fidelity-assets.jsonl', assets.values())
    with pytest.raises(ValueError, match='representable'):
        build_asset_packet(project, ['a1'])
    assert representation_status(project)['assets']['a1']['state'] == 'fallback'


def test_wrong_format_rejected_before_publication(project: Path) -> None:
    payload = candidate_input(project)
    payload['candidates'][0].update(format='code', content='x = 1')
    write_json(project / 'candidate.json', payload)
    with pytest.raises(ValueError, match='format'):
        submit_candidates(project, project / 'candidate.json')
    assert not (project / 'evidence/representations/index.json').exists()


@pytest.mark.parametrize('dependency', ['runtime', 'svg', 'png', 'pdf'])
@pytest.mark.parametrize('after_accept', [False, True])
def test_review_binds_copied_dependencies(project: Path, dependency: str, after_accept: bool) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    payload = review_input(project)
    packet = read_json(project / 'evidence/representations/packets' / (payload['packet_id'] + '.json'))
    if packet.get('render_manifest'):
        payload['render_manifest_sha256'] = packet['render_manifest_sha256']
        write_json(project / 'review.json', payload)
    if after_accept:
        import_asset_review(project, project / 'review.json', True)
    folder = (project / packet['render_artifact']['path']).parent
    path = next((folder / 'mathjax').rglob('*.js')) if dependency == 'runtime' else next((folder / 'original-assets').glob('*.' + dependency))
    path.write_bytes(b'changed local dependency')
    if after_accept:
        assert representation_status(project)['assets']['a1']['state'] == 'asset-audit'
        assert '已核验' not in resolve_asset_markdown(project, '{{asset:a1}}', project / 'output')
    else:
        with pytest.raises(ValueError, match='artifact|manifest|dependency'):
            import_asset_review(project, project / 'review.json', True)


def test_rebuilt_source_is_portable(project: Path, tmp_path: Path) -> None:
    from littrans.project import rebuild_project
    from littrans.storage import load_project
    rebuilt = tmp_path / 'rebuilt'
    config = rebuild_project(project, rebuilt)
    assert not Path(config.source_path).is_absolute()
    assert config.source(rebuilt).is_relative_to(rebuilt)
    project.rename(tmp_path / 'old-unavailable')
    moved = tmp_path / 'moved'
    rebuilt.rename(moved)
    assert load_project(moved).source(moved).is_file()
    assert read_json(moved / 'derived/provenance.json')['source_is_copied']


def test_managed_layout_requires_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from littrans import layout_runtime as runtime
    python = tmp_path / 'venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    model = tmp_path / runtime.MODEL_NAME
    model.mkdir()
    (model / 'config.json').write_text('{}')
    (model / 'model.safetensors').touch()
    monkeypatch.setattr(runtime, 'layout_cache_root', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'runtime_paths', lambda: (python, model))
    monkeypatch.setattr(runtime, '_run', lambda *args: subprocess.CompletedProcess([], 0, runtime.MINERU_VERSION, ''))
    assert not runtime.layout_runtime_status()['ok']
    (tmp_path / 'venv' / runtime.READY_MARKER).write_text('ready\n')
    assert runtime.layout_runtime_status()['ok']


@pytest.mark.parametrize('number', ['1', '10', '12'])
def test_multidigit_footnote_glyphs(number: str) -> None:
    import pymupdf as fitz

    from littrans.fidelity import _native
    from littrans.source_structure import plan_structure
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 200), 'Prose with a note', fontsize=12)
        page.insert_text((160, 196), number, fontsize=7)
        page.insert_text((50, 700), number + 'Note text.', fontsize=9)
        glyphs, blocks = _native(page)
        plan = plan_structure(glyphs, blocks, [{'label': 'footnote', 'bbox': [90, 1370, 400, 1420]}], page.rect.height)
        calls = [m for m in plan['markers'].values() if not m['definition']]
        assert len(calls) == len(number)
        assert {m['number'] for m in calls} == {number}


@pytest.mark.parametrize('kind,format_name,content', [
    ('table', 'table', {'rows': [['a|b', '<tag>'], ['line\nbreak', 'value']]}),
    ('code', 'code', 'if x:\n    print("```")\n'),
])
def test_verified_nonlatex_markdown(project: Path, kind: str, format_name: str, content: object) -> None:
    assets = load_assets(project)
    assets['a1'].kind = kind
    write_jsonl(project / 'derived/fidelity-assets.jsonl', assets.values())
    payload = candidate_input(project)
    payload['candidates'][0].update(format=format_name, content=content)
    write_json(project / 'candidate.json', payload)
    submit_candidates(project, project / 'candidate.json')
    review_input(project)
    import_asset_review(project, project / 'review.json', True)
    markdown = resolve_asset_markdown(project, '{{asset:a1}}', project / 'output')
    assert '已核验' in markdown and '转写未完成' not in markdown and '![原式 a1]' in markdown
    if kind == 'table':
        assert 'a&#124;b' in markdown and '&lt;tag&gt;' in markdown and 'line<br>break' in markdown
    else:
        assert '````\n' in markdown and content in markdown
    original = resolve_asset_markdown(project, '{{asset:a1}}', project / 'output', originals_only=True)
    assert '已核验' not in original and '![原式 a1]' in original


def test_plain_text_markdown_is_literal() -> None:
    from littrans.representations import _candidate_markdown
    rendered = _candidate_markdown({'format': 'text', 'content': '*literal* <script> [link](url)'})
    assert r'\*literal\*' in rendered and '&lt;script&gt;' in rendered and r'\[link\]' in rendered


@pytest.mark.parametrize('change', ['delete', 'manifest', 'path', 'legacy', 'receipt'])
def test_invalid_review_artifact_rejected(project: Path, change: str) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    payload = review_input(project)
    packet_path = project / 'evidence/representations/packets' / (payload['packet_id'] + '.json')
    packet = read_json(packet_path)
    if change == 'delete':
        (project / packet['render_artifact']['path']).unlink()
    elif change == 'manifest':
        packet['render_manifest']['comparison.html'] = '0' * 64
    elif change == 'path':
        packet['render_artifact']['path'] = '../outside.html'
    elif change == 'legacy':
        packet.pop('render_manifest')
        packet.pop('render_manifest_sha256')
    elif change == 'receipt':
        payload.pop('render_manifest_sha256')
        write_json(project / 'review.json', payload)
    write_json(packet_path, packet)
    with pytest.raises((ValueError, OSError)):
        import_asset_review(project, project / 'review.json', True)
    assert representation_status(project)['assets']['a1']['state'] != 'verified'


@pytest.mark.parametrize('kind', ['figure', 'mixed-region', 'table'])
def test_historical_wrong_kind_cannot_stay_verified(project: Path, kind: str) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    review_input(project)
    import_asset_review(project, project / 'review.json', True)
    assets = load_assets(project)
    assets['a1'].kind = kind
    write_jsonl(project / 'derived/fidelity-assets.jsonl', assets.values())
    assert representation_status(project)['assets']['a1']['state'] != 'verified'


def test_layout_failed_retry_does_not_keep_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from littrans import layout_runtime as runtime
    cache = tmp_path / 'cache'
    environment = cache / 'venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    marker = environment / runtime.READY_MARKER
    marker.write_text('ready\n')
    model = cache / runtime.MODEL_NAME
    model.mkdir()
    (model / 'config.json').write_text('{}')
    monkeypatch.delenv('LITTRANS_LAYOUT_PYTHON', raising=False)
    monkeypatch.delenv('LITTRANS_LAYOUT_MODEL', raising=False)
    monkeypatch.setattr(runtime, 'layout_cache_root', lambda: cache)
    monkeypatch.setattr(runtime, 'layout_runtime_status', lambda: {'ok': False})
    monkeypatch.setattr(runtime, '_pip', lambda *args: None)
    downloads = []
    def download(python, target, source):
        downloads.append(target)
        (target / 'model.safetensors').touch()
    monkeypatch.setattr(runtime, '_download_weights', download)
    monkeypatch.setattr(runtime, '_smoke_test', lambda *args: {'status': 'unavailable'})
    with pytest.raises(RuntimeError, match='smoke'):
        runtime.install_layout_runtime()
    assert not marker.exists() and downloads == [model]
    monkeypatch.setattr(runtime, '_smoke_test', lambda *args: {'status': 'ok'})
    assert runtime.install_layout_runtime()['installed']
    assert marker.is_file() and downloads == [model, model]


def test_external_runtime_does_not_require_managed_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from littrans import layout_runtime as runtime
    python = tmp_path / 'external-python'
    python.touch()
    model = tmp_path / 'external-model'
    model.mkdir()
    for name in ('config.json', 'model.safetensors'):
        (model / name).touch()
    monkeypatch.setattr(runtime, 'layout_cache_root', lambda: tmp_path / 'managed')
    monkeypatch.setattr(runtime, 'runtime_paths', lambda: (python, model))
    monkeypatch.setattr(runtime, '_run', lambda *args: subprocess.CompletedProcess([], 0, runtime.MINERU_VERSION, ''))
    assert runtime.layout_runtime_status()['ok']


def test_completed_optional_queue_honors_scope_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_efficiency_v4 import _make_project

    from littrans import workflow
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    monkeypatch.setattr(workflow, '_batch_stage_details', lambda *args: ('complete', []))
    monkeypatch.setattr(workflow, '_batch_stage', lambda *args: 'complete')
    def lane(root, manifest, units):
        return {'complete': False, 'pending': {'transcribe': ['a'], 'asset-audit': ['b']}, 'recovery': []}
    monkeypatch.setattr(workflow, '_asset_lane', lane)
    result = workflow.workflow_next(root, start_at=manifests[1].batch_id, limit=1)
    assert result['stage'] == 'complete' and result['batch_ids'] == result['ready_tasks'] == []
    assert {task['batch_id'] for task in result['optional_asset_tasks']} == {manifests[1].batch_id}
    assert {task['stage'] for task in result['optional_asset_tasks']} == {'transcribe', 'asset-audit'}
    monkeypatch.setattr(workflow, '_asset_lane', lambda *args: {'complete': True, 'pending': {}})
    assert workflow.workflow_next(root)['optional_asset_tasks'] == []


def test_damaged_render_rebuild_needs_fresh_review(project: Path) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    old_review = review_input(project)
    import_asset_review(project, project / 'review.json', True)
    old_packet = read_json(project / 'evidence/representations/packets' / (old_review['packet_id'] + '.json'))
    original_render = project / old_packet['render_artifact']['path']
    original_render.write_text('broken', encoding='utf-8')
    new_packet = build_asset_packet(project, ['a1'], 'asset-audit')
    assert new_packet['packet_id'] != old_packet['packet_id']
    assert original_render.read_text() == 'broken'
    assert representation_status(project)['assets']['a1']['state'] == 'asset-audit'
    with pytest.raises(ValueError):
        import_asset_review(project, project / 'review.json', True)
    review_input(project)
    import_asset_review(project, project / 'review.json', True)
    assert representation_status(project)['assets']['a1']['state'] == 'verified'


@pytest.mark.parametrize('number,gap,size', [('12', 0, 12), ('12', 30, 7), ('123', 0, 7)])
def test_unmatched_digits_are_not_partial_footnotes(number: str, gap: int, size: int) -> None:
    import pymupdf as fitz

    from littrans.fidelity import _native
    from littrans.source_structure import plan_structure
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 200), 'Prose with a note', fontsize=12)
        if gap:
            page.insert_text((160, 196), '1', fontsize=size)
            page.insert_text((160 + gap, 196), '2', fontsize=size)
        else:
            page.insert_text((160, 196), number, fontsize=size)
        page.insert_text((50, 700), '12Note text.', fontsize=9)
        glyphs, blocks = _native(page)
        plan = plan_structure(glyphs, blocks, [{'label': 'footnote', 'bbox': [90, 1370, 400, 1420]}], page.rect.height)
        assert not [m for m in plan['markers'].values() if not m['definition']]


def test_multidigit_footnotes_emit_once_and_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf as fitz

    from littrans import fidelity
    from littrans.models import SourceUnit
    from littrans.project import initialize_project
    from littrans.storage import read_jsonl
    source = tmp_path / 'footnotes.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for y in (200, 240):
            page.insert_text((50, y), 'Prose with a note', fontsize=12)
            page.insert_text((160, y - 4), '12', fontsize=7)
        page.insert_text((50, 700), '12Note text.', fontsize=9)
        doc.save(source)
    root = tmp_path / 'notes'
    initialize_project(source, root, 'technical-book')
    monkeypatch.setattr(fidelity, 'detect_layout', lambda images, output: {
        'status': 'ok', 'pages': {str(images[0].resolve()): [
            {'label': 'footnote', 'bbox': [90, 1370, 400, 1420]}]}})
    fidelity.prepare_source(root)
    units = read_jsonl(root / 'derived/units.jsonl', SourceUnit)
    assert sum(u.source_text.count('[^12]') for u in units) == 2
    notes = [u for u in units if u.footnote_number == '12']
    assert len(notes) == 1
    callers = [u for u in units if u.footnote_refs]
    assert callers and all(u.footnote_refs == [notes[0].unit_id] for u in callers)
