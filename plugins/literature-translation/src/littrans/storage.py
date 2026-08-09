from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from littrans.models import PROJECT_SCHEMA_VERSION, ProjectConfig, utc_now

ModelT = TypeVar("ModelT", bound=BaseModel)


PROJECT_DIRS = (
    "source",
    "derived/assets",
    "context/chapters",
    "glossary",
    "batches",
    "translations",
    "reviews",
    "qa",
    "evidence/pages",
    "evidence/audits",
    "packets",
    "output",
    "overrides",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        for attempt in range(7):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(0.02 * (2**attempt))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@contextmanager
def project_write_lock(root: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Serialize project mutations across independent agent processes."""
    lock_dir = root / ".littrans-write-lock"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for project write lock: {lock_dir}. "
                    "Remove it only after confirming no littrans process is running."
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


def read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return payload


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    if not path.exists():
        return []
    records: list[ModelT] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(model.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"Invalid record at {path}:{number}: {exc}") from exc
    return records


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    text = "".join(record.model_dump_json(exclude_none=True) + "\n" for record in records)
    atomic_write_text(path, text)


def append_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    addition = "".join(record.model_dump_json(exclude_none=True) + "\n" for record in records)
    atomic_write_text(path, existing + addition)


def initialize_project_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for directory in PROJECT_DIRS:
        (root / directory).mkdir(parents=True, exist_ok=True)


def load_project(root: Path) -> ProjectConfig:
    return ProjectConfig.model_validate(read_yaml(root / "project.yaml"))


def require_current_project_schema(
    root: Path, operation: str
) -> ProjectConfig:
    config = load_project(root)
    if config.schema_version != PROJECT_SCHEMA_VERSION:
        raise ValueError(
            f"{operation} requires project schema v{PROJECT_SCHEMA_VERSION}; "
            "run `littrans project migrate PROJECT --to 4` first "
            f"(current schema: v{config.schema_version})"
        )
    return config


def save_project(root: Path, config: ProjectConfig) -> None:
    config.updated_at = utc_now()
    write_yaml(root / "project.yaml", config.model_dump(mode="json", exclude_none=True))


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]
