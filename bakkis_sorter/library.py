"""Scanning music folders and computing ``BAKKIS_SORTED`` destination paths.

Pure filesystem helpers with no UI. File moves themselves live in ``session.py``.
"""

from __future__ import annotations

import os
import random
import re
import threading
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .config import safe_segment

SUPPORTED_FORMATS = frozenset({".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".alac", ".ogg"})

BAKKIS_SORTED_NAME = "BAKKIS_SORTED"
SKIPPED_NAME = "Skipped"
# v1 used flat output roots before BAKKIS_SORTED; never scan into them.
LEGACY_SKIP_DIR_NAMES = frozenset({"keep", "archive", "favorites", "skipped", "genres"})
CLEANUP_IGNORABLE_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

_DATE_PREFIXED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2} - (.+)$")

PROGRESS_EVERY = 40


def is_audio_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_FORMATS


def should_ignore_file(name: str) -> bool:
    """Hidden files, ``.DS_Store`` and AppleDouble ``._*`` files."""
    return name.startswith(".")


def build_skip_dir_names(preset_names: Iterable[str] = ()) -> frozenset[str]:
    out = set(LEGACY_SKIP_DIR_NAMES)
    out.add(BAKKIS_SORTED_NAME.lower())
    out.update(n.lower() for n in preset_names if n)
    return frozenset(out)


def should_skip_dir(name: str, skip: frozenset[str]) -> bool:
    if name.startswith("."):
        return True
    low = name.lower()
    if low in skip:
        return True
    m = _DATE_PREFIXED_DIR_RE.match(name)
    return bool(m and m.group(1).lower() in skip)


def iter_audio_files(
    root: Path,
    *,
    recursive: bool = True,
    skip_dir_names: Optional[frozenset[str]] = None,
) -> Iterator[Path]:
    """Yield audio files under ``root`` (root is resolved once; files are not individually resolved)."""
    base = Path(root).resolve()
    skip = build_skip_dir_names() if skip_dir_names is None else skip_dir_names
    if recursive:
        for dirpath, dirs, files in os.walk(base, topdown=True):
            dirs[:] = [d for d in dirs if not should_skip_dir(d, skip)]
            for name in files:
                if should_ignore_file(name):
                    continue
                p = Path(dirpath, name)
                if is_audio_path(p):
                    yield p
    else:
        try:
            entries = sorted(os.scandir(base), key=lambda e: e.name)
        except OSError:
            return
        for ent in entries:
            if should_ignore_file(ent.name):
                continue
            try:
                is_file = ent.is_file()
            except OSError:
                continue
            p = Path(ent.path)
            if is_file and is_audio_path(p):
                yield p


def scan(
    root: Path,
    *,
    recursive: bool = True,
    skip_dir_names: Optional[frozenset[str]] = None,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Optional[list[Path]]:
    """Collect audio files. Returns None if ``cancel`` was set during the scan."""
    out: list[Path] = []
    for p in iter_audio_files(root, recursive=recursive, skip_dir_names=skip_dir_names):
        if cancel is not None and cancel.is_set():
            return None
        out.append(p)
        if on_progress is not None and len(out) % PROGRESS_EVERY == 0:
            on_progress(len(out))
    if cancel is not None and cancel.is_set():
        return None
    return out


class ScanJob:
    """Runs ``scan`` on a daemon thread. Callbacks run on that thread; never after ``cancel()``."""

    def __init__(
        self,
        root: Path,
        *,
        recursive: bool = True,
        skip_dir_names: Optional[frozenset[str]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
        on_done: Optional[Callable[[list[Path]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.root = Path(root)
        self._cancel = threading.Event()
        self._recursive = recursive
        self._skip = skip_dir_names
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_error = on_error
        self.result: Optional[list[Path]] = None
        self._thread = threading.Thread(target=self._run, name="bakkis-scan", daemon=True)

    def start(self) -> "ScanJob":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout)

    def _progress(self, n: int) -> None:
        if self._on_progress is not None and not self._cancel.is_set():
            self._on_progress(n)

    def _run(self) -> None:
        try:
            res = scan(
                self.root,
                recursive=self._recursive,
                skip_dir_names=self._skip,
                cancel=self._cancel,
                on_progress=self._progress,
            )
        except Exception as e:  # noqa: BLE001 - reported to caller
            if self._on_error is not None and not self._cancel.is_set():
                self._on_error(e)
            return
        if res is None or self._cancel.is_set():
            return
        self.result = res
        if self._on_done is not None:
            self._on_done(res)


# --------------------------------------------------------------------------- ordering


def _in_order_key(p: Path) -> str:
    return str(p).lower()


def order_tracks(paths: Iterable[Path], order: str, rng: Optional[random.Random] = None) -> list[Path]:
    out = list(paths)
    if order == "random":
        (rng or random).shuffle(out)
        return out
    return sorted(out, key=_in_order_key)


def reorder_keep_current(queue: list[Path], order: str, rng: Optional[random.Random] = None) -> list[Path]:
    """Reorder a queue without changing its head (the current track)."""
    if not queue:
        return []
    current = queue[0]
    if order == "random":
        rest = list(queue[1:])
        (rng or random).shuffle(rest)
        return [current] + rest
    # In order: continue alphabetically from the current track, wrapping around.
    ordered = sorted(queue, key=_in_order_key)
    i = ordered.index(current)
    return ordered[i:] + ordered[:i]


# --------------------------------------------------------------------------- display helpers


def relative_posix(path: Path, root: Path) -> str:
    try:
        return Path(path).relative_to(Path(root)).as_posix()
    except ValueError:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            return Path(path).name


def source_folder_display(path: Path, root: Path) -> str:
    """Parent folder under ``root`` for display, e.g. ``Promos / Label``; empty at top level."""
    parent = Path(relative_posix(path, root)).parent
    return "" if parent == Path(".") else " / ".join(parent.parts)


# --------------------------------------------------------------------------- destinations


def sorted_root(music_root: Path) -> Path:
    return Path(music_root).resolve() / BAKKIS_SORTED_NAME


def preset_session_root(music_root: Path, preset_name: str, *, dated: bool, today: Optional[date] = None) -> Path:
    """``<root>/BAKKIS_SORTED/<preset>[/YYYY-MM-DD]``."""
    base = sorted_root(music_root) / safe_segment(preset_name)
    if dated:
        return base / (today or date.today()).isoformat()
    return base


def slot_folder_name(preset: dict, slot: int) -> str:
    """Leaf folder for a decision slot.

    Swipe presets name folders after their labels (v1 behavior); grid presets use ``folders``.
    """
    if preset["mode"] == "swipe":
        return safe_segment(preset["labels"][slot], "?")
    return safe_segment(preset["folders"][slot], "Genre")


def slot_destination_dir(music_root: Path, preset: dict, slot: int, *, dated: bool, today: Optional[date] = None) -> Path:
    return preset_session_root(music_root, preset["name"], dated=dated, today=today) / slot_folder_name(preset, slot)


def skipped_destination_dir(
    music_root: Path, preset: dict, src: Path, *, dated: bool, today: Optional[date] = None
) -> Path:
    """``…/<preset>/[date/]Skipped/<relative source subfolders>``."""
    base = preset_session_root(music_root, preset["name"], dated=dated, today=today) / SKIPPED_NAME
    rel_parent = Path(relative_posix(src, music_root)).parent
    return base if rel_parent == Path(".") else base / rel_parent


def unique_destination(dest_dir: Path, name: str) -> Path:
    """``dest_dir/name``, or ``name (2).ext``, ``name (3).ext``… if taken."""
    cand = Path(dest_dir) / name
    if not os.path.lexists(cand):
        return cand
    stem, suf = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        cand = Path(dest_dir) / f"{stem} ({n}){suf}"
        if not os.path.lexists(cand):
            return cand
        n += 1


# --------------------------------------------------------------------------- empty source folder cleanup


def _is_within(child: Path, ancestor: Path) -> bool:
    try:
        child.relative_to(ancestor)
        return True
    except ValueError:
        return False


def cleanup_chain(src: Path, music_root: Path) -> list[Path]:
    """Ancestors of ``src`` below ``music_root`` (deepest first), excluding the root and ``BAKKIS_SORTED``."""
    root = Path(music_root).resolve()
    sroot = root / BAKKIS_SORTED_NAME
    out: list[Path] = []
    p = Path(src).parent.resolve()
    while p != root and _is_within(p, root):
        if not _is_within(p, sroot):
            out.append(p)
        if p.parent == p:
            break
        p = p.parent
    return out


def is_cleanup_ignorable(name: str) -> bool:
    return name.lower() in CLEANUP_IGNORABLE_NAMES or name.startswith("._")


def _junk_if_removable(d: Path) -> Optional[list[Path]]:
    """Junk files to delete if ``d`` holds nothing else; None if the dir must stay."""
    junk: list[Path] = []
    try:
        entries = list(os.scandir(d))
    except OSError:
        return None
    for ent in entries:
        try:
            if ent.is_dir(follow_symlinks=False) or not is_cleanup_ignorable(ent.name):
                return None
        except OSError:
            return None
        junk.append(Path(ent.path))
    return junk


def cleanup_empty_parents(src: Path, music_root: Path) -> tuple[list[Path], Optional[str]]:
    """Remove now-empty ancestor folders of a moved file.

    Returns (deleted dirs deepest-first, error message or None). Stops at the first non-empty dir.
    Undo recreates them by creating the original file's parent directory.
    """
    deleted: list[Path] = []
    for d in cleanup_chain(src, music_root):
        junk = _junk_if_removable(d)
        if junk is None:
            break
        try:
            for j in junk:
                j.unlink()
            d.rmdir()
        except OSError as e:
            return deleted, f"Could not remove empty folder {d}: {e}"
        deleted.append(d)
    return deleted, None
