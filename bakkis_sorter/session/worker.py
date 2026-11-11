"""Background file operation worker thread and move/copy primitives."""

from __future__ import annotations

import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional


def _move(src: Path, dest: Path) -> None:
    shutil.move(str(src), str(dest))


def _copy(src: Path, dest: Path) -> None:
    shutil.copy2(str(src), str(dest))


def _is_empty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size == 0
    except OSError:
        return False


class Worker:
    """Single daemon thread executing jobs in FIFO order."""

    def __init__(self, on_error: Optional[Callable[[Exception], None]] = None) -> None:
        self._on_error = on_error
        self._q: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="bakkis-fileops", daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], None]) -> None:
        self._q.put(job)

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Block until every submitted job has finished. False if ``timeout`` ran out first."""
        q = self._q
        with q.all_tasks_done:
            if timeout is None:
                while q.unfinished_tasks:
                    q.all_tasks_done.wait()
                return True
            end = time.monotonic() + timeout
            while q.unfinished_tasks:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                q.all_tasks_done.wait(left)
            return True

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                job()
            except Exception as e:  # noqa: BLE001 - one bad job must never stop all later file ops
                if self._on_error is not None:
                    try:
                        self._on_error(e)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                self._q.task_done()
