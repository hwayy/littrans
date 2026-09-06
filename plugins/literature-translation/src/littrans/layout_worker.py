"""Standalone entry point for the pinned, isolated MinerU environment."""
from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    version = importlib.metadata.version("mineru")
    if version != "3.4.5":
        raise RuntimeError(f"Expected mineru==3.4.5, found {version}")
    from mineru.model.layout.pp_doclayoutv2 import PPDocLayoutV2LayoutModel
    from PIL import Image

    model = PPDocLayoutV2LayoutModel(weight=request["model"], device="cpu")
    pages = {}
    for name in request["images"]:
        with Image.open(name) as image:
            pages[name] = model.predict(image.convert("RGB"))
    Path(sys.argv[2]).write_text(json.dumps({"status": "ok", "version": version, "model": "PP-DocLayoutV2", "device": "cpu", "formula_decoder": None, "fingerprint": request["fingerprint"], "weights": request["weights"], "pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
