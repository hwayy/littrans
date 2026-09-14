"""Standalone entry point for the pinned, isolated MinerU environment."""
from __future__ import annotations

import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path


def _write_result(path: Path, payload: dict[str, object]) -> None:
    """Publish standalone worker JSON without exposing a partially written cache."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    _write_result(Path(sys.argv[2]), {"status": "ok", "version": version, "model": "PP-DocLayoutV2", "device": "cpu", "formula_decoder": None, "fingerprint": request["fingerprint"], "weights": request["weights"], "pages": pages})


if __name__ == "__main__":
    main()
