"""Optional real-PDF smoke check in a new directory outside the repository.

Uses the project's recorded detector results, never its manual source overrides.
No approval is imported. Copyrighted images stay in the disposable directory.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pymupdf as fitz
from littrans import fidelity
from littrans.extractor import parse_page_spec
from littrans.project import initialize_project
from littrans.source_render import render_source_review
from littrans.storage import load_project, read_json, write_json, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages", default="32-33,35,45-47,52,66,69-70,75,82,104,116,135,140,151,153-154,180,183,200,234")
    args = parser.parse_args()
    source, root = args.source_project.resolve(), args.output.resolve()
    if root.exists() or root.is_relative_to(Path(__file__).resolve().parents[1]) or root.is_relative_to(source):
        raise ValueError("output must be new and outside the repository and original project")
    old = load_project(source)
    config = initialize_project(old.source(source), root, "technical-book", scaffold=False)
    profile = source / "context/source-structure.json"
    if profile.is_file():
        shutil.copyfile(profile, root / "context/source-structure.json")
    pages = parse_page_spec(args.pages, config.source_pages)
    units, assets, records = [], {}, []
    with fitz.open(config.source(root)) as document:
        for number in pages:
            original = read_json(source / f"derived/fidelity-pages/p{number:04d}.json")
            shutil.copyfile(source / original["page_image"], root / original["page_image"])
            layout = fidelity._cached_layout(source, original)
            page_units, page_assets, ledger = fidelity._page_prepare(root, document, number, config.source_sha256, layout)
            assets.update({a.id: a for a in page_assets})
            page_units = fidelity._continue_numbered_container(units, page_units, assets)
            units.extend(page_units)
            write_json(root / f"derived/fidelity-pages/p{number:04d}.json", ledger)
            if layout.get("fingerprint"):
                write_json(root / f"derived/fidelity-layout/{layout['fingerprint']}.json", layout)
            records.append({"page": number, "units": [u.model_dump(mode="json") for u in page_units],
                            "assets": [a.model_dump(mode="json") for a in page_assets]})
            print(json.dumps({"page": number, "units": len(page_units), "assets": len(page_assets)}), flush=True)
    write_jsonl(root / "derived/units.jsonl", units)
    write_jsonl(root / "derived/fidelity-assets.jsonl", assets.values())
    write_json(root / "observations.json", records)
    selection = ",".join(map(str, pages))
    scan = fidelity.scan_layout(root, selection)
    render = render_source_review(root, selection)
    write_json(root / "validation.json", {"scan": scan, "render": render, "approved": False})
    print(json.dumps({"output": str(root), "scan": scan["layout_summary"], "render": render}), flush=True)


if __name__ == "__main__":
    main()
