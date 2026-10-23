"""Export M3U8 playlists of the sorted folders, for importing into Rekordbox or Serato.

One playlist per destination folder (Skipped is left out), all written to
``BAKKIS_SORTED/_Playlists/``. Paths are absolute so the files work wherever the playlist is opened.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from . import library
from .config import safe_segment
from .metadata import TrackMeta

PLAYLIST_DIR = "_Playlists"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _audio_files(folder: Path) -> list[Path]:
    try:
        return sorted(
            (Path(e.path) for e in os.scandir(folder) if e.is_file() and not e.name.startswith(".") and library.is_audio_path(Path(e.name))),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return []


def destination_folders(music_root: Path) -> list[tuple[str, Path]]:
    """(playlist name, folder) for every ``<preset>/[date/]<folder>`` under BAKKIS_SORTED."""
    root = library.sorted_root(music_root)
    out: list[tuple[str, Path]] = []
    try:
        presets = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))), key=lambda p: p.name.lower())
    except OSError:
        return out
    for preset in presets:
        for child in sorted((c for c in preset.iterdir() if c.is_dir()), key=lambda p: p.name.lower()):
            if _DATE_RE.match(child.name):
                for leaf in sorted((c for c in child.iterdir() if c.is_dir()), key=lambda p: p.name.lower()):
                    if leaf.name != library.SKIPPED_NAME:
                        out.append((f"{preset.name} - {child.name} - {leaf.name}", leaf))
            elif child.name != library.SKIPPED_NAME:
                out.append((f"{preset.name} - {child.name}", child))
    return out


def export(music_root: Path, get_meta: Callable[[Path], TrackMeta]) -> dict:
    """Write the playlists. Returns {"folder": <_Playlists path>, "playlists": [{name, tracks}]}."""
    target = library.sorted_root(music_root) / PLAYLIST_DIR
    written = []
    for name, folder in destination_folders(music_root):
        files = _audio_files(folder)
        if not files:
            continue
        lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
        for f in files:
            m = get_meta(f)
            label = f"{m.artist} - {m.title}" if m.artist and m.title else m.display
            lines.append(f"#EXTINF:{int(round(m.duration or 0))},{label}")
            lines.append(str(f))
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"{safe_segment(name, 'Playlist')}.m3u8"
        tmp = out.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, out)
        written.append({"name": name, "tracks": len(files), "path": str(out)})
    return {"folder": str(target), "playlists": written}
