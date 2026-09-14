from pathlib import Path

import pytest
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as asset_project

from littrans.representations import (
    build_asset_packet,
    import_asset_review,
    representation_status,
    resolve_asset_html,
    submit_candidates,
)
from littrans.storage import read_json, write_json

project = asset_project


@pytest.mark.parametrize(
    "old_verdict,new_verdict",
    [("accept", "reject"), ("accept", "unresolved"), ("reject", "accept")],
)
def test_replay_cannot_replace_later_review(project: Path, old_verdict: str, new_verdict: str):
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    old = review_input(project, old_verdict)
    import_asset_review(project, project / "review.json", True)
    packet = build_asset_packet(project, ["a1"], "asset-audit", host="claude")
    assert packet["packet_id"] != old["packet_id"]
    newer = {
        **old,
        "packet_id": packet["packet_id"],
        "render_artifact_sha256": packet["render_artifact"]["sha256"],
        "render_manifest_sha256": packet["render_manifest_sha256"],
        "image_evidence": packet["required_images"],
        "decisions": [{**old["decisions"][0], "verdict": new_verdict}],
    }
    write_json(project / "newer-review.json", newer)
    import_asset_review(project, project / "newer-review.json", True)
    index_path = project / "evidence/representations/index.json"
    before = read_json(index_path)
    assert import_asset_review(project, project / "review.json", True)["replayed"]
    assert read_json(index_path) == before
    assert representation_status(project)["assets"]["a1"]["state"] == (
        "verified" if new_verdict == "accept" else "fallback"
    )
    rendered = resolve_asset_html(project, "{{asset:a1}}", project / "output")
    assert ("data-candidate-latex=" in rendered) is (new_verdict == "accept")


def test_replay_recovers_absent_mapping(project: Path):
    candidate_input(project)
    submit_candidates(project, project / "candidate.json")
    review = review_input(project)
    import_asset_review(project, project / "review.json", True)
    index_path = project / "evidence/representations/index.json"
    index = read_json(index_path)
    sha = review["decisions"][0]["candidate_sha256"]
    del index["reviews"][sha]
    write_json(index_path, index)
    assert representation_status(project)["assets"]["a1"]["state"] == "asset-audit"
    assert import_asset_review(project, project / "review.json", True)["replayed"]
    assert read_json(index_path)["reviews"][sha] == review["packet_id"]
    assert representation_status(project)["assets"]["a1"]["state"] == "verified"
