"""Round 16: the defects the chapter 1 rebuild ledger reported (LT-077 … LT-082).

Each test states the rule that was missing. The ledger's pages were replayed on a scratch
copy of the project to settle the rules; the tests here are synthetic units, glyph lines
and tiny projects only. They cover: the scope of the workflow coverage check (077); the
scaffolded launcher's host awareness and build metadata (078); fraction and case rows of a
display box and the prose margin (079); the decorative rule as a parent and the page-top
continuation flag (080); enumerated items, proof steps, italic statement continuations and
planner continuations (081); the proof tombstone after its display (082).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from test_efficiency_v4 import _make_project
from test_source_structure_v8 import _line

from littrans.fidelity import (
    _delimiter_columns,
    _display_box_owned,
    _left_margin,
    _make_unit,
    _prose_word_ids,
    _separate_display_units,
)
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.hosts import HOST_ENV_SIGNALS
from littrans.models import RenderPolicy, SourceUnit, UnitKind
from littrans.project import initialize_project
from littrans.scaffold import render_scaffold_template, scaffold_context
from littrans.source_structure import _emphasised_share, assemble_structure
from littrans.storage import read_jsonl, write_jsonl
from littrans.workflow import workflow_next, workflow_status


def _plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {"omitted": {}, "notes": {}, "note_top": 1000.0, "first_x": {}, "margin": 58.7,
                            "font_size": 10.9, "indent_style": True, "display_blocks": [], "blocks": []}
    plan.update(overrides)
    return plan


# --- LT-077 -------------------------------------------------------------------------------

def _batch_only_page_one(tmp_path: Path) -> tuple[Path, list[str]]:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    kept = []
    for manifest in manifests:
        if 2 in manifest.pages:  # type: ignore[attr-defined]
            shutil.rmtree(root / "batches" / manifest.batch_id)  # type: ignore[attr-defined]
        else:
            kept.append(manifest.batch_id)  # type: ignore[attr-defined]
    assert kept
    return root, kept


def test_workflow_status_and_next_coordinate_their_scope_not_the_whole_record(tmp_path: Path) -> None:
    root, batch_ids = _batch_only_page_one(tmp_path)
    # Page 2 is extracted and verified but not yet batched: coordinating page 1 reports it.
    status = workflow_status(root, batch_ids)
    assert status["stage"] == "translate" and status["unbatched_pages"] == [2]
    following = workflow_next(root)
    assert following["stage"] == "translate" and following["unbatched_pages"] == [2]
    # A unit recovered on a coordinated page still blocks the wave.
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    inserted = units[0].model_copy(update={"unit_id": "p0001-u999-inserted", "source_text": "Recovered on page one.", "source_hash": "f" * 64})
    write_jsonl(units_path, [*units, inserted])
    with pytest.raises(ValueError, match="do not cover current renderable source units.*p0001-u999-inserted"):
        workflow_status(root, batch_ids)
    with pytest.raises(ValueError, match="unbatched_units=.*p0001-u999-inserted"):
        workflow_next(root)


# --- LT-078 -------------------------------------------------------------------------------

def _launcher(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, **constants: str) -> ModuleType:
    root = tmp_path / "project"
    (tmp_path / "book.pdf").write_bytes(b"")
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Fixture page.")
    document.save(tmp_path / "book.pdf")
    initialize_project(tmp_path / "book.pdf", root, "technical-book", "Fixture")
    launcher = root / "tools" / "lt.py"
    text = launcher.read_text(encoding="utf-8")
    for name, value in constants.items():
        start = text.index(f"{name} = ")
        text = text.replace(text[start:text.index("\n", start)], f"{name} = {value}")
    launcher.write_text(text, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("LITTRANS_PLUGIN_ROOT", raising=False)
    for names in HOST_ENV_SIGNALS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("lt_launcher_round16", launcher)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin(directory: Path) -> Path:
    (directory / "scripts").mkdir(parents=True, exist_ok=True)
    (directory / "scripts" / "littrans.py").write_text("import sys; print(sys.argv[0])\n", encoding="utf-8")
    return directory


def test_version_key_accepts_build_metadata_as_a_later_build() -> None:
    text = render_scaffold_template("lt.py.j2", scaffold_context(Path.cwd(), Path.cwd()))
    namespace: dict[str, Any] = {}
    exec(compile(text, "lt.py", "exec"), namespace)  # noqa: S102 - the scaffolded launcher itself
    key = namespace["version_key"]
    assert key("0.6.0-dev.13+codex.20260920173940") > key("0.6.0-dev.13") > key("0.6.0-dev.7")
    assert key("0.6.0") > key("0.6.0-dev.13+codex.20260920173940")
    assert key("0.10.0") > key("0.9.9") > key("notes")
    # The launcher reads the session's client by the plugin's own signals.
    assert {host: tuple(names) for host, names in namespace["HOST_SIGNALS"].items()} == dict(HOST_ENV_SIGNALS)


def test_launcher_runs_what_the_sessions_client_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    module = _launcher(tmp_path, home, monkeypatch,
                       RECORDED_PLUGIN_ROOT='r"/home/other/.claude/plugins/cache/littrans/literature-translation/0.6.0-dev.13"',
                       RECORDED_HOME_RELATIVE_ROOT='".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.13"')
    stale = _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0")
    codex = _plugin(home / ".codex/plugins/cache/littrans/literature-translation/0.6.0-dev.13+codex.20260920173940")
    # A Codex session runs the Codex install although another client's cache holds a higher version.
    monkeypatch.setenv("CODEX_THREAD_ID", "t1")
    assert module.resolve_plugin_root() == codex
    monkeypatch.delenv("CODEX_THREAD_ID")
    # Without a session signal the highest installed version wins, whichever client holds it.
    assert module.resolve_plugin_root() == stale
    # Claude Code's own record names its installation; a leftover directory is not one.
    installed = _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.7")
    (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({
        "version": 2, "plugins": {"literature-translation@littrans": [{"scope": "user", "installPath": str(installed), "version": "0.6.0-dev.7"}]},
    }), encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert module.resolve_plugin_root() == installed
    monkeypatch.delenv("CLAUDECODE")
    assert module.resolve_plugin_root() == codex  # dev.13+codex is the highest installed build
    # The recorded root wins while it exists on this host.
    recorded = _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.13")
    assert module.resolve_plugin_root() == recorded


# --- LT-079 -------------------------------------------------------------------------------

def test_left_margin_is_the_pen_origin_of_text_lines_not_brace_pieces() -> None:
    glyphs: list[dict[str, Any]] = []
    for index, word in enumerate(("Theorem", "obvious", "where", "and")):
        line = _line(word, "CMR10", line=f"b{index}-l0", y=100.0 + 20 * index, x=58.0)
        for glyph in line:  # the ink starts where each letter's side bearing puts it
            glyph["bbox"][0] += 0.3 * (index % 3)
        glyphs.extend(line)
    for index in range(7):  # seven pieces of one stretched brace, each a native line at one x
        glyphs.extend(_line("⎨", "CMEX10", line=f"b9-l{index}", y=300.0 + 4 * index, x=203.2))
    assert _left_margin(glyphs) == 58.0


def test_a_numerator_row_of_words_over_a_fraction_bar_stays_in_the_display() -> None:
    numerator = _line("surface area(U)", _mixed("surface area(U)", "(U)"), line="b2-l0", y=150.0, x=215.0)
    main = _line("P(U) =", "CMMI10", line="b3-l0", y=162.0, x=139.0)
    denominator = _line("4π", "CMMI10", line="b3-l1", y=174.0, x=250.0)
    owned = [*numerator, *main, *denominator]
    prose = _prose_word_ids(owned)
    assert {g["text"] for g in owned if g["id"] in prose} == set("surfacearea")
    bar = [[214.0, 161.0, 306.0, 161.4]]
    kept, outside, _ = _display_box_owned(owned, prose, 58.7, bar)
    assert {g["id"] for g in numerator} <= {g["id"] for g in kept} and not outside
    # Without the bar the row of words is prose beside the formula, as before.
    kept, outside, _ = _display_box_owned(owned, prose, 58.7)
    assert not {g["id"] for g in numerator} & {g["id"] for g in kept} and outside


def _mixed(text: str, math: str) -> list[str]:
    return ["CMMI10" if c in math else "CMR10" for c in text]


def test_case_rows_are_bracketed_by_the_whole_delimiter_column() -> None:
    pieces = [
        *_line("⎧", "CMEX10", line="b5-l0", y=532.6, x=203.2),
        *_line("⎪", "CMEX10", line="b5-l1", y=542.3, x=203.2),
        *_line("⎨", "CMEX10", line="b5-l2", y=548.8, x=203.2),
        *_line("⎪", "CMEX10", line="b5-l3", y=568.4, x=203.2),
        *_line("⎩", "CMEX10", line="b5-l4", y=575.0, x=203.2),
    ]
    for piece, (top, bottom) in zip(pieces, ((532.6, 542.5), (542.3, 549.1), (548.8, 568.7), (568.4, 575.2), (575.0, 585.0)), strict=True):
        piece["bbox"] = [203.2, top, 204.5, bottom]
        piece["baseline"] = top
    columns = _delimiter_columns(pieces)
    assert columns == [[203.2, 532.6, 204.5, 585.0]]
    row = _line("p(x), X is discrete-valued,", _mixed("p(x), X is discrete-valued,", "pxX"), line="b6-l0", y=544.0, x=209.0)
    for glyph in row:
        glyph["baseline"] = 552.0  # the row's baseline falls between two extender pieces
    head = _line("M(t) =", "CMMI10", line="b4-l0", y=554.0, x=122.7)
    owned = [*pieces, *head, *row]
    prose = _prose_word_ids(owned)
    kept, _, _ = _display_box_owned(owned, prose, 58.7)
    assert {g["id"] for g in row} <= {g["id"] for g in kept}
    # A row at the prose margin is still the paragraph the box overshot into.
    kept, outside, _ = _display_box_owned(owned, prose, 205.0)
    assert not {g["id"] for g in row} & {g["id"] for g in kept} and outside


# --- LT-080 -------------------------------------------------------------------------------

def test_an_omitted_rule_is_never_the_parent_of_the_prose_after_it() -> None:
    ASSETS["a-p0043-4a930e88d655"] = _asset("a-p0043-4a930e88d655", "mixed-region", [58.2, 61.1, 417.9, 62.7])
    ASSETS["a-p0043-e29aee420cc9"] = _asset("a-p0043-e29aee420cc9", "math", [169.3, 103.8, 300, 130], display=True)
    rule = _unit("p0043-visual-a-p0043-4a930e88d655", "{{asset:a-p0043-4a930e88d655}}", [58.2, 61.1, 417.9, 62.7],
                 kind="note", render_policy="omit", translatable=False)
    first = _unit("p0043-b1", "One immediately has", [58.7, 90.5, 400, 101])
    display = _unit("p0043-b2", "{{asset:a-p0043-e29aee420cc9}}", [169.3, 103.8, 300, 130], kind="equation")
    plan = _plan(first_x={"b1": 58.7, "b2": 169.3})
    result = assemble_structure([rule, first, display], ASSETS, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0043-visual-a-p0043-4a930e88d655", "p0043-b1", "p0043-b1"]
    assert result[0].render_policy is RenderPolicy.OMIT
    # A rule inside the page keeps the open group as its parent, as recorded before.
    mid = _unit("p0043-visual-a-p0043-bb0fb2a74e3c", "{{asset:a-p0043-4a930e88d655}}", [398.9, 642.4, 417.9, 643.0],
                kind="note", render_policy="omit", translatable=False)
    result = assemble_structure([first, display, mid], ASSETS, plan, _make_unit)
    assert result[2].parent_id == "p0043-b1"


def _asset(aid: str, kind: str, bbox: list[float], display: bool = False) -> FidelityAsset:
    fragment = FidelityFragment(page=43, bbox=bbox, width=bbox[2] - bbox[0], height=bbox[3] - bbox[1], png_path="x.png", svg_path="x.svg",
                                file_sha256={name: "c" * 64 for name in ("x.png", "x.svg")})
    return FidelityAsset(id=aid, kind=kind, source_sha256="0" * 64, content_sha256="1" * 64, fragments=[fragment], display=display, grouping_pending=False, provenance=["test"])


# The assets the synthetic units reference: a placeholder a test did not declare is inline math.
ASSETS: dict[str, FidelityAsset] = {}


def _unit(uid: str, text: str, bbox: list[float], page: int = 2, kind: str = "paragraph", **extra: Any) -> SourceUnit:
    for aid in re.findall(r"\{\{asset:([^}]+)\}\}", text):
        ASSETS.setdefault(aid, _asset(aid, "math", [100.0, 100.0, 120.0, 110.0]))
    return _make_unit(page, uid, text, bbox, ASSETS, kind=kind, **extra)


def test_the_page_top_flag_marks_a_continued_sentence_not_a_new_one() -> None:
    plan = _plan(first_x={"b1": 58.7})
    for opening in ("This gives", "Then", "In one dimension if {{asset:a}} this reduces to"):
        unit = _unit("p0044-b1", opening, [58.7, 90.5, 400, 101])
        assert not assemble_structure([unit], ASSETS, plan, _make_unit)[0].continues_from_previous, opening
    for opening in ("where the notation means", "of the chain, which we now prove.", "{{asset:a}} is discrete-valued, provided"):
        unit = _unit("p0050-b1", opening, [58.7, 90.5, 400, 101])
        assert assemble_structure([unit], ASSETS, plan, _make_unit)[0].continues_from_previous, opening
    # A script without letter case keeps the geometric reading.
    unit = _unit("p0050-b1", "这个记号表示", [58.7, 90.5, 400, 101])
    assert assemble_structure([unit], ASSETS, plan, _make_unit)[0].continues_from_previous


# --- LT-081 -------------------------------------------------------------------------------

def test_enumerated_items_at_an_indent_hang_from_the_paragraph_that_introduces_them() -> None:
    intro = _unit("p0043-b26", "This can be used to compute some generating functions.", [76.6, 422.9, 400, 434])
    items = [
        _unit("p0043-b27", "(a) Bernoulli distribution: {{asset:x}}.", [82.1, 443.3, 400, 454]),
        _unit("p0043-b28", "(b) Binomial distribution: {{asset:y}}.", [81.5, 459.0, 400, 470]),
        _unit("p0043-b29", "(c) Poisson distribution: {{asset:z}}.", [82.7, 476.1, 400, 487]),
    ]
    after = _unit("p0043-b30", "The moment generating function of a random variable is defined for all", [76.6, 498.5, 400, 509])
    plan = _plan(first_x={"b26": 76.6, "b27": 82.1, "b28": 81.5, "b29": 82.7, "b30": 76.6})
    result = assemble_structure([intro, *items, after], ASSETS, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0043-b26"] * 4 + ["p0043-b30"]
    assert [u.unit_id for u in result] == ["p0043-b26", "p0043-b27", "p0043-b28", "p0043-b29", "p0043-b30"]


def test_proof_steps_join_the_proof_including_one_whose_display_widens_its_box() -> None:
    ASSETS["a"] = _asset("a", "math", [106.9, 296.9, 300, 330], display=True)
    ASSETS["b"] = _asset("b", "math", [91.0, 383.4, 300, 410], display=True)
    proof = _unit("p0040-b10", "**Proof.** (i) Note that", [58.7, 287.5, 400, 298])
    display = _unit("p0040-b11", "{{asset:a}}", [106.9, 296.9, 300, 330], kind="equation")
    tail = _unit("p0040-b15", "by the almost sure convergence and dominated convergence theorems.", [58.7, 337.5, 400, 348])
    second = _unit("p0040-b16", "(ii) The proof will be deferred to Section 1.11.", [76.6, 353.9, 400, 364])
    third = _unit("p0040-b17", "(iii) This is a consequence of the Hölder inequality:", [76.6, 370.4, 400, 381])
    third_display = _unit("p0040-b18", "{{asset:b}}", [91.0, 383.4, 300, 410], kind="equation")
    fifth = _unit("p0040-b29", "(v) Argue by contradiction. Suppose there exist a bounded continuous function", [58.7, 485.6, 400, 530])
    heading = _unit("p0040-b32", "1.9. Characteristic Function", [58.7, 594.8, 300, 606], kind="heading")
    plan = _plan(first_x={"b10": 58.7, "b11": 106.9, "b15": 58.7, "b16": 76.6, "b17": 76.6, "b18": 91.0, "b29": 76.6, "b32": 58.7})
    result = assemble_structure([proof, display, tail, second, third, third_display, fifth, heading], ASSETS, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0040-b10"] * 7 + ["p0040-b32"]


def test_items_that_open_their_own_group_at_a_page_top_leave_their_siblings_their_own() -> None:
    items = [
        _unit("p0038-b1", "(iv) {{asset:a}} *if* {{asset:b}} *is independent of* {{asset:c}}.", [78.8, 90.2, 400, 101]),
        _unit("p0038-b2", "(v) {{asset:d}} *if* {{asset:e}} *is* {{asset:f}}*-measurable.*", [81.8, 107.4, 400, 118]),
        _unit("p0038-b3", "(vi) *If* {{asset:g}} *is a sub-algebra, then* {{asset:h}}.", [78.8, 124.7, 400, 135]),
    ]
    plan = _plan(first_x={"b1": 78.8, "b2": 81.8, "b3": 78.8})
    result = assemble_structure(items, ASSETS, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0038-b1", "p0038-b2", "p0038-b3"]


def test_an_italic_paragraph_continues_an_italic_statement_and_upright_prose_ends_it() -> None:
    theorem = _unit("p0042-b1", "**Theorem 1.35** (Lévy’s continuity theorem). *Let* {{asset:a}} *be a sequence of probability measures on* {{asset:b}} *with characteristic functions* {{asset:c}}*. Assume that*", [58.7, 90.2, 400, 130])
    first = _unit("p0042-b2", "(1) {{asset:d}} *converges everywhere on* {{asset:e}} *to a limiting function* {{asset:f}}.", [82.1, 135.8, 400, 146])
    second = _unit("p0042-b3", "(2) {{asset:g}} *is continuous at* {{asset:h}}.", [82.1, 152.7, 400, 163])
    then = _unit("p0042-b4", "*Then there exists a probability distribution* {{asset:i}} *such that* {{asset:j}}*. Moreover* {{asset:k}} *is continuous.*", [58.7, 170.5, 400, 195])
    conversely = _unit("p0042-b6", "*Conversely, if* {{asset:l}}*, where* {{asset:m}} *is some probability distribution, then* {{asset:n}} *converges.*", [58.7, 202.9, 400, 250])
    remark = _unit("p0042-b8", "For a proof, see [**Chu01**].", [76.6, 257.1, 400, 268])
    plan = _plan(first_x={"b1": 58.7, "b2": 82.1, "b3": 82.1, "b4": 58.7, "b6": 76.6, "b8": 76.6},
                 blocks=[{"id": "b4", "paragraph_break": True}])
    result = assemble_structure([theorem, first, second, then, conversely, remark], ASSETS, plan, _make_unit)
    assert [u.unit_id for u in result] == ["p0042-b1", "p0042-b2", "p0042-b3", "p0042-b4", "p0042-b6", "p0042-b8"]
    assert [u.parent_id for u in result] == ["p0042-b1"] * 5 + ["p0042-b8"]
    assert _emphasised_share(theorem.source_text) > 0.6 > _emphasised_share(remark.source_text)
    # An upright statement (a definition, an example) is not continued by an indented paragraph.
    example = _unit("p0031-b1", "**Example 1.8** (Binomial distribution). The binomial distribution has the form", [58.7, 90.5, 400, 101])
    plain = _unit("p0031-b9", "*The* binomial distribution can be obtained from independent Bernoulli trials.", [76.6, 203.2, 400, 214])
    result = assemble_structure([example, plain], ASSETS, _plan(first_x={"b1": 58.7, "b9": 76.6}), _make_unit)
    assert [u.parent_id for u in result] == ["p0031-b1", "p0031-b9"]


def test_a_chunk_the_planner_read_as_an_items_continuation_returns_to_the_item() -> None:
    item = _unit("p0047-b24", "1.4. Numerically investigate the limit processes", [79.1, 478.5, 400, 489], page=47)
    row = _unit("p0047-b25", "Binomial {{asset:bin}} Poisson {{asset:poi}} Normal {{asset:nor}}", [108.2, 500.7, 400, 511], page=47)
    rest = _unit("p0047-b26", "when the parameters grow, by comparing the plots for different distributions.", [101.0, 523.2, 400, 534], page=47)
    plan = _plan(margin=101.0, first_x={"b24": 79.1, "b25": 108.2, "b26": 101.0},
                 list_items={"b24": {"label": "1.4.", "body_x": 101.0}, "b26": {"continues": "b24"}},
                 blocks=[{"id": "b25", "paragraph_break": True}, {"id": "b26", "paragraph_break": True}])
    result = assemble_structure([item, row, rest], ASSETS, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0047-b24", "p0047-b25", "p0047-b24"]


# --- LT-082 -------------------------------------------------------------------------------

def test_the_tombstone_of_a_labelled_display_reads_after_the_display() -> None:
    aid = "a-p0042-c01ac2b3bd24"
    assets = {aid: _asset(aid, "math", [125.6, 536.5, 304.0, 584.2], display=True)}
    proof = _make_unit(42, "p0042-b24", "**Proof.** Assume that f is a characteristic function. Then", [58.7, 500.4, 417.0, 538.3], assets)
    block = _make_unit(42, "p0042-b25", "□ (1.50)", [58.7, 551.4, 417.4, 562.6], assets)
    display = _make_unit(42, "p0042-b26", "{{asset:" + aid + "}}", [125.6, 536.5, 304.0, 584.2], assets)
    heading = _make_unit(42, "p0042-b36", "1.10. Generating Function and Cumulants", [58.7, 588.3, 300, 600], assets, kind="heading")
    separated = _separate_display_units([proof, block, display, heading], assets)
    assert [u.unit_id for u in separated] == ["p0042-b24", "p0042-b26", "p0042-b25", "p0042-b36"]
    assert separated[1].kind is UnitKind.EQUATION and separated[1].equation_number == "1.50"
    assert separated[2].source_text == "□"
    plan = _plan(first_x={"b24": 58.7, "b25": 408.9, "b26": 125.6, "b36": 58.7})
    result = assemble_structure(separated, assets, plan, _make_unit)
    assert [(u.unit_id, u.parent_id) for u in result] == [
        ("p0042-b24", "p0042-b24"), ("p0042-b26", "p0042-b24"), ("p0042-b25", "p0042-b24"), ("p0042-b36", "p0042-b36"),
    ]
    assert result[0].source_text.endswith("Then") and result[2].source_text == "□"
