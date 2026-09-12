from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as asset_project

from littrans import layout_detector
from littrans.representation_models import AssetReviewSubmission
from littrans.representations import (
    import_asset_review,
    representation_status,
    resolve_asset_html,
    submit_candidates,
)
from littrans.storage import read_json, write_json

project = asset_project


@pytest.mark.parametrize('corruption', ['verdict', 'receipt', 'missing', 'decisions'])
def test_stored_review_corruption_cannot_grant_approval(project: Path, corruption: str) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    payload = review_input(project, verdict='reject' if corruption == 'verdict' else 'accept')
    import_asset_review(project, project / 'review.json', True)
    review_path = project / 'evidence/representations/reviews' / (payload['packet_id'] + '.json')
    record = read_json(review_path)
    if corruption == 'verdict':
        record['decisions'][0]['verdict'] = 'accept'
    elif corruption == 'receipt':
        record['image_evidence'] = {}
    elif corruption == 'missing':
        record.pop('review_sha256')
    else:
        record['decisions'] = []
    write_json(review_path, record)
    assert representation_status(project)['assets']['a1']['state'] == 'asset-audit'
    assert 'data-candidate-latex' not in resolve_asset_html(project, '{{asset:a1}}', project / 'output')
    with pytest.raises(ValueError, match='review|Review'):
        import_asset_review(project, project / 'review.json', True)


@pytest.mark.parametrize('value', [None, '', 'not-a-hash'])
def test_review_schema_rejects_invalid_manifest_digest(project: Path, value: str | None) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    payload = review_input(project)
    payload['render_manifest_sha256'] = value
    with pytest.raises(ValidationError):
        AssetReviewSubmission.model_validate(payload)


def test_review_schema_requires_manifest_receipt(project: Path) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    payload = review_input(project)
    payload.pop('render_manifest_sha256')
    assert 'render_manifest_sha256' in AssetReviewSubmission.model_json_schema()['required']
    with pytest.raises(ValidationError):
        AssetReviewSubmission.model_validate(payload)


@pytest.mark.parametrize('cached', [False, True])
def test_unready_managed_detector_never_runs_or_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool) -> None:
    cache = tmp_path / 'managed'
    python = cache / 'venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    model = cache / 'PP-DocLayoutV2'
    model.mkdir()
    (model / 'model.safetensors').touch()
    monkeypatch.setattr(layout_detector, 'layout_cache_root', lambda: cache)
    monkeypatch.setattr(layout_detector, 'runtime_paths', lambda: (python, model))
    output = tmp_path / 'layout.json'
    def run(*args, **kwargs):
        write_json(output, {'status': 'ok', 'pages': {}})
        return subprocess.CompletedProcess([], 0, '', '')
    monkeypatch.setattr(layout_detector.subprocess, 'run', run)
    if cached:
        write_json(output, {'status': 'ok', 'pages': {}, 'fingerprint': 'old'})
    before = output.read_bytes() if output.exists() else None
    result = layout_detector.detect_layout([], output)
    assert result['status'] == 'unavailable' and 'smoke' in result['reason']
    assert (output.read_bytes() if output.exists() else None) == before
    assert not output.with_suffix('.request.json').exists()


@pytest.mark.parametrize('managed_python,managed_model', [(True, True), (True, False), (False, True), (False, False)])
def test_detector_readiness_and_cache_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                managed_python: bool, managed_model: bool) -> None:
    cache = tmp_path / 'managed'
    python = (cache if managed_python else tmp_path / 'external') / 'venv/Scripts/python.exe'
    model = (cache if managed_model else tmp_path / 'external') / 'PP-DocLayoutV2'
    python.parent.mkdir(parents=True)
    python.touch()
    model.mkdir(parents=True)
    (model / 'model.safetensors').write_bytes(b'weights')
    monkeypatch.setattr(layout_detector, 'layout_cache_root', lambda: cache)
    monkeypatch.setattr(layout_detector, 'runtime_paths', lambda: (python, model))
    output = tmp_path / 'layout.json'
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        request = read_json(Path(command[2]))
        write_json(output, {'status': 'ok', 'pages': {}, 'fingerprint': request['fingerprint']})
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(layout_detector.subprocess, 'run', run)
    marker = cache / 'venv' / layout_detector.READY_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    assert layout_detector.detect_layout([], output)['status'] == 'ok'
    assert layout_detector.detect_layout([], output)['status'] == 'ok'
    assert len(calls) == 1
    marker.unlink()
    expected = 'unavailable' if managed_python or managed_model else 'ok'
    assert layout_detector.detect_layout([], output)['status'] == expected
    assert len(calls) == 1


@pytest.mark.parametrize('verdict', ['accept', 'reject', 'unresolved'])
def test_intact_review_replays_and_preserves_verdict(project: Path, verdict: str) -> None:
    candidate_input(project)
    submit_candidates(project, project / 'candidate.json')
    review_input(project, verdict=verdict)
    import_asset_review(project, project / 'review.json', True)
    assert import_asset_review(project, project / 'review.json', True)['replayed']
    assert representation_status(project)['assets']['a1']['state'] == (
        'verified' if verdict == 'accept' else 'fallback')
