import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from test_efficiency_v4 import _audit_and_approve, _make_project, _submit
from test_fidelity_source import approve
from test_footnotes_v6 import _linked_notes
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans.batching import create_batches
from littrans.models import AssetTranslation, ReviewIssue, SourceUnit, UnitKind
from littrans.rendering import render_project
from littrans.storage import read_jsonl, write_jsonl, write_yaml
from littrans.workflow import create_workflow_packet, workflow_next, workflow_status

project = workflow_project


def completed_notes(tmp_path):
    root, batches, units = _linked_notes(tmp_path)
    for index, batch in enumerate(batches):
        _submit(root, batch.batch_id, target_text="译文[^1]" if index == 0 else "注释")
    for batch in batches:
        _audit_and_approve(root, batch.batch_id)
    return root, batches, units


@pytest.mark.parametrize("page", [1, 2])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_completed_workflow_rechecks_source_closure(tmp_path, page, damage):
    root, batches, _ = completed_notes(tmp_path)
    bid = batches[0].batch_id
    assert workflow_status(root, [bid])["complete"]
    path = root / f"evidence/pages/fidelity-p{page:04d}.review.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}", encoding="utf-8")
    status = workflow_status(root, [bid])
    assert status["stage"] == "source-review" and not status["complete"]
    assert not status["optional_asset_tasks"]
    assert workflow_next(root, start_at=bid, through=bid)["stage"] == "source-review"
    packet = create_workflow_packet(root, "source-review", [bid])
    assert set(packet["pages"]) == {1, 2}
    assert workflow_status(root, [batches[2].batch_id])["complete"]


def test_dependency_floor_supports_identity_exclusions():
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    dependency = next(Requirement(value) for value in config["project"]["dependencies"] if value.startswith("pydantic"))
    assert "2.11.10" not in dependency.specifier
    assert "2.12.0" in dependency.specifier


@pytest.mark.parametrize("field", ["first", "last", "both"])
@pytest.mark.parametrize("kind", [UnitKind.PARAGRAPH, UnitKind.NOTE, UnitKind.LIST_ITEM])
def test_continuation_companions_follow_complete_prose(project, field, kind):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    first = next(u for u in units if u.page == 1 and u.translatable)
    last = next(u for u in units if u.page == 2 and u.translatable)
    first.kind = last.kind = kind
    first.continued_to_next = last.continues_from_previous = True
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = create_batches(project, "1-2", prefix="companions")[0]
    def change(unit, record):
        record.target_text = ("开始正文" if unit.page == 1 else "结束正文") + " " + " ".join(
            "{{asset:" + aid + "}}" for aid in unit.asset_content_hashes)
        if field == "both" or (field == "first") == (unit.page == 1):
            record.asset_translations = [AssetTranslation(asset_id=aid, target_text=f"第{unit.page}处伴随")
                                         for aid in unit.asset_content_hashes]
    submit(project, batch.batch_id, change)
    output = render_project(project, None, batch_id=batch.batch_id, allow_draft=True)
    for format in ("html", "markdown"):
        content = Path(output[format]).read_text(encoding="utf-8")
        for page in ([1, 2] if field == "both" else [1] if field == "first" else [2]):
            assert content.count(f"第{page}处伴随") == 1
            assert content.index("开始正文") < content.index("结束正文") < content.index(f"第{page}处伴随")
        if format == "html":
            assert "</aside></p>" not in content


@pytest.mark.parametrize("severity", ["blocker", "major"])
@pytest.mark.parametrize("has_owner", [False, True])
def test_readonly_issue_routes_to_editable_owner(tmp_path, severity, has_owner):
    root, batches = _make_project(tmp_path, pages=2, max_words=100)
    first, second = batches
    for batch in batches:
        _submit(root, batch.batch_id)
    second.unit_ids = [*first.unit_ids, *second.unit_ids]
    second.read_only_unit_ids = list(first.unit_ids)
    second.pages = [1, 2]
    write_yaml(root / "batches" / second.batch_id / "manifest.yaml", second.model_dump(mode="json"))
    for batch in batches:
        _audit_and_approve(root, batch.batch_id)
    issue = ReviewIssue(issue_id="readonly-fix", batch_id=second.batch_id, unit_id=first.unit_ids[0],
                        severity=severity, type="meaning", explanation="Fix the context translation", reviewer="reviewer")
    write_jsonl(root / "reviews" / f"{second.batch_id}.issues.jsonl", [issue])
    if not has_owner:
        first.translatable_unit_ids = []
        first.read_only_unit_ids = list(first.unit_ids)
        write_yaml(root / "batches" / first.batch_id / "manifest.yaml", first.model_dump(mode="json"))
        with pytest.raises(ValueError, match="no editable owning batch"):
            workflow_next(root, start_at=second.batch_id, through=second.batch_id)
        return
    result = workflow_next(root, start_at=second.batch_id, through=second.batch_id)
    assert result["stage"] == "revise" and result["batch_ids"] == [first.batch_id]
    tasks = workflow_status(root, [second.batch_id])["ready_tasks"]
    assert [(t["stage"], t["batch_id"]) for t in tasks] == [("revise", first.batch_id)]
    packet = create_workflow_packet(root, "revise", [first.batch_id])
    issues = read_jsonl(root / packet.storage_root / packet.packet_id / f"{first.batch_id}.issues.jsonl", ReviewIssue)
    assert issues == [issue]


@pytest.mark.parametrize("qa_current", [False, True])
def test_explicit_render_uses_current_dependency_cover(tmp_path, qa_current):
    root, batches, _ = completed_notes(tmp_path)
    dependency = batches[1]
    stale = dependency.model_copy(deep=True, update={"batch_id": "v4-b999", "created_at": "2099-01-01T00:00:00Z"})
    write_yaml(root / "batches" / stale.batch_id / "manifest.yaml", stale.model_dump(mode="json"))
    if qa_current:
        from littrans.quality import run_qa
        assert run_qa(root, stale.batch_id).passed
    output = render_project(root, None, batch_id=batches[0].batch_id)
    assert Path(output["html"]).is_file()
