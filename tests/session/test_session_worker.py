"""Targeted unit tests for background Worker thread and move/copy primitives."""

import time
from pathlib import Path
import pytest

from bakkis_sorter.session.worker import Worker, _copy, _is_empty, _move


def test_worker_fifo_execution():
    events = []
    w = Worker()
    w.submit(lambda: events.append("first"))
    w.submit(lambda: events.append("second"))
    w.submit(lambda: events.append("third"))
    assert w.flush(timeout=5) is True
    assert events == ["first", "second", "third"]
    w.stop()


def test_worker_error_callback():
    errors = []
    w = Worker(on_error=errors.append)

    def bad():
        raise RuntimeError("boom")

    w.submit(bad)
    assert w.flush(timeout=5) is True
    assert len(errors) == 1
    assert str(errors[0]) == "boom"
    w.stop()


def test_file_primitives(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    assert not _is_empty(src)

    empty = tmp_path / "empty.txt"
    empty.touch()
    assert _is_empty(empty)

    dest_copy = tmp_path / "dest_copy.txt"
    _copy(src, dest_copy)
    assert dest_copy.exists()
    assert dest_copy.read_text() == "hello"

    dest_move = tmp_path / "dest_move.txt"
    _move(src, dest_move)
    assert dest_move.exists()
    assert not src.exists()
