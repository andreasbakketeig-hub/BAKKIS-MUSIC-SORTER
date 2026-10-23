"""Export M3U8 playlists of the sorted folders, for importing into Rekordbox or Serato.

One playlist per destination folder (Skipped is left out), written to ``_Playlists/`` of the tree it
comes from (BAKKIS_SORTED or BAKKIS_SORTED_COPIES). Paths are absolute so the files work wherever the
playlist is opened.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from . import library
from .config import safe_segment
from .metadata import TrackMeta

PLAYLIST_DIR = "_Playlists"


def _audio_files(folder: Path) -> list[Path]:
    try:
        return sorted(
            (Path(e.path) for e in os.scandir(folder) if e.is_file() and not e.name.startswith(".") and library.is_audio_path(Path(e.name))),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return []


def tree_folders(tree: Path) -> list[Path]:
    """Every folder below ``tree`` that holds tracks, e.g. ``<preset>/[date/]<folder>`` and the folders of a
    second pass (``Genre Sort/Techno/Quick Sort/Keep``). Skipped, hidden and ``_`` folders are left out."""
    out: list[Path] = []
    for dirpath, dirs, _files in os.walk(tree):
        dirs[:] = sorted((d for d in dirs if not d.startswith((".", "_")) and d != library.SKIPPED_NAME), key=str.lower)
        if dirpath != str(tree) and _audio_files(Path(dirpath)):
            out.append(Path(dirpath))
    return out


def destination_folders(music_root: Path) -> list[tuple[str, Path, Path]]:
    """(playlist name, folder, tree) for every folder with tracks in BAKKIS_SORTED and BAKKIS_SORTED_COPIES."""
    out: list[tuple[str, Path, Path]] = []
    for tree in library.tree_roots(music_root):
        suffix = " (copies)" if tree.name == library.COPIES_NAME else ""
        for folder in tree_folders(tree):
            out.append((" - ".join(folder.relative_to(tree).parts) + suffix, folder, tree))
    return out


def write_playlist(
    music_root: Path, name: str, files: list[Path], get_meta: Callable[[Path], TrackMeta], tree: Optional[Path] = None
) -> dict:
    """One M3U8 in ``<tree>/_Playlists`` (BAKKIS_SORTED by default). Returns {name, tracks, path}."""
    target = (tree or library.sorted_root(music_root)) / PLAYLIST_DIR
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
    return {"name": name, "tracks": len(files), "path": str(out)}


def export(music_root: Path, get_meta: Callable[[Path], TrackMeta]) -> dict:
    """Write the playlists. Returns {"folder": <first _Playlists path written>, "playlists": [{name, tracks}]}."""
    written = []
    folders: list[Path] = []
    for name, folder, tree in destination_folders(music_root):
        written.append(write_playlist(music_root, name, _audio_files(folder), get_meta, tree))
        if tree / PLAYLIST_DIR not in folders:
            folders.append(tree / PLAYLIST_DIR)
    first = folders[0] if folders else library.sorted_root(music_root) / PLAYLIST_DIR
    return {"folder": str(first), "playlists": written}
