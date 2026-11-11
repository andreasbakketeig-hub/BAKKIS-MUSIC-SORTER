"""Preset storage, manipulation, and validation."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

from .paths import (
    GENRE_LABELS_JSON,
    MODE_PRESETS_JSON,
    _read_json,
    config_dir,
    write_json_atomic,
)
from .coercions import (
    DEFAULT_GENRE_COLORS,
    DEFAULT_GENRE_LABELS,
    DEFAULT_SWIPE_COLORS,
    DEFAULT_SWIPE_FOLDERS,
    DEFAULT_SWIPE_LABELS,
    GRID_SLOTS,
    MODES,
    SWIPE_SLOTS,
    coerce_bool,
    coerce_palette_hex,
    norm_mode,
    normalize_hex,
    safe_segment,
)

def new_preset_id() -> str:
    return uuid.uuid4().hex


def load_genre_defaults() -> tuple[list[str], list[str]]:
    """Grid defaults from v1 ``genre_labels.json`` if present, else built-in genres."""
    data = _read_json(config_dir() / GENRE_LABELS_JSON)
    raw_labels: Optional[list] = None
    raw_colors: Optional[list] = None
    if isinstance(data, dict) and isinstance(data.get("labels"), list):
        raw_labels = data["labels"]
        raw_colors = data.get("colors") if isinstance(data.get("colors"), list) else None
    elif isinstance(data, list):
        raw_labels = data
    if raw_labels is None:
        return list(DEFAULT_GENRE_LABELS), list(DEFAULT_GENRE_COLORS)
    labels = [str(x).strip() or "?" for x in raw_labels[:GRID_SLOTS]]
    labels += DEFAULT_GENRE_LABELS[len(labels) : GRID_SLOTS]
    colors = [
        coerce_palette_hex(raw_colors[i], DEFAULT_GENRE_COLORS[i]) if raw_colors and i < len(raw_colors) else DEFAULT_GENRE_COLORS[i]
        for i in range(GRID_SLOTS)
    ]
    return labels, colors


def default_swipe_preset() -> dict:
    return {
        "id": new_preset_id(),
        "name": "Quick Sort",
        "mode": "swipe",
        "labels": list(DEFAULT_SWIPE_LABELS),
        "colors": list(DEFAULT_SWIPE_COLORS),
        "folders": list(DEFAULT_SWIPE_FOLDERS),
        "dated": False,
    }


def default_grid_preset(use_genre_file: bool = True) -> dict:
    labels, colors = load_genre_defaults() if use_genre_file else (list(DEFAULT_GENRE_LABELS), list(DEFAULT_GENRE_COLORS))
    return {
        "id": new_preset_id(),
        "name": "Genre Sort",
        "mode": "grid",
        "labels": labels,
        "colors": colors,
        "folders": [safe_segment(lb, "Genre") for lb in labels],
        "dated": False,
    }


# Presets saved before per-preset date folders: these built-ins get them (weekly batches), others follow
# the old global setting.
DATED_BY_DEFAULT = frozenset({"promos", "demos"})


def coerce_preset(blob: object, dated_default: bool = False) -> Optional[dict]:
    """Validate one preset dict; returns a clean copy or None if unusable."""
    if not isinstance(blob, dict):
        return None
    mode = str(blob.get("mode", "")).lower()
    if mode not in MODES:
        return None
    labels = blob.get("labels")
    if mode == "grid" and not isinstance(labels, list):
        return None
    labels = labels if isinstance(labels, list) else []
    colors = blob.get("colors") if isinstance(blob.get("colors"), list) else []
    folders = blob.get("folders") if isinstance(blob.get("folders"), list) else []
    pid = str(blob.get("id") or "").strip() or new_preset_id()

    if mode == "swipe":
        n = SWIPE_SLOTS
        name = str(blob.get("name") or "").strip() or "Swipe preset"
        ls = [str(labels[i]).strip() or "?" if i < len(labels) else DEFAULT_SWIPE_LABELS[i] for i in range(n)]
        cs = [normalize_hex(colors[i], DEFAULT_SWIPE_COLORS[i]) if i < len(colors) else DEFAULT_SWIPE_COLORS[i] for i in range(n)]
        fs = [
            str(folders[i]).strip() if i < len(folders) and str(folders[i]).strip() else DEFAULT_SWIPE_FOLDERS[i]
            for i in range(n)
        ]
    else:
        n = GRID_SLOTS
        name = str(blob.get("name") or "").strip() or "Grid preset"
        ls = [str(labels[i]).strip() or "?" if i < len(labels) else DEFAULT_GENRE_LABELS[i] for i in range(n)]
        cs = [coerce_palette_hex(colors[i], DEFAULT_GENRE_COLORS[i]) if i < len(colors) else DEFAULT_GENRE_COLORS[i] for i in range(n)]
        fs = [
            str(folders[i]).strip() if i < len(folders) and str(folders[i]).strip() else safe_segment(ls[i], "Genre")
            for i in range(n)
        ]
    if "dated" in blob:
        dated = coerce_bool(blob["dated"])
    else:
        dated = dated_default or name.strip().lower() in DATED_BY_DEFAULT
    return {"id": pid, "name": name, "mode": mode, "labels": ls, "colors": cs, "folders": fs, "dated": dated}


def normalize_preset_store(raw: object, dated_default: bool = False) -> dict:
    data = raw if isinstance(raw, dict) else {}
    presets: list[dict] = []
    seen: set[str] = set()
    for item in data.get("presets") or []:
        p = coerce_preset(item, dated_default)
        if p is None:
            continue
        if p["id"] in seen:
            p["id"] = new_preset_id()
        seen.add(p["id"])
        presets.append(p)
    if not any(p["mode"] == "swipe" for p in presets):
        presets.insert(0, default_swipe_preset())
    if not any(p["mode"] == "grid" for p in presets):
        presets.append(default_grid_preset())
    swipe_ids = [p["id"] for p in presets if p["mode"] == "swipe"]
    grid_ids = [p["id"] for p in presets if p["mode"] == "grid"]
    aid = str(data.get("active_swipe_preset_id") or "")
    gid = str(data.get("active_grid_preset_id") or "")
    added = data.get("builtins_added")
    return {
        "presets": presets,
        "active_swipe_preset_id": aid if aid in swipe_ids else swipe_ids[0],
        "active_grid_preset_id": gid if gid in grid_ids else grid_ids[0],
        # Built-in presets already offered once (so a deleted one does not come back). v1 ignores this key.
        "builtins_added": [str(x) for x in added] if isinstance(added, list) else [],
    }


# Ready-made presets that show what the app is for. Swipe slots: [0] = → (Keep), [1] = ← (Archive),
# [2] = ↑ (Favorite, the strongest yes). Swipe folders are named after the labels.
BUILTIN_PRESETS: tuple[dict, ...] = (
    {
        "key": "promos",
        "mode": "swipe",
        "name": "Promos",  # a DJ answering label promos (promo report wording)
        "labels": ["Maybe", "Not for me", "Feeling it"],
        "colors": ["#00D6B4", "#FF2D55", "#FFF200"],
        "dated": True,  # one folder per batch of promos
    },
    {
        "key": "demos",
        "mode": "swipe",
        "name": "Demos",  # a label answering demo submissions
        "labels": ["Hold", "Reject", "Sign"],
        "colors": ["#00D6B4", "#FF2D55", "#FFF200"],
        "dated": True,
    },
    {
        "key": "set-builder",
        "mode": "grid",
        "name": "Set Builder",
        "labels": ["Warm-up", "Deep", "Groove", "Build-up", "Peak time", "Driving", "Closing", "After hours", "Other"],
        "colors": ["#18A0FB", "#7C5CFF", "#00D6B4", "#FFF200", "#FF2D55", "#FF7A1A", "#D65CFF", "#2A2F36", "#8A8F98"],
        "dated": False,
    },
)


def add_builtin_presets(data: dict) -> bool:
    """Add each built-in preset once (skipping names that already exist). Returns True if anything changed."""
    changed = False
    names = {p["name"].strip().lower() for p in data["presets"]}
    for b in BUILTIN_PRESETS:
        if b["key"] in data["builtins_added"]:
            continue
        data["builtins_added"].append(b["key"])
        changed = True
        if b["name"].lower() in names:
            continue
        p = coerce_preset(
            {"id": new_preset_id(), "mode": b["mode"], "name": b["name"], "labels": b["labels"], "colors": b["colors"],
             "folders": list(b["labels"]), "dated": b["dated"]}
        )
        if p is not None:
            data["presets"].append(p)
    return changed


class PresetError(ValueError):
    pass


class PresetStore:
    """In-memory preset store backed by bakkis.db kv_store with fallback to mode_presets.json."""

    def __init__(self, path: Optional[Path] = None, dated_default: bool = False, *, storage: Optional[Any] = None) -> None:
        """``dated_default``: date folders for presets saved before they had their own switch."""
        self.path = Path(path) if path is not None else config_dir() / MODE_PRESETS_JSON
        self._storage = storage

        raw = None
        # 1. Try SQLite kv_store if no explicit custom path provided
        if path is None:
            try:
                st = self._get_storage()
                if st is not None:
                    db_data = st.get_kv("mode_presets")
                    if isinstance(db_data, dict):
                        raw = db_data
            except Exception:
                pass

        # 2. Fallback to disk JSON file
        if raw is None:
            raw = _read_json(self.path)

        self.data = normalize_preset_store(raw, dated_default)
        add_builtin_presets(self.data)
        if raw != self.data or (path is None and raw is not None):
            self.save()

    def _get_storage(self) -> Any:
        if self._storage is None:
            try:
                from ..storage import get_storage

                self._storage = get_storage()
            except Exception:
                pass
        return self._storage

    def save(self) -> None:
        st = self._get_storage()
        if st is not None:
            try:
                st.put_kv("mode_presets", self.data)
            except Exception:
                pass
        try:
            write_json_atomic(self.path, self.data)
        except OSError:
            pass

    # -- queries

    @property
    def presets(self) -> list[dict]:
        return self.data["presets"]

    def get(self, pid: str) -> Optional[dict]:
        for p in self.presets:
            if p["id"] == pid:
                return p
        return None

    def of_mode(self, mode: str) -> list[dict]:
        return [p for p in self.presets if p["mode"] == mode]

    def active(self, mode: str) -> dict:
        p = self.get(self.data[f"active_{norm_mode(mode)}_preset_id"])
        assert p is not None  # guaranteed by normalize_preset_store
        return p

    # -- mutations (each saves)

    def set_active(self, pid: str) -> dict:
        p = self.get(pid)
        if p is None:
            raise PresetError("Unknown preset")
        self.data[f"active_{p['mode']}_preset_id"] = pid
        self.save()
        return p

    def create(
        self, mode: str, name: str, labels: list, colors: list, folders: Optional[list] = None, dated: bool = False
    ) -> dict:
        p = coerce_preset(
            {"id": new_preset_id(), "mode": norm_mode(mode), "name": name, "labels": labels, "colors": colors,
             "folders": folders or [], "dated": bool(dated)}
        )
        if p is None:
            raise PresetError("Invalid preset")
        self.presets.append(p)
        self.save()
        return p

    def update(self, pid: str, **fields: object) -> dict:
        old = self.get(pid)
        if old is None:
            raise PresetError("Unknown preset")
        merged = dict(old)
        for k in ("name", "labels", "colors", "folders", "dated"):
            if k in fields:
                merged[k] = fields[k]
        p = coerce_preset(merged)
        if p is None:
            raise PresetError("Invalid preset")
        old.clear()
        old.update(p)
        self.save()
        return old

    def delete(self, pid: str) -> None:
        p = self.get(pid)
        if p is None:
            raise PresetError("Unknown preset")
        if len(self.of_mode(p["mode"])) <= 1:
            raise PresetError("Cannot delete the last preset of a mode")
        self.presets.remove(p)
        key = f"active_{p['mode']}_preset_id"
        if self.data[key] == pid:
            self.data[key] = self.of_mode(p["mode"])[0]["id"]
        self.save()

    def reset_grid_to_default_genres(self, pid: str) -> dict:
        p = self.get(pid)
        if p is None or p["mode"] != "grid":
            raise PresetError("Not a grid preset")
        d = default_grid_preset(use_genre_file=False)
        return self.update(pid, labels=d["labels"], colors=d["colors"], folders=d["folders"])
