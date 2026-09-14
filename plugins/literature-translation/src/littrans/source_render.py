"""Human-readable checkpoint of the preserved source before translation.

`source render` lays out the verified source units in reading order with the
high-resolution original assets embedded inline, so a person can read the
extraction as a document and spot structural or boundary defects that the
overlay report does not make obvious. It changes no project state.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from littrans.extractor import parse_page_spec
from littrans.fidelity_models import asset_reference_ids, load_assets
from littrans.models import RenderPolicy, SourceUnit, UnitKind
from littrans.rendering import _safe_name, _unit_html
from littrans.representations import resolve_asset_html
from littrans.storage import atomic_write_text, load_project, read_json, read_jsonl
from littrans.verification import verify_extraction

STYLE = """
:root { color-scheme: light dark; --paper:#f8f5ee; --ink:#20201e; --muted:#6b6b65; --line:#d6d0c4; --accent:#315d7d; --warn:#b45309; --warn-bg:#fff2c9; --ok:#15803d; }
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font-family:"Segoe UI","Noto Sans SC",sans-serif; }
header { position:sticky; top:0; z-index:2; padding:.9rem 4vw; background:color-mix(in srgb,var(--paper) 94%,transparent); border-bottom:1px solid var(--line); backdrop-filter:blur(6px); }
header h1 { margin:0 0 .3rem; font:600 clamp(1.2rem,2.6vw,1.8rem)/1.2 Georgia,serif; }
header p { margin:0; color:var(--muted); font-size:.9rem; }
.status { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.8rem; font-weight:600; }
.status.ok { background:color-mix(in srgb,var(--ok) 18%,transparent); color:var(--ok); }
.status.warn { background:var(--warn-bg); color:var(--warn); }
main { width:min(980px,94vw); margin:1.5rem auto 5rem; }
html { scroll-padding-top:7rem; }
.summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); gap:.75rem; margin:0 0 1.5rem; }
.summary div { padding:.6rem .8rem; border:1px solid var(--line); border-radius:.5rem; font-size:.85rem; color:var(--muted); }
.summary strong { display:block; font-size:1.25rem; color:var(--ink); }
.attention { margin:0 0 1.5rem; padding:.8rem 1rem; border-left:4px solid var(--warn); background:var(--warn-bg); color:#2d2a20; font-size:.9rem; }
.attention ul { margin:.4rem 0 0; padding-left:1.2rem; }
.page-break { margin:2.5rem 0 1rem; display:flex; align-items:center; gap:1rem; color:var(--muted); font-size:.85rem; }
.page-break::before,.page-break::after { content:""; flex:1; border-top:1px dashed var(--line); }
.page-break a { color:var(--accent); }
.group { margin:0 0 1.15rem; padding:0 0 0 .9rem; border-left:3px solid transparent; }
.group.multi { border-left-color:color-mix(in srgb,var(--accent) 45%,transparent); }
.unit { position:relative; margin:.35rem 0; }
.unit .meta { display:block; margin:0 0 .1rem; color:var(--muted); font:11px/1.4 ui-monospace,monospace; opacity:.75; }
.unit:hover .meta { opacity:1; }
.body { font:17.5px/1.72 Georgia,"Noto Serif SC",serif; overflow-wrap:anywhere; }
.body p { margin:.15rem 0; }
.body h2 { margin:.6rem 0 .3rem; font:700 1.35em/1.25 Georgia,serif; }
.kind-heading .body h2 { font-size:1.5em; }
.kind-list_item .body ul,.kind-list_item .body ol { margin:.1rem 0; padding-left:1.6rem; }
.kind-list_item + .kind-list_item { margin-top:-.1rem; }
.kind-equation .body { text-align:center; padding:.35rem 2.5rem; }
.kind-equation .fidelity-complex { position:relative; }
.kind-equation .fidelity-complex > .equation-number { display:inline; position:absolute; right:.25rem; top:50%; transform:translateY(-50%); }
figure { margin:.6rem 0; text-align:center; }
figure img { max-width:100%; height:auto; background:white; border:1px solid var(--line); }
figcaption { margin:.35rem 0; font-size:.95em; color:var(--muted); font-style:italic; text-align:center; }
.body .fidelity-asset { display:inline-block; max-width:100%; vertical-align:baseline; }
.body .fidelity-asset[data-display="true"] { display:block; overflow-x:auto; margin:.4em auto; text-align:center; }
.body .fidelity-asset img { background:white; vertical-align:middle; }
.fidelity-complex { position:relative; overflow-x:auto; }
.fidelity-complex > .equation-number { display:block; text-align:right; }
.kind-equation .display-line { padding-right:3.5rem; }
.kind-equation .display-line .fidelity-asset[data-display="true"] { display:inline-block; margin:.2em .4em; vertical-align:middle; }
.fidelity-complex.display-line.multirow { display:flex; align-items:center; justify-content:center; gap:.3em; }
.display-rows { display:inline-flex; flex-direction:column; align-items:flex-start; text-align:left; }
.display-row { display:block; }
.display-lead .fidelity-asset[data-display="true"] { display:inline-block; margin:0; }
.omitted { margin:.5rem 0 0; padding:.4rem .8rem; border:1px dashed var(--line); border-radius:.4rem; color:var(--muted); font-size:.8rem; }
.omitted code { font-size:.8rem; }
pre { margin:.25rem 0; padding:1rem; overflow:auto; border:1px solid var(--line); border-radius:.4rem; }
table { border-collapse:collapse; font:15px/1.45 "Segoe UI",sans-serif; } th,td { padding:.45rem .6rem; border:1px solid var(--line); }
a { color:var(--accent); }
@media (prefers-color-scheme: dark) { :root { --paper:#181a1b; --ink:#e8e4dc; --muted:#aaa69e; --line:#3b3d3e; --accent:#8ec4ea; --warn-bg:#403619; --ok:#6fcf8a; } .attention { color:var(--ink); } }
"""


def _page_ledger(root: Path, page: int) -> dict[str, Any]:
    path = root / f"derived/fidelity-pages/p{page:04d}.json"
    return read_json(path) if path.is_file() else {}


def _unit_body(root: Path, unit: SourceUnit, output: Path, unit_map: dict[str, SourceUnit] | None = None) -> str:
    text = unit.source_markdown or unit.source_text
    body = _unit_html(unit, text, source_view=True, unit_map=unit_map)
    if unit.kind is UnitKind.FIGURE and "<figure" not in body:
        body = "<figure>" + body + "</figure>"
    return resolve_asset_html(root, body, output, originals_only=True)


def _groups(units: list[SourceUnit]) -> list[list[SourceUnit]]:
    groups: list[list[SourceUnit]] = []
    for unit in units:
        key = unit.parent_id or unit.unit_id
        if groups and (groups[-1][0].parent_id or groups[-1][0].unit_id) == key and groups[-1][0].page == unit.page:
            groups[-1].append(unit)
        else:
            groups.append([unit])
    return groups


def _embed_assets(document: str, output: Path) -> str:
    """Inline the referenced asset images as data URIs for a single shareable file."""
    import base64
    import mimetypes

    def replace(match: re.Match[str]) -> str:
        attribute, relative = match[1], html.unescape(match[2])
        path = output / relative
        if not path.is_file():
            return match[0]
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".svg":
            mime = "image/svg+xml"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{attribute}="data:{mime};base64,{payload}"'

    return re.sub(r'(src|data-original-fallback)="(original-assets/[^"]+)"', replace, document)


def render_source_review(root: Path, page_spec: str = "all", name: str | None = None, *, standalone: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    pages = parse_page_spec(page_spec, config.source_pages)
    all_units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    unit_map = {u.unit_id: u for u in all_units}
    units = [u for u in all_units if u.page in pages]
    selected_with_notes = {unit.unit_id for unit in units}
    while True:
        expanded = selected_with_notes | {ref for unit in all_units if unit.unit_id in selected_with_notes for ref in unit.footnote_refs}
        if expanded == selected_with_notes:
            break
        selected_with_notes = expanded
    units = [unit for unit in all_units if unit.unit_id in selected_with_notes]
    pages = sorted(set(pages) | {unit.page for unit in units})
    if not units:
        raise ValueError(f"No prepared source units for pages {page_spec}; run source prepare first")
    assets = load_assets(root)
    verification = verify_extraction(root, page_spec)
    output = root / "output"
    label = _safe_name(name) if name is not None else f"source-p{min(pages):04d}-p{max(pages):04d}"
    html_path = output / f"{label}.html"
    if not html_path.resolve().is_relative_to(output.resolve()):
        raise ValueError("Source render output must stay inside the output directory")
    output.mkdir(parents=True, exist_ok=True)

    referenced = {aid for u in units for aid in asset_reference_ids(u.source_markdown or u.source_text)}
    export_methods = Counter(assets[aid].fragments[0].export_method for aid in referenced if aid in assets)
    kinds = Counter(u.kind.value for u in units if u.render_policy is RenderPolicy.INCLUDE)
    attention: list[str] = []
    for error in verification.get("errors", []):
        attention.append("verify: " + html.escape(json.dumps(error, ensure_ascii=False)))
    # One line per page, not per asset: hundreds of identical notices would push the
    # body far below the fold and make the checkpoint unusable for visual review.
    pending_by_page: dict[int, list[str]] = {}
    for aid in sorted(referenced):
        asset = assets.get(aid)
        if asset is not None and asset.grouping_pending:
            pending_by_page.setdefault(asset.fragments[0].page, []).append(f"<code>{html.escape(aid)}</code> ({html.escape(asset.kind)})")
    for page, pending in sorted(pending_by_page.items()):
        attention.append(f"page {page}: {len(pending)} asset(s) with a pending grouping decision "
                         f"<details><summary>list</summary>{', '.join(pending)}</details>")
    for page in pages:
        ledger = _page_ledger(root, page)
        if ledger and ledger.get("layout_status") != "ok":
            attention.append(f"page {page}: layout detector {html.escape(str(ledger.get('layout_status')))} — {html.escape(str(ledger.get('layout_reason')))}")
        if ledger and not (root / f"evidence/pages/fidelity-p{page:04d}.review.json").is_file():
            attention.append(f"page {page}: no approved source review receipt yet")

    sections: list[str] = []
    current_page: int | None = None
    for group in _groups(units):
        page = group[0].page
        if page != current_page:
            current_page = page
            page_png = root / f"evidence/pages/fidelity-p{page:04d}.png"
            link = f' <a href="{html.escape(page_png.resolve().as_uri())}">original page image</a>' if page_png.is_file() else ""
            sections.append(f'<div class="page-break" id="page-{page}">PDF page {page}{link}</div>')
            omitted = [u for u in all_units if u.page == page and u.render_policy is RenderPolicy.OMIT]
            if omitted:
                items = "".join(
                    f"<li><code>{html.escape(u.unit_id)}</code> ({html.escape(u.kind.value)}): {html.escape((u.source_markdown or u.source_text)[:120])}</li>"
                    for u in omitted
                )
                sections.append(f'<details class="omitted"><summary>{len(omitted)} omitted running-material unit(s) on this page — confirm none is body content</summary><ul>{items}</ul></details>')
        visible = [u for u in group if u.render_policy is RenderPolicy.INCLUDE]
        if not visible:
            continue
        articles = []
        for unit in visible:
            meta = f"{html.escape(unit.unit_id)} · {html.escape(unit.kind.value)}"
            if unit.equation_number:
                meta += f" · ({html.escape(unit.equation_number)})"
            if unit.continues_from_previous:
                meta += " · continues previous page"
            if unit.continued_to_next:
                meta += " · continues on next page"
            if unit.footnote_refs:
                meta += " · footnotes " + ", ".join(html.escape(r) for r in unit.footnote_refs)
            articles.append(
                f'<article class="unit kind-{html.escape(unit.kind.value)}" id="{html.escape(unit.unit_id)}">'
                f'<span class="meta">{meta}</span><div class="body">{_unit_body(root, unit, output, unit_map)}</div></article>'
            )
        multi = " multi" if len(visible) > 1 else ""
        sections.append(f'<section class="group{multi}">' + "".join(articles) + "</section>")

    status = ('<span class="status ok">source verified</span>' if verification.get("passed")
              else '<span class="status warn">source not verified</span>')
    page_label = f"{min(pages)}–{max(pages)}" if len(set(pages)) == max(pages) - min(pages) + 1 else "、".join(map(str, sorted(set(pages))))
    summary = "".join(
        f"<div><strong>{value}</strong>{html.escape(key)}</div>"
        for key, value in [
            ("units", sum(kinds.values())),
            *[(f"{kind}", count) for kind, count in sorted(kinds.items())],
            ("assets referenced", len(referenced)),
            *[(f"assets: {method}", count) for method, count in sorted(export_methods.items())],
        ]
    )
    attention_html = ""
    if attention:
        attention_html = '<div class="attention"><strong>Needs attention before translation</strong><ul>' + "".join(f"<li>{item}</li>" for item in attention) + "</ul></div>"
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(config.title)} — source checkpoint</title><style>{STYLE}</style></head><body>"
        f"<header><h1>{html.escape(config.title)}</h1><p>Source checkpoint · PDF pages {html.escape(page_label)} · {status}"
        f' · <a href="{html.escape(config.source(root).as_uri())}">PDF</a></p></header><main>'
        f'<div class="summary">{summary}</div>{attention_html}' + "".join(sections) + "</main></body></html>"
    )
    if standalone:
        document = _embed_assets(document, output)
    atomic_write_text(html_path, document)
    return {
        "html": str(html_path),
        "standalone": standalone,
        "pages": pages,
        "units": sum(kinds.values()),
        "kinds": dict(sorted(kinds.items())),
        "assets": len(referenced),
        "asset_export_methods": dict(sorted(export_methods.items())),
        "source_verified": bool(verification.get("passed")),
        "attention": len(attention),
    }
