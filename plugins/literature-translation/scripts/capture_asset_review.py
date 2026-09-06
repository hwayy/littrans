"""Capture each asset-audit candidate with its original in isolated offline Chrome.

Usage: python capture_asset_review.py PROJECT PACKET_ID --chrome PATH_TO_CHROME
This creates rendering evidence only. It never imports an audit or accepts math.
Run in a context capable of launching Chrome's sandboxed subprocesses on Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# The plugin's scripts directory also contains littrans.py. Put its actual
# package first so directly executing this helper does not import that wrapper.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from littrans.representations import _load_packet, _review_html, install_mathjax  # noqa: E402
from littrans.storage import atomic_write_text, read_json, write_json  # noqa: E402


def capture(root: Path, packet_id: str, chrome: Path, width: int = 1280,
            height: int = 1800, device_scale: int = 2) -> dict[str, Any]:
    if device_scale < 1 or device_scale > 4:
        raise ValueError("device_scale must be between 1 and 4")
    root, chrome = root.resolve(), chrome.resolve()
    packet = _load_packet(root, packet_id, "asset-audit")
    # Chromium still encounters Windows MAX_PATH limits in cache/screenshot
    # subsystems even though Python accepts the longer evidence paths.
    output = root / "evidence/asset-captures" / packet_id[:16]
    output.resolve().relative_to(root)
    output.mkdir(parents=True, exist_ok=True)
    install_mathjax(output)
    prior_report = output / "capture-report.json"
    if prior_report.exists() and read_json(prior_report)["packet_id"] != packet_id:
        raise ValueError("Capture directory packet prefix collision")
    profile = Path(tempfile.gettempdir()) / "littrans-review-browser" / hashlib.sha256(str(root).encode()).hexdigest()[:16]
    results: list[dict[str, Any]] = []
    for asset in packet["assets"]:
        key = asset["id"]
        stem = re.sub(r"[^A-Za-z0-9._-]", "-", key)[:40] + "-" + hashlib.sha256(key.encode()).hexdigest()[:8]
        candidate = packet["candidates"][key]
        record_path = output / f"{stem}.capture.json"
        expected = {"packet_id": packet_id, "asset_id": key,
                    "candidate_sha256": candidate["candidate_sha256"],
                    "render_artifact_sha256": packet["render_artifact"]["sha256"],
                    "width": width, "height": height, "device_scale": device_scale, "chrome": str(chrome)}
        if record_path.is_file():
            prior = read_json(record_path)
            if (prior.get("input") == expected and prior.get("exit_code") == 0
                    and (root / prior["screenshot"]).is_file()
                    and hashlib.sha256((root / prior["screenshot"]).read_bytes()).hexdigest() == prior["screenshot_sha256"]):
                results.append(prior)
                continue
        page = output / f"{stem}.html"
        # A derived single-asset view uses precisely the packet's original and
        # candidate data, identical renderer and root-relative evidence paths.
        atomic_write_text(page, _review_html(root, output, {**packet, "assets": [asset]}))
        screenshot, dom = output / f"{stem}.png", output / f"{stem}.dom.html"
        command = [str(chrome), "--headless=new", "--disable-gpu", "--no-first-run",
                   "--no-default-browser-check", "--disable-background-networking",
                   "--host-resolver-rules=MAP * ~NOTFOUND", f"--user-data-dir={profile}",
                   f"--force-device-scale-factor={device_scale}",
                   f"--window-size={width},{height}", "--dump-dom", "--virtual-time-budget=10000",
                   f"--screenshot={screenshot}", page.as_uri()]
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60, check=False)
        atomic_write_text(dom, run.stdout)
        status = re.search(r'data-render-status="([^"]+)"', run.stdout)
        state = status.group(1) if status else "not-rendered"
        successful = (run.returncode == 0 and not candidate["validation_errors"]
                      and candidate["status"] != "unresolved"
                      and (state == "passed" if candidate["format"] == "latex" else bool(run.stdout)))
        record = {"input": expected, "exit_code": run.returncode,
                  "renderer_success": successful, "render_status": state,
                  "mathematical_fidelity": "not-evaluated-independent-visual-review-required",
                  "html": page.relative_to(root).as_posix(), "dom": dom.relative_to(root).as_posix(),
                  "screenshot": screenshot.relative_to(root).as_posix(),
                  "screenshot_sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest() if screenshot.is_file() else None,
                  "browser_stderr": run.stderr}
        write_json(record_path, record)
        results.append(record)
    report = {"packet_id": packet_id, "render_artifact": packet["render_artifact"],
              "captures": results, "renderer_success_count": sum(item["renderer_success"] for item in results),
              "acceptance_granted": False}
    write_json(output / "capture-report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("packet_id")
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1800)
    parser.add_argument("--device-scale", type=int, default=2)
    args = parser.parse_args()
    report = capture(args.project, args.packet_id, args.chrome, args.width, args.height, args.device_scale)
    print(json.dumps({"packet_id": report["packet_id"], "captures": len(report["captures"]),
                      "renderer_success_count": report["renderer_success_count"],
                      "acceptance_granted": False}))
