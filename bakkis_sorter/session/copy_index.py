"""Copy-mode deduplication index tracking files copied into BAKKIS_SORTED_COPIES."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .. import library
from ..config import write_json_atomic

COPIED_INDEX_NAME = ".bakkis_copied.json"


def copied_index_path(music_root: Path) -> Path:
    return library.copies_root(music_root) / COPIED_INDEX_NAME


def load_copied_index(music_root: Path, *, storage: Optional[Any] = None) -> dict:
    """``{relative source path: {"dest": str, "size": int}}`` of sources already copied by us."""
    st = storage
    if st is None:
        try:
            from ..storage import get_storage

            st = get_storage()
        except Exception:
            pass

    out = {}
    if st is not None:
        try:
            stored = st.get_kv(f"copy_index:{Path(music_root).resolve().as_posix()}")
            if isinstance(stored, dict):
                out.update(stored)
        except Exception:
            pass

    # Merge / fallback to disk file if present
    try:
        with open(copied_index_path(music_root), encoding="utf-8") as f:
            data = json.load(f)
        src = data.get("sources") if isinstance(data, dict) else None
        if isinstance(src, dict):
            out.update(src)
    except (OSError, ValueError):
        pass
    return out


def save_copied_index(music_root: Path, index: dict, *, storage: Optional[Any] = None) -> None:
    st = storage
    if st is None:
        try:
            from ..storage import get_storage

            st = get_storage()
        except Exception:
            pass

    if st is not None:
        try:
            st.put_kv(f"copy_index:{Path(music_root).resolve().as_posix()}", index)
        except Exception:
            pass

    path = copied_index_path(music_root)
    if not index:
        if path.exists():
            path.unlink()
        return
    write_json_atomic(path, {"version": 1, "sources": index})


def filter_already_copied(paths: list[Path], music_root: Path, index: Optional[dict] = None) -> list[Path]:
    """Drop sources recorded in the copy index whose size is unchanged (the file was not replaced)."""
    idx = load_copied_index(music_root) if index is None else index
    if not idx:
        return list(paths)
    out = []
    for p in paths:
        rec = idx.get(library.relative_posix(p, Path(music_root).resolve()))
        if rec is not None:
            try:
                if os.path.getsize(p) == rec.get("size"):
                    continue
            except OSError:
                pass
        out.append(p)
    return out
