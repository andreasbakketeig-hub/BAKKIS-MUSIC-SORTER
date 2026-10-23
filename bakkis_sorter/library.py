"""Scanning music folders and computing destination paths.

Moved tracks go to ``BAKKIS_SORTED``, copies to ``BAKKIS_SORTED_COPIES`` (so the copies are easy to tell
apart and safe to delete). A folder that already lies inside one of those trees is sorted in place:
``BAKKIS_SORTED/Genre Sort/Techno/Quick Sort/Keep``, and always by moving (the files are ours already).

Pure filesystem helpers with no UI. File moves themselves live in ``session.py``.
"""

from __future__ import annotations

import os
import random
import re
import threading
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, TypeVar

from .config import safe_segment

SUPPORTED_FORMATS = frozenset({".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".alac", ".ogg"})

BAKKIS_SORTED_NAME = "BAKKIS_SORTED"
COPIES_NAME = "BAKKIS_SORTED_COPIES"
SORTED_TREE_NAMES = frozenset({BAKKIS_SORTED_NAME.lower(), COPIES_NAME.lower()})
SKIPPED_NAME = "Skipped"
# v1 used flat output roots before BAKKIS_SORTED; never scan into them.
LEGACY_SKIP_DIR_NAMES = frozenset({"keep", "archive", "favorites", "skipped", "genres"})
CLEANUP_IGNORABLE_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

_DATE_PREFIXED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2} - (.+)$")

PROGRESS_EVERY = 40

T = TypeVar("T")


def is_audio_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_FORMATS


def should_ignore_file(name: str) -> bool:
    """Hidden files, ``.DS_Store`` and AppleDouble ``._*`` files."""
    return name.startswith(".")


def should_skip_dir(name: str, *, at_root: bool, root_skip: frozenset = frozenset()) -> bool:
    """Directories the scan never enters.

    Hidden folders and the sorted trees are skipped at any depth. v1's old flat output folders
    (KEEP, ARCHIVE, …, also ``YYYY-MM-DD - KEEP``) only exist at the top of a music folder, so they are
    skipped there only; a source folder that happens to share a preset or genre name is still scanned.
    ``root_skip`` adds lower-case names to skip at the top (preset folders when sorting in place).
    """
    if name.startswith("."):
        return True
    low = name.lower()
    if low in SORTED_TREE_NAMES:
        return True
    if at_root and low in root_skip:
        return True
    if at_root:
        m = _DATE_PREFIXED_DIR_RE.match(name)
        return low in LEGACY_SKIP_DIR_NAMES or bool(m and m.group(1).lower() in LEGACY_SKIP_DIR_NAMES)
    return False


def iter_audio_files(root: Path, *, recursive: bool = True, root_skip: frozenset = frozenset()) -> Iterator[Path]:
    """Yield audio files under ``root`` (root is resolved once; files are not individually resolved)."""
    base = Path(root).resolve()
    if recursive:
        for dirpath, dirs, files in os.walk(base, topdown=True):
            at_root = dirpath == str(base)
            dirs[:] = [d for d in dirs if not should_skip_dir(d, at_root=at_root, root_skip=root_skip)]
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
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    root_skip: frozenset = frozenset(),
) -> Optional[list[Path]]:
    """Collect audio files. Returns None if ``cancel`` was set during the scan."""
    out: list[Path] = []
    for p in iter_audio_files(root, recursive=recursive, root_skip=root_skip):
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
        on_progress: Optional[Callable[[int], None]] = None,
        on_done: Optional[Callable[[list[Path]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        root_skip: frozenset = frozenset(),
        before: Optional[Callable[[], None]] = None,
    ) -> None:
        self.root = Path(root)
        self._root_skip = root_skip
        self._before = before  # runs on the scan thread first (e.g. finish pending moves)
        self._cancel = threading.Event()
        self._recursive = recursive
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
            if self._before is not None:
                self._before()
            res = scan(
                self.root,
                recursive=self._recursive,
                cancel=self._cancel,
                on_progress=self._progress,
                root_skip=self._root_skip,
            )
        except Exception as e:  # noqa: BLE001 - reported to caller
            if self._on_error is not None and not self._cancel.is_set():
                self._on_error(e)
            return
        if res is None or self._cancel.is_set():
            return
        self.result = res
        if self._on_done is not None and not self._cancel.is_set():
            self._on_done(res)


# --------------------------------------------------------------------------- ordering


def _in_order_key(p) -> str:
    return str(p).lower()


def order_tracks(paths: Iterable[T], order: str, rng: Optional[random.Random] = None, key: Callable[[T], str] = _in_order_key) -> list[T]:
    out = list(paths)
    if order == "random":
        (rng or random).shuffle(out)
        return out
    return sorted(out, key=key)


def reorder_keep_current(
    queue: list[T], order: str, rng: Optional[random.Random] = None, key: Callable[[T], str] = _in_order_key
) -> list[T]:
    """Reorder a queue without changing its head (the current track). ``key`` maps items to a path string."""
    if not queue:
        return []
    current = queue[0]
    if order == "random":
        rest = list(queue[1:])
        (rng or random).shuffle(rest)
        return [current] + rest
    # In order: continue alphabetically from the current track, wrapping around.
    ordered = sorted(queue, key=key)
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


def tree_of(path: Path) -> Optional[Path]:
    """The BAKKIS_SORTED or BAKKIS_SORTED_COPIES folder that ``path`` is (or lies in), else None."""
    p = Path(path).resolve()
    for cand in (p, *p.parents):
        if cand.name.lower() in SORTED_TREE_NAMES:
            return cand
    return None


def owner_root(music_root: Path) -> Path:
    """The music folder that owns the sorted trees: ``music_root`` itself unless it lies inside one."""
    tree = tree_of(music_root)
    return tree.parent if tree is not None else Path(music_root).resolve()


def sorts_in_place(music_root: Path) -> bool:
    """True when the loaded folder is inside a sorted tree (second pass: sort in place, always move)."""
    return tree_of(music_root) is not None


def effective_operation(music_root: Path, op: str) -> str:
    return "move" if op != "copy" or sorts_in_place(music_root) else "copy"


def sorted_root(music_root: Path) -> Path:
    """``<owner>/BAKKIS_SORTED``: where moved tracks go."""
    return owner_root(music_root) / BAKKIS_SORTED_NAME


def copies_root(music_root: Path) -> Path:
    """``<owner>/BAKKIS_SORTED_COPIES``: where copies go (and Discover's hearted folders)."""
    return owner_root(music_root) / COPIES_NAME


def tree_roots(music_root: Path) -> list[Path]:
    """Both sorted trees of the owning music folder (they may not exist)."""
    return [sorted_root(music_root), copies_root(music_root)]


def output_base(music_root: Path, op: str = "move") -> Path:
    """Folder that holds the ``<preset>/…`` folders for a sort from ``music_root``."""
    if sorts_in_place(music_root):
        return Path(music_root).resolve()
    return copies_root(music_root) if op == "copy" else sorted_root(music_root)


def preset_session_root(
    music_root: Path, preset_name: str, *, dated: bool, today: Optional[date] = None, op: str = "move"
) -> Path:
    """``<base>/<preset>[/YYYY-MM-DD]``; base is BAKKIS_SORTED, BAKKIS_SORTED_COPIES or the folder itself."""
    base = output_base(music_root, op) / safe_segment(preset_name)
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


def slot_destination_dir(
    music_root: Path, preset: dict, slot: int, *, dated: bool, today: Optional[date] = None, op: str = "move"
) -> Path:
    return preset_session_root(music_root, preset["name"], dated=dated, today=today, op=op) / slot_folder_name(preset, slot)


def skipped_destination_dir(
    music_root: Path, preset: dict, src: Path, *, dated: bool, today: Optional[date] = None, op: str = "move"
) -> Path:
    """``…/<preset>/[date/]Skipped/<relative source subfolders>``."""
    base = preset_session_root(music_root, preset["name"], dated=dated, today=today, op=op) / SKIPPED_NAME
    rel_parent = Path(relative_posix(src, music_root)).parent
    return base if rel_parent == Path(".") else base / rel_parent


def reserve_destination(dest_dir: Path, name: str) -> Path:
    """Like ``unique_destination``, but creates the file exclusively (O_EXCL), so nothing else can take the
    name between the check and the move. The empty placeholder is then replaced by the real file."""
    stem, suf = Path(name).stem, Path(name).suffix
    n = 1
    while True:
        cand = Path(dest_dir) / (name if n == 1 else f"{stem} ({n}){suf}")
        try:
            fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return cand


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


def is_within(child: Path, ancestor: Path) -> bool:
    """True if ``child`` (resolved) lies inside ``ancestor`` (resolved)."""
    return _is_within(Path(child).resolve(), Path(ancestor).resolve())


def _is_within(child: Path, ancestor: Path) -> bool:
    try:
        child.relative_to(ancestor)
        return True
    except ValueError:
        return False


def cleanup_chain(src: Path, music_root: Path) -> list[Path]:
    """Ancestors of ``src`` below ``music_root`` (deepest first), excluding the root and the sorted trees."""
    root = Path(music_root).resolve()
    trees = [root / BAKKIS_SORTED_NAME, root / COPIES_NAME]
    out: list[Path] = []
    p = Path(src).parent.resolve()
    while p != root and _is_within(p, root):
        if not any(_is_within(p, t) for t in trees):
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


def music_root_of(sorted_path: Path) -> Optional[Path]:
    """The music folder that owns a path inside one of its sorted trees."""
    tree = tree_of(Path(sorted_path).parent)
    return tree.parent if tree is not None else None


def prune_sorted_dirs(start: Path, music_root: Optional[Path] = None) -> list[Path]:
    """Remove folders in a sorted tree that are empty (or hold only junk), from ``start`` upward.

    Used after undo, put back and failed sorts so no empty Keep/2026-10-23/… folders are left behind.
    Stops at the first folder that still has content, never removes the loaded ``music_root`` and never
    touches anything outside the sorted tree that ``start`` lies in.
    """
    sroot = tree_of(start)
    if sroot is None:
        return []
    keep = Path(music_root).resolve() if music_root is not None else None
    p = Path(start).resolve()
    removed: list[Path] = []
    while (p == sroot or _is_within(p, sroot)) and p != keep:
        if p.is_dir():
            junk = _junk_if_removable(p)
            if junk is None:
                break
            try:
                for j in junk:
                    j.unlink()
                p.rmdir()
            except OSError:
                break
            removed.append(p)
        if p == sroot:
            break
        p = p.parent
    return removed
