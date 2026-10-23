"""History across sessions, read from the JSONL journal, with "put back" for past sorts.

A sort is still in effect unless a later ``undo`` or ``restore`` entry names the same destination.
Putting back never overwrites: if the original path is taken, the file gets a unique name.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import library
from .session import Journal, load_copied_index, save_copied_index

MAX_DAYS = 90


def _entries(journal: Journal, days: int) -> list[dict]:
    """All journal entries of the last ``days`` days, oldest first, each with a stable ``id``."""
    out: list[dict] = []
    today = date.today()
    for n in range(days, -1, -1):
        day = today - timedelta(days=n)
        path = journal.path_for(day)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                e["id"] = f"{day.isoformat()}:{i}"
                out.append(e)
    return out


def load(journal: Journal, days: int = MAX_DAYS, active_dests: frozenset = frozenset()) -> list[dict]:
    """Sessions (newest first), each with the sorts still in effect.

    ``active_dests`` are destinations owned by the running session (those are undone with Undo).
    """
    entries = _entries(journal, days)
    cancelled = {e.get("dest") for e in entries if e.get("event") in ("undo", "restore") and e.get("dest")}
    sessions: dict[str, dict] = {}
    for e in entries:
        if e.get("event") != "sort" or not e.get("dest") or e["dest"] in cancelled:
            continue
        key = e.get("session") or e["id"].split(":")[0]  # entries from before session ids: group by day
        s = sessions.setdefault(
            key,
            {"id": key, "started": e.get("ts"), "root": e.get("root"), "items": []},
        )
        dest = Path(e["dest"])
        if str(dest) in active_dests:
            status = "current"
        elif not os.path.lexists(dest):
            status = "missing"
        else:
            status = "sorted"
        s["items"].append(
            {
                "id": e["id"],
                "ts": e.get("ts"),
                "op": e.get("op", "move"),
                "label": e.get("label"),
                "preset": e.get("preset"),
                "name": Path(e.get("src", "")).name,
                "src": e.get("src"),
                "dest": e["dest"],
                "status": status,
            }
        )
    return sorted(sessions.values(), key=lambda s: s["started"] or "", reverse=True)


def restore(
    journal: Journal,
    entry_ids: list[str],
    active_dests: frozenset = frozenset(),
    move: Callable[[Path, Path], None] = lambda a, b: shutil.move(str(a), str(b)),
) -> list[dict]:
    """Put back the given sorts. Returns one result per id: {id, ok, message, restored?}."""
    wanted = set(entry_ids)
    by_id = {e["id"]: e for e in _entries(journal, MAX_DAYS) if e["id"] in wanted}
    results = []
    for eid in entry_ids:
        e = by_id.get(eid)
        if e is None or e.get("event") != "sort":
            results.append({"id": eid, "ok": False, "message": "Not found in the history"})
            continue
        src, dest = Path(e["src"]), Path(e["dest"])
        if str(dest) in active_dests:
            results.append({"id": eid, "ok": False, "message": "Part of the current session: use Undo"})
            continue
        if not os.path.lexists(dest):
            results.append({"id": eid, "ok": False, "message": f"{dest.name} is no longer in {dest.parent.name}"})
            continue
        try:
            if e.get("op") == "copy":
                dest.unlink()
                restored: Optional[Path] = src
                _forget_copy(e, src)
            else:
                src.parent.mkdir(parents=True, exist_ok=True)
                restored = src if not os.path.lexists(src) else library.unique_destination(src.parent, src.name)
                move(dest, restored)
        except OSError as err:
            results.append({"id": eid, "ok": False, "message": f"Could not put back {src.name}: {err}"})
            continue
        journal.append({**{k: e.get(k) for k in ("op", "mode", "preset", "label", "src", "dest", "session", "root")},
                        "event": "restore", "restored": str(restored)})
        msg = "Put back" if restored == src else f"Put back as {restored.name} (the name was taken)"
        results.append({"id": eid, "ok": True, "message": msg, "restored": str(restored)})
    return results


def _forget_copy(entry: dict, src: Path) -> None:
    root = entry.get("root")
    if not root:
        return
    idx = load_copied_index(Path(root))
    if idx.pop(library.relative_posix(src, Path(root)), None) is not None:
        try:
            save_copied_index(Path(root), idx)
        except OSError:
            pass
