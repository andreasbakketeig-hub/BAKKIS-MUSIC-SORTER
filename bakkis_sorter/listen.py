"""Listen mode: play through unsorted and/or sorted folders without sorting anything.

Nothing here moves or copies audio files. "Like" only appends to a playlist in
``BAKKIS_SORTED/_Playlists``.
"""

from __future__ import annotations

import os
import random
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import library, playlists
from .metadata import TrackMeta
from .session import Track, new_track

LIKED_PLAYLIST = "Liked while listening.m3u8"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sorted_sources(music_root: Path) -> list[dict]:
    """Every sorted destination folder as a listen source: {key, label, count}."""
    root = library.sorted_root(music_root)
    out = []
    for _name, folder in playlists.destination_folders(music_root):
        count = len(playlists._audio_files(folder))  # noqa: SLF001 - shared helper
        if not count:
            continue
        rel = folder.relative_to(root)
        parts = list(rel.parts)  # <preset>[/<date>]/<folder>
        label = " · ".join([parts[-1], parts[0]] + [p for p in parts[1:-1] if _DATE_RE.match(p)])
        out.append({"key": f"sorted:{rel.as_posix()}", "label": label, "count": count})
    return out


def resolve_source(music_root: Path, key: str) -> Optional[Path]:
    """Folder for a ``sorted:`` key, refusing anything outside BAKKIS_SORTED."""
    if not key.startswith("sorted:"):
        return None
    root = library.sorted_root(music_root)
    folder = (root / key[len("sorted:") :]).resolve()
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

    def public(self, music_root: Path) -> list[dict]:
        return [
            {"id": t.id, "name": t.path.name, "folder": library.source_folder_display(t.path, music_root), "source": self.source_of[t.id]}
            for t in self.tracks
        ]


def like(music_root: Path, path: Path, get_meta: Callable[[Path], TrackMeta]) -> dict:
    """Append a track to the 'Liked while listening' playlist (no duplicates)."""
    target = library.sorted_root(music_root) / playlists.PLAYLIST_DIR / LIKED_PLAYLIST
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else ["#EXTM3U", "#PLAYLIST:Liked while listening"]
    entries = [l for l in lines if l and not l.startswith("#")]
    if str(path) in entries:
        return {"added": False, "count": len(entries), "path": str(target)}
    m = get_meta(path)
    label = f"{m.artist} - {m.title}" if m.artist and m.title else m.display
    lines += [f"#EXTINF:{int(round(m.duration or 0))},{label}", str(path)]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return {"added": True, "count": len(entries) + 1, "path": str(target)}
