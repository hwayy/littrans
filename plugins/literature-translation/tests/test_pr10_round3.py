import subprocess
from pathlib import Path

import pytest
from test_fidelity_source import approve
from test_fidelity_source import project as source_project

from littrans import fidelity, layout_detector
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.rendering import _unit_html
from littrans.storage import read_json, write_json

project = source_project


@pytest.mark.parametrize('field', ['passed', 'reviewer', 'decision', 'digest', 'packet'])
def test_source_receipt_mutation_fails_closed(project: Path, field: str) -> None:
    fidelity.prepare_source(project, '1', allow_missing_layout=True)
    approve(project)
    receipt_path = project / 'evidence/pages/fidelity-p0001.review.json'
    receipt = read_json(receipt_path)
    if field == 'passed':
        review = read_json(project / 'review.json')
        review['pages'][0]['coverage_complete'] = False
        write_json(project / 'review.json', review)
        fidelity.import_source_review(project, project / 'review.json', True)
        receipt = read_json(receipt_path)
        receipt['passed'] = True
    elif field == 'reviewer':
        receipt['reviewer'] = ''
    elif field == 'decision':
        receipt['decision']['viewed_original'] = False
    elif field == 'digest':
        receipt.pop('receipt_sha256', None)
    else:
        packet = fidelity.build_source_review_packet(project, '1')
        Path(packet['packet_path']).write_text('{}', encoding='utf-8')
    write_json(receipt_path, receipt)
    assert not fidelity.verify_fidelity(project, '1')['passed']


@pytest.mark.parametrize('source_view', [True, False])
def test_table_footnotes_link_to_scoped_definition(source_view: bool) -> None:
    table = TableData(rows=[['Heading[^1]'], ['Value[^1]']], column_count=1)
    unit = SourceUnit(unit_id='table', kind=UnitKind.TABLE, page=2, bbox=(0, 0, 1, 1),
                      source_text='Table', source_hash='test', table=table, confidence=1.0)
    note = unit.model_copy(update={'kind': UnitKind.FOOTNOTE, 'footnote_number': '1', 'source_text': 'Definition'})
    rendered = _unit_html(unit, None, table, source_view=source_view)
    definition = _unit_html(note, None, source_view=source_view)
    target = f"fn-p2-{'source' if source_view else 'target'}-1"
    assert rendered.count(f'href="#{target}"') == 2
    assert f'id="{target}"' in definition


@pytest.mark.parametrize('change', ['worker', 'interpreter', 'version', 'failed-probe'])
def test_layout_cache_binds_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    python = tmp_path / 'python.exe'
    python.touch()
    model = tmp_path / 'model'
    model.mkdir()
    worker = tmp_path / 'layout_worker.py'
    worker.write_text('# worker v1', encoding='utf-8')
    monkeypatch.setattr(layout_detector, '__file__', str(tmp_path / 'layout_detector.py'))
    monkeypatch.setattr(layout_detector, 'runtime_paths', lambda: (python, model))
    identity = {'python': '3.12', 'packages': {'mineru': '3.4.5'}}
    output = tmp_path / 'result.json'
    executions = []

    def run(command, **kwargs):
        if command[1] == '-c':
            if identity.get('failed'):
                return subprocess.CompletedProcess(command, 1, '', 'broken runtime')
            import json
            return subprocess.CompletedProcess(command, 0, json.dumps(identity), '')
        executions.append(command)
        request = read_json(Path(command[2]))
        write_json(output, {'status': 'ok', 'pages': {}, 'fingerprint': request['fingerprint']})
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(layout_detector.subprocess, 'run', run)
    assert layout_detector.detect_layout([], output)['status'] == 'ok'
    assert layout_detector.detect_layout([], output)['status'] == 'ok'
    assert len(executions) == 1
    if change == 'worker':
        worker.write_text('# worker v2', encoding='utf-8')
    elif change == 'interpreter':
        python = tmp_path / 'other-python.exe'
        python.touch()
    elif change == 'version':
        identity['python'] = '3.13'
    else:
        identity['failed'] = True
    result = layout_detector.detect_layout([], output)
    assert result['status'] == ('unavailable' if change == 'failed-probe' else 'ok')
    assert len(executions) == (1 if change == 'failed-probe' else 2)


@pytest.mark.parametrize('field', ['reviewer', 'decision', 'source', 'page', 'packet_id'])
def test_source_receipt_revalidates_provenance_and_decision(project: Path, field: str) -> None:
    fidelity.prepare_source(project, '1', allow_missing_layout=True)
    approve(project)
    path = project / 'evidence/pages/fidelity-p0001.review.json'
    receipt = read_json(path)
    if field == 'reviewer':
        receipt['reviewer'] = ''
    elif field == 'decision':
        receipt['decision']['coverage_complete'] = False
    elif field == 'source':
        receipt['source_sha256'] = '0' * 64
    elif field == 'page':
        receipt['decision']['page'] = 2
    else:
        receipt['packet_id'] = '../outside'
    receipt['receipt_sha256'] = fidelity._hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
    write_json(path, receipt)
    assert not fidelity.verify_fidelity(project, '1')['passed']


def test_legacy_source_receipt_requires_fresh_review(project: Path) -> None:
    fidelity.prepare_source(project, '1', allow_missing_layout=True)
    approve(project)
    path = project / 'evidence/pages/fidelity-p0001.review.json'
    receipt = read_json(path)
    receipt.pop('receipt_sha256')
    receipt.pop('packet_id')
    write_json(path, receipt)
    assert not fidelity.verify_fidelity(project, '1')['passed']
    approve(project)
    assert fidelity.verify_fidelity(project, '1')['passed']
