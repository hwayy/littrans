from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import littrans.storage as storage


def _denied(path: Path, winerror: int) -> PermissionError:
    error = PermissionError(13, "Access is denied", str(path))
    error.winerror = winerror
    return error


def test_windows_lock_handoff_retries_without_entering_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir = Path.mkdir
    attempts, sleeps = [], []
    def transient(path: Path, *args, **kwargs):
        if path.name == ".littrans-write-lock":
            attempts.append(path)
            if len(attempts) < 3:
                raise _denied(path, 5)
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(Path, "mkdir", transient)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="body failed"):
        with storage.project_write_lock(tmp_path):
            assert len(attempts) == 3
            assert (tmp_path / ".littrans-write-lock").is_dir()
            raise RuntimeError("body failed")
    assert sleeps == [.05, .05]
    assert not (tmp_path / ".littrans-write-lock").exists()


def test_persistent_windows_denial_keeps_original_error_at_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _denied(tmp_path / ".littrans-write-lock", 5)
    calls, clock = [], [-1.0]
    def denied(path: Path, *args, **kwargs):
        calls.append(path)
        raise expected
    def monotonic():
        clock[0] += 1
        return clock[0]
    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(Path, "mkdir", denied)
    monkeypatch.setattr(storage.time, "monotonic", monotonic)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError) as failure:
        with storage.project_write_lock(tmp_path, timeout_seconds=2):
            pytest.fail("Denied lock must never enter its critical section")
    assert failure.value is expected
    assert failure.value.filename == str(tmp_path / ".littrans-write-lock")
    assert len(calls) == 2


@pytest.mark.parametrize(("host", "winerror"), [("nt", 32), ("posix", 5)])
def test_unrelated_lock_permission_errors_are_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, winerror: int) -> None:
    expected = _denied(tmp_path / ".littrans-write-lock", winerror)
    def denied(path: Path, *args, **kwargs):
        raise expected
    monkeypatch.setattr(storage, "os", SimpleNamespace(name=host))
    monkeypatch.setattr(Path, "mkdir", denied)
    monkeypatch.setattr(storage.time, "sleep", lambda _: pytest.fail("Unrelated errors must not be hidden"))
    with pytest.raises(PermissionError) as failure:
        with storage.project_write_lock(tmp_path):
            pytest.fail("Denied lock must never enter its critical section")
    assert failure.value is expected


def test_real_threads_preserve_mutual_exclusion_during_directory_handoffs(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    counter_guard = threading.Lock()
    active = maximum = completed = 0
    def worker():
        nonlocal active, maximum, completed
        barrier.wait(timeout=5)
        for _ in range(300):
            with storage.project_write_lock(tmp_path):
                with counter_guard:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(.0001)
                with counter_guard:
                    active -= 1
                    completed += 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    assert maximum == 1 and completed == 600 and active == 0
    assert not (tmp_path / ".littrans-write-lock").exists()
