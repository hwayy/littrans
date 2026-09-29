from __future__ import annotations

import importlib
import multiprocessing
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from littrans import external_review


def _hold_lock(path: str, connection: Connection) -> None:
    try:
        with external_review._os_file_lock(Path(path), 2) as acquired:
            connection.send(acquired)
            if acquired:
                connection.recv()
    finally:
        connection.close()


@pytest.fixture
def held_lock(tmp_path: Path):
    path = tmp_path / "shared.oslock"
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_hold_lock, args=(str(path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(15), "Lock holder did not start"
        assert parent.recv() is True
        yield path, process, parent
    finally:
        if process.is_alive():
            process.terminate()
        process.join(10)
        parent.close()
        assert not process.is_alive()
        process.close()


def test_empty_file_lock_excludes_other_process_and_times_out(held_lock) -> None:
    path, _, _ = held_lock
    assert path.stat().st_size == 0
    with external_review._os_file_lock(path, .05) as acquired:
        assert acquired is False
    with external_review._os_file_lock(path.with_name("independent.oslock"), 0) as acquired:
        assert acquired is True
    assert path.stat().st_size == 0


@pytest.mark.parametrize("terminate", [False, True], ids=["normal-exit", "terminated"])
def test_process_exit_releases_lock(held_lock, terminate: bool) -> None:
    path, process, connection = held_lock
    if terminate:
        process.terminate()
    else:
        connection.send("release")
    process.join(10)
    assert not process.is_alive()
    if not terminate:
        assert process.exitcode == 0
    with external_review._os_file_lock(path, 2) as acquired:
        assert acquired is True
    assert path.is_file() and path.stat().st_size == 0


def test_unbounded_wait_enters_only_after_other_process_releases(
    held_lock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, process, connection = held_lock
    waiting = threading.Event()
    real_sleep = external_review.time.sleep
    deadline = external_review.time.monotonic() + 10

    def observed_sleep(seconds):
        if external_review.time.monotonic() >= deadline:
            raise TimeoutError("Contender failed to acquire after holder release")
        waiting.set()
        real_sleep(seconds)

    monkeypatch.setattr(external_review.time, "sleep", observed_sleep)

    def wait_for_lock():
        with external_review._os_file_lock(path, None) as acquired:
            return acquired

    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(wait_for_lock)
        try:
            assert waiting.wait(5), "Contender did not reach lock contention"
            assert not future.done()
        finally:
            connection.send("release")
        assert future.result(timeout=10) is True
    process.join(10)
    assert process.exitcode == 0


@pytest.mark.parametrize("content", [b"", b"\0", b"legacy-lock"])
def test_lock_preserves_file_and_releases_after_base_exception(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "existing.oslock"
    path.write_bytes(content)
    identity = path.stat().st_ino
    with pytest.raises(KeyboardInterrupt, match="body failed"):
        with external_review._os_file_lock(path) as acquired:
            assert acquired is True
            raise KeyboardInterrupt("body failed")
    with external_review._os_file_lock(path, 0) as acquired:
        assert acquired is True
    assert path.read_bytes() == content
    assert path.stat().st_ino == identity


def test_unlock_error_still_closes_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "unlock.oslock"
    real_open = Path.open
    handles = []

    def tracked_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        handles.append(handle)
        return handle

    backend = importlib.import_module("msvcrt" if sys.platform == "win32" else "fcntl")
    name = "locking" if sys.platform == "win32" else "flock"
    unlock = backend.LK_UNLCK if sys.platform == "win32" else backend.LOCK_UN
    real_lock = getattr(backend, name)

    def fail_unlock(fd, mode, *args):
        if mode == unlock:
            raise OSError("unlock failed")
        return real_lock(fd, mode, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", tracked_open)
        patch.setattr(backend, name, fail_unlock)
        with pytest.raises(OSError, match="unlock failed"):
            with external_review._os_file_lock(path) as acquired:
                assert acquired is True
        assert len(handles) == 1 and handles[0].closed
    with external_review._os_file_lock(path, 0) as acquired:
        assert acquired is True
