"""Spot tracks that are already sorted: same title + artist and about the same length.

The index covers ``BAKKIS_SORTED`` and ``BAKKIS_SORTED_COPIES`` of the loaded folder. It is built on a background thread and
updated as sorts complete. It only warns; it never blocks a decision.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from . import library
from .metadata import TrackMeta

DURATION_TOLERANCE = 2.0  # seconds
_NOISE_RE = re.compile(r"\((?:original|extended)(?: mix| version)?\)|[^\w\s]", re.IGNORECASE)


def norm(text: Optional[str]) -> str:
    """Comparable form: lowercase, no punctuation, no "(Original Mix)" suffix, single spaces."""
    if not text:
        return ""
    return " ".join(_NOISE_RE.sub(" ", text.lower()).split())


def key_for(meta: TrackMeta) -> Optional[tuple[str, str]]:
    title, artist = norm(meta.title), norm(meta.artist)
    if not title or not artist:
        return None  # file-name guesses are too loose to call something a duplicate
    return title, artist


class DuplicateIndex:
    def __init__(self, music_root: Path, get_meta: Callable[[Path], TrackMeta]) -> None:
        self.music_root = Path(music_root).resolve()
        self._get_meta = get_meta
        self._lock = threading.Lock()
        self._by_key: dict[tuple[str, str], list[tuple[Path, float]]] = {}
        self.ready = threading.Event()
        self._cancel = threading.Event()

    def start(self) -> "DuplicateIndex":
        threading.Thread(target=self._build, name="bakkis-dupes", daemon=True).start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def _build(self) -> None:
        try:
            for root in library.tree_roots(self.music_root):
                for dirpath, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for name in files:
                        if self._cancel.is_set():
                            return
                        p = Path(dirpath, name)
                        if not name.startswith(".") and library.is_audio_path(p):
                            self.add(p)
        finally:
            self.ready.set()

    def add(self, path: Path) -> None:
        try:
            meta = self._get_meta(path)
        except Exception:  # noqa: BLE001 - unreadable files are simply not indexed
            return
        k = key_for(meta)
        if k is None:
            return
        with self._lock:
            entries = self._by_key.setdefault(k, [])
            if all(p != path for p, _ in entries):
                entries.append((Path(path), meta.duration))

    def find(self, path: Path, meta: TrackMeta) -> Optional[dict]:
        """The first sorted copy of this track, as {"path", "folder"}; None if it looks new."""
        k = key_for(meta)
        if k is None:
            return None
        with self._lock:
            entries = list(self._by_key.get(k, ()))
        for p, dur in entries:
            if p == Path(path):
                continue
            if meta.duration and dur and abs(meta.duration - dur) > DURATION_TOLERANCE:
                continue
            if not p.exists():
                continue
            tree = library.tree_of(p.parent)
            rel = Path(library.relative_posix(p, tree)) if tree else Path(p.name)
            parts = [x for x in rel.parent.parts if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x)]
            # "<preset>/<folder>" -> "Keep · Quick Sort"
            folder = " · ".join(reversed(parts[:2])) if parts else rel.parent.as_posix()
            if tree is not None and tree.name == library.COPIES_NAME:
                folder += " (copies)"
            return {"path": str(p), "folder": folder}
        return None
