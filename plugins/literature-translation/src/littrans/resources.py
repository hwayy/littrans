"""Locate portable instruction resources in either a plugin tree or a wheel."""
from pathlib import Path


def resource_root() -> Path:
    for root in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parent):
        if (root / "roles" / "translator.md").is_file():
            return root
    raise FileNotFoundError("LitTrans role resources are missing; reinstall this build")
