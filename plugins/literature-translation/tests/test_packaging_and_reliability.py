from __future__ import annotations

import os
import posixpath
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from littrans.extractor import apply_layout_overrides
from littrans.models import ProjectConfig, ProjectStatus, SourceUnit, TranslationRecord, UnitKind
from littrans.storage import sha256_text, write_jsonl, write_yaml


def test_distribution_contains_portable_roles_and_only_current_skills(tmp_path: Path) -> None:
    pytest.importorskip("hatchling.build")
    repository = Path(__file__).resolve().parents[3]
    output = tmp_path / "distribution"
    subprocess.run([sys.executable, str(repository / "scripts/build_distribution.py"),
                    str(output)], cwd=repository, check=True, capture_output=True, text=True)
    expected_skills = {"literature-translation", "source-processor", "context-manager",
                       "translation-coordinator"}
    expected_roles = {path.name for path in
                      (repository / "plugins/literature-translation/roles").glob("*.md")}
    for artifact, prefix in ((next(output.glob("*.zip")), "literature-translation/"),
                             (next(output.glob("*.whl")), "littrans/")):
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            assert {name.removeprefix(prefix + "roles/") for name in names
                    if name.startswith(prefix + "roles/")} == expected_roles
            assert {name.removeprefix(prefix + "skills/").split("/")[0] for name in names
                    if name.startswith(prefix + "skills/")} == expected_skills
            for document in ("task-protocol.md", "cli-reference.md", "installation.md"):
                assert prefix + "references/" + document in names
            # References must remain navigable in both distributions, not just the checkout.
            for name in names:
                if not name.startswith(prefix + "references/") or not name.endswith(".md"):
                    continue
                text = archive.read(name).decode("utf-8")
                for link in re.findall(r"\]\(([^)\s]+)\)", text):
                    if "://" in link or link.startswith("mailto:"):
                        continue
                    filename, _, fragment = link.partition("#")
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), filename)) if filename else name
                    assert target in names, (artifact, name, link)
                    if fragment and target.endswith(".md"):
                        headings = re.findall(r"^#+ (.+)$", archive.read(target).decode("utf-8"), re.M)
                        anchors = {re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-") for title in headings}
                        assert fragment in anchors, (artifact, name, link)


def test_wheel_contains_template_and_installed_render_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hatchling = pytest.importorskip("hatchling.build")
    plugin_root = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    monkeypatch.chdir(plugin_root)
    wheel_name = hatchling.build_wheel(str(wheel_dir))
    wheel_path = wheel_dir / wheel_name
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel_path) as archive:
        assert "littrans/templates/bilingual.html.j2" in archive.namelist()
        # The record scaffold ships with the package, so `project init` works from a wheel.
        for template in ("LITTRANS.md.j2", "gitignore.j2", "lt.py.j2", "reference.yaml.j2"):
            assert f"littrans/templates/scaffold/{template}" in archive.namelist()
        for profile in ("technical-book", "research-paper", "en-zh-cn"):
            assert f"littrans/profiles/{profile}.yaml" in archive.namelist()
        archive.extractall(installed)

    project = tmp_path / "project"
    script = r"""
from pathlib import Path
from littrans.models import ProjectConfig, SourceUnit, UnitKind
from littrans.rendering import render_project
from littrans.project import load_profile
from littrans.storage import sha256_text, write_jsonl, write_yaml

for profile in ('technical-book', 'research-paper'):
    settings = load_profile(profile)
    assert settings['name'] == profile
    assert settings['batch']['max_source_words'] == 900
    assert settings['batch']['soft_max_assets'] == 60
assert load_profile('en-zh-cn')

root = Path(__import__('sys').argv[1])
for directory in ('derived', 'translations', 'reviews', 'qa', 'glossary', 'output', 'batches'):
    (root / directory).mkdir(parents=True, exist_ok=True)
source = root / 'source.pdf'
source.write_bytes(b'%PDF-1.4\n')
config = ProjectConfig(
    project_id='wheel-render',
    title='Wheel Render',
    source_path=str(source),
    source_sha256='0' * 64,
    source_pages=1,
    profile='technical-book',
)
write_yaml(root / 'project.yaml', config.model_dump(mode='json', exclude_none=True))
text = 'Packaged template is available.'
write_jsonl(
    root / 'derived' / 'units.jsonl',
    [SourceUnit(
        unit_id='p0001-u001-packaged',
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=text,
        source_hash=sha256_text(text),
        translatable=False,
        confidence=1,
    )],
)
outputs = render_project(root, '1', 'wheel', allow_draft=True)
rendered = Path(outputs['html']).read_text(encoding='utf-8')
assert 'Wheel Render' in rendered
assert '双语译本' in rendered
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    subprocess.run(
        [sys.executable, "-c", script, str(project)],
        check=True,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_failed_override_validation_leaves_project_files_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    for directory in ("derived", "translations", "overrides"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    source = root / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    config = ProjectConfig(
        project_id="override-transaction",
        title="Override transaction",
        source_path=str(source),
        source_sha256="0" * 64,
        source_pages=1,
        profile="technical-book",
        status=ProjectStatus.EXTERNAL_REVIEWED,
    )
    write_yaml(root / "project.yaml", config.model_dump(mode="json", exclude_none=True))
    text = "Original source text."
    unit = SourceUnit(
        unit_id="p0001-u001-original",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=text,
        source_hash=sha256_text(text),
        confidence=1,
    )
    write_jsonl(root / "derived" / "units.jsonl", [unit])
    write_jsonl(
        root / "translations" / "current.jsonl",
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="原始译文。",
                source_hash=unit.source_hash,
                status=ProjectStatus.EXTERNAL_REVIEWED,
            )
        ],
    )
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "kind": "heading",
                    "verified": True,
                    "reason": "Verified against the source page.",
                }
            ]
        },
    )
    # This validation error used to be discovered only after units and
    # translations had already been replaced.
    (root / "derived" / "extraction-issues.jsonl").write_text(
        '{"not": "a valid ExtractionIssue"}\n', encoding="utf-8"
    )
    tracked = [
        root / "derived" / "units.jsonl",
        root / "translations" / "current.jsonl",
        root / "project.yaml",
        root / "derived" / "extraction-issues.jsonl",
    ]
    before = {path: path.read_bytes() for path in tracked}

    with pytest.raises(ValueError, match="Invalid record"):
        apply_layout_overrides(root)

    assert {path: path.read_bytes() for path in tracked} == before
    assert not list(root.glob(".littrans-overrides-*"))


def test_console_streams_emit_utf8_with_lf() -> None:
    import io

    from littrans.cli import _configure_console_stream

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk", newline=None)
    _configure_console_stream(stream)
    stream.write("• 列表项\n")
    stream.flush()
    assert buffer.getvalue() == "• 列表项\n".encode()

    class NoReconfigure:
        pass

    _configure_console_stream(NoReconfigure())  # must not raise


def test_generated_batch_files_use_lf(tmp_path: Path) -> None:
    from test_efficiency_v4 import _make_project

    root, manifests = _make_project(tmp_path, pages=1)
    batch_dir = root / "batches" / manifests[0].batch_id
    for name in ("source.md", "context.md"):
        assert b"\r\n" not in (batch_dir / name).read_bytes()
    for name in ("document-brief.md", "style-guide.md"):
        assert b"\r\n" not in (root / "context" / name).read_bytes()
