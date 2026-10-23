"""Discover mode (internally "listen"): play through unsorted and/or sorted folders without sorting.

Playing never moves or copies audio files. Hearted tracks are remembered in the config dir
(``hearts.json``) until you turn them into a folder; that step copies, so originals stay put.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import library, playlists
from .config import config_dir, safe_segment, write_json_atomic
from .metadata import TrackMeta
from .session import Journal, Track, new_track

HEARTS_JSON = "hearts.json"
DISCOVER_DIR = "Discover"


def _tree_key(tree: Path) -> str:
    return "copies" if tree.name == library.COPIES_NAME else "sorted"


def sorted_sources(music_root: Path) -> list[dict]:
    """Every sorted destination folder as a listen source: {key, label, count}."""
    out = []
    for _name, folder, tree in playlists.destination_folders(music_root):
        count = len(playlists._audio_files(folder))  # noqa: SLF001 - shared helper
        rel = folder.relative_to(tree)
        parts = list(rel.parts)  # <preset>[/<date>]/<folder>[/<preset>/<folder> on a second pass]
        label = " · ".join([parts[-1]] + parts[:-1]) + (" (copies)" if _tree_key(tree) == "copies" else "")
        out.append({"key": f"{_tree_key(tree)}:{rel.as_posix()}", "label": label, "count": count})
    return out


def resolve_source(music_root: Path, key: str) -> Optional[Path]:
    """Folder for a ``sorted:`` or ``copies:`` key, refusing anything outside that tree."""
    kind, sep, rel = key.partition(":")
    if not sep or kind not in ("sorted", "copies"):
        return None
    root = library.sorted_root(music_root) if kind == "sorted" else library.copies_root(music_root)
    folder = (root / rel).resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        return None
    return folder if folder.is_dir() else None


class ListenSession:
    def __init__(self, items: Iterable[tuple[Path, str]], shuffle: bool = True, rng: Optional[random.Random] = None) -> None:
        seen: set = set()
        self.tracks: list[Track] = []
        self.source_of: dict[str, str] = {}
        for path, source in items:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            t = new_track(path)
            self.tracks.append(t)
            self.source_of[t.id] = source
        if shuffle:
            (rng or random).shuffle(self.tracks)
        self.by_id = {t.id: t for t in self.tracks}

    def track_path(self, track_id: str) -> Optional[Path]:
        t = self.by_id.get(str(track_id))
        return t.path if t else None

    def public(self, music_root: Path, hearted: Optional[set] = None) -> list[dict]:
        hearted = hearted or set()
        return [
            {
                "id": t.id,
                "name": t.path.name,
                "folder": library.source_folder_display(t.path, music_root),
                "source": self.source_of[t.id],
                "rel": library.relative_posix(t.path, music_root),
                "hearted": str(t.path) in hearted,
            }
            for t in self.tracks
        ]


# --------------------------------------------------------------------------- hearts


class Hearts:
    """Hearted tracks by path, kept across launches until they are made into a folder or cleared."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else config_dir() / HEARTS_JSON
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
        except (OSError, ValueError):
            items = None
        self.items: list[dict] = [i for i in (items or []) if isinstance(i, dict) and i.get("path")]

    def _save(self) -> None:
        write_json_atomic(self.path, {"version": 1, "items": self.items})

    def has(self, path: Path) -> bool:
        return any(i["path"] == str(path) for i in self.items)

    def set(self, path: Path, on: bool, source: str = "") -> bool:
        """Heart or un-heart a track. Returns the new state."""
        key = str(path)
        if on and not self.has(path):
            self.items.append({"path": key, "name": Path(key).name, "source": source, "added": time.time()})
        elif not on:
            self.items = [i for i in self.items if i["path"] != key]
        self._save()
        return on

    def clear(self) -> None:
        self.items = []
        self._save()

    def public(self, resolve: Callable[[Path], Optional[Path]]) -> list[dict]:
        out = []
        for i in self.items:
            now = resolve(Path(i["path"]))
            out.append({**i, "current": str(now) if now else None, "missing": now is None})
        return out


def current_location(path: Path, journal: Journal) -> Optional[Path]:
    """Where a file is now: its path, or where a later sort moved it (from the journal)."""
    if path.exists():
        return path
    moved: dict = {}
    for day_file in sorted(journal.directory.glob("*.jsonl")) if journal.directory.is_dir() else []:
        try:
            lines = day_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("op") != "move" or not e.get("src") or not e.get("dest"):
                continue
            if e.get("event") == "sort":
                moved[e["src"]] = e["dest"]
            elif e.get("event") in ("undo", "restore"):
                moved.pop(e["src"], None)
    seen = set()
    cur = str(path)
    while cur in moved and cur not in seen:  # follow chains (sorted, put back, sorted again)
        seen.add(cur)
        cur = moved[cur]
    p = Path(cur)
    return p if p.exists() else None


def create_folder(music_root: Path, hearts: Hearts, journal: Journal, name: Optional[str] = None) -> dict:
    """Copy every hearted track into BAKKIS_SORTED_COPIES/Discover/<name>. Never overwrites; originals stay."""
    base = library.copies_root(music_root) / DISCOVER_DIR
    leaf = safe_segment(name or f"{date.today().isoformat()} Hearted", "Hearted")
    target = base / leaf
    n = 2
    while target.exists():
        target = base / f"{leaf} ({n})"
        n += 1
    copied, missing = [], []
    for item in hearts.items:
        src = current_location(Path(item["path"]), journal)
        if src is None:
            missing.append(item["name"])
            continue
        target.mkdir(parents=True, exist_ok=True)
        dest = library.unique_destination(target, src.name)
        shutil.copy2(str(src), str(dest))
        copied.append(str(dest))
    return {"folder": str(target) if copied else None, "copied": len(copied), "missing": missing}
