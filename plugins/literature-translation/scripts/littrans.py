"""Run the bundled package from a source checkout without installing it first."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

try:
    from littrans.cli import app  # noqa: E402
except ModuleNotFoundError:
    from bootstrap import main

    if __name__ == "__main__":
        main()
else:
    if __name__ == "__main__":
        app()
