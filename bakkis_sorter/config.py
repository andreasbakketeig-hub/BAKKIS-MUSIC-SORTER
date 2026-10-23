"""Paths, user settings and mode presets.

All user data lives in ``~/Library/Application Support/BAKKIS MUSIC SORTER/`` (override with env
``BAKKIS_CONFIG_DIR``). On first run, v1 JSON files from the project folder are migrated.
The ``mode_presets.json`` format is identical to v1 so both versions can share it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME

SETTINGS_JSON = "settings.json"
LEGACY_SETTINGS_JSON = "bakkis_settings.json"
MODE_PRESETS_JSON = "mode_presets.json"
GENRE_LABELS_JSON = "genre_labels.json"
MIGRATION_MARKER = ".migrated_from_v1"

MODES = ("swipe", "grid")
SWIPE_SLOTS = 3
GRID_SLOTS = 9

# Auto-play start choices. Tracks shorter than start + 30 s start about a third of the way in (see app.js).
PLAYBACK_START_OPTIONS_SEC: tuple[float, ...] = (0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 240.0, 300.0, 360.0)
DEFAULT_PLAYBACK_START_SEC = 120.0

DEFAULT_SWIPE_LABELS = ["Keep", "Archive", "Favorite"]
DEFAULT_SWIPE_COLORS = ["#00D6B4", "#FF2D55", "#FFF200"]
DEFAULT_SWIPE_FOLDERS = ["KEEP", "ARCHIVE", "FAVORITES"]

DEFAULT_GENRE_LABELS = [
    "House",
    "Tech House",
    "Deep House",
    "Minimal",
    "Afro House",
    "Melodic",
    "Techno",
    "Garage",
    "Other",
]

# Fixed palette for grid tiles (name, hex).
GENRE_PALETTE: tuple[tuple[str, str], ...] = (
    ("Teal", "#00D6B4"),
    ("Blue", "#18A0FB"),
    ("Purple", "#7C5CFF"),
    ("Gray", "#8A8F98"),
    ("Orange", "#FF7A1A"),
    ("Pink", "#D65CFF"),
    ("Red", "#FF2D55"),
    ("Yellow", "#FFF200"),
    ("Dark", "#2A2F36"),
)
GENRE_PALETTE_HEX_SET = frozenset(hx.upper() for _, hx in GENRE_PALETTE)
DEFAULT_GENRE_COLORS = [hx for _, hx in GENRE_PALETTE]


# --------------------------------------------------------------------------- paths


def bundle_dir() -> Path:
    """Read-only assets: the PyInstaller extract dir when frozen, else the repo root."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Writable user config dir (created on demand)."""
    override = os.environ.get("BAKKIS_CONFIG_DIR", "").strip()
    if override:
        d = Path(override).expanduser()
    else:
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def migrate_from_v1(v1_dir: Optional[Path] = None) -> list[str]:
    """Copy v1 JSON files into the config dir once. Returns the file names that were copied.

    Never overwrites existing v2 files. A marker file prevents re-running after the user
    deletes or edits their settings.
    """
    dest = config_dir()
    marker = dest / MIGRATION_MARKER
    if marker.exists():
        return []
    src = Path(v1_dir) if v1_dir is not None else bundle_dir()
    copied: list[str] = []
    if src.resolve() != dest.resolve():
        settings_src = src / SETTINGS_JSON
        if not settings_src.is_file():
            settings_src = src / LEGACY_SETTINGS_JSON
        pairs = [
            (settings_src, SETTINGS_JSON),
            (src / MODE_PRESETS_JSON, MODE_PRESETS_JSON),
            (src / GENRE_LABELS_JSON, GENRE_LABELS_JSON),
        ]
        for s, name in pairs:
            d = dest / name
            if d.exists() or not s.is_file():
                continue
            try:
                shutil.copy2(s, d)
                copied.append(name)
            except OSError:
                pass
    try:
        marker.write_text("1\n", encoding="utf-8")
    except OSError:
        pass
    return copied


# --------------------------------------------------------------------------- coercion helpers


def coerce_bool(val: object) -> bool:
    """JSON / hand-edited booleans (``bool("false")`` would be wrong)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def normalize_hex(h: object, fallback: str = DEFAULT_GENRE_COLORS[0]) -> str:
    s = str(h or "").strip()
    if not s:
        return fallback.upper()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    try:
        int(s[1:], 16)
    except ValueError:
        return fallback.upper()
    return s.upper() if len(s) == 7 else fallback.upper()


def coerce_palette_hex(h: object, fallback: str) -> str:
    n = normalize_hex(h, fallback)
    if n in GENRE_PALETTE_HEX_SET:
        return n
    fb = normalize_hex(fallback)
    return fb if fb in GENRE_PALETTE_HEX_SET else DEFAULT_GENRE_COLORS[0]


def safe_segment(name: object, fallback: str = "Preset") -> str:
    """Sanitize one path segment (preset, bucket, genre folder names)."""
    s = str(name or "").strip().replace("/", "-").replace("\\", "-").replace(":", "-").replace("\x00", "")
    s = s.strip(". ") or fallback
    return s[:120]


def norm_mode(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in ("genre", "grid"):
        return "grid"
    return "swipe"


# --------------------------------------------------------------------------- settings


def default_settings() -> dict:
    return {
        "default_folder_path": None,
        "auto_load_default_folder": False,
        "default_mode_on_startup": "swipe",
        "startup_preset_id": None,
        "playback_start_seconds": DEFAULT_PLAYBACK_START_SEC,
        "track_order": "random",
        "include_subfolders": True,
        "sort_confirmation_sound": True,
        "swipe_archive_hotkey": "down",
        "create_dated_sorting_sessions": True,
        "cleanup_empty_source_folders_after_sort": False,
        "show_help_guide_on_startup": False,
        "show_queue_panel_on_startup": False,
        "volume": 80,
        # New in v2
        "file_operation": "move",
        "hide_copied_on_rescan": True,
        "swipe_keep_direction": "right",
        "end_of_track": "skip",
        "move_warning_acknowledged": False,
        "guide_seen": False,  # the guide opens once on first launch
        "coach_done": False,  # first-session hint above the tiles
        "check_for_updates": True,  # one request to GitHub Releases at startup
        "advanced_features": False,  # playlist export, BPM/key filter
    }


def normalize_settings(raw: object) -> dict:
    """Return a clean settings dict: known keys only, valid values, v1 aliases migrated."""
    src = raw if isinstance(raw, dict) else {}
    out = default_settings()

    dfp = src.get("default_folder_path")
    out["default_folder_path"] = str(dfp).strip() if isinstance(dfp, str) and dfp.strip() else None

    for k in (
        "auto_load_default_folder",
        "include_subfolders",
        "sort_confirmation_sound",
        "create_dated_sorting_sessions",
        "cleanup_empty_source_folders_after_sort",
        "show_help_guide_on_startup",
        "show_queue_panel_on_startup",
        "hide_copied_on_rescan",
        "move_warning_acknowledged",
        "guide_seen",
        "coach_done",
        "check_for_updates",
        "advanced_features",
    ):
        if k in src:
            out[k] = coerce_bool(src[k])

    out["default_mode_on_startup"] = norm_mode(src.get("default_mode_on_startup", "swipe"))

    sp = src.get("startup_preset_id")
    out["startup_preset_id"] = sp.strip() if isinstance(sp, str) and sp.strip() else None

    try:
        pss = float(src.get("playback_start_seconds", DEFAULT_PLAYBACK_START_SEC))
    except (TypeError, ValueError):
        pss = DEFAULT_PLAYBACK_START_SEC
    out["playback_start_seconds"] = min(PLAYBACK_START_OPTIONS_SEC, key=lambda x: abs(x - pss))

    # v1 stored both track_order_mode and track_order.
    order = src.get("track_order_mode") or src.get("track_order") or "random"
    out["track_order"] = order if order in ("random", "in_order") else "random"

    out["swipe_archive_hotkey"] = "up" if str(src.get("swipe_archive_hotkey", "")).strip().lower() == "up" else "down"

    try:
        vol = int(float(src.get("volume", 80)))
    except (TypeError, ValueError):
        vol = 80
    out["volume"] = max(0, min(100, vol))

    out["file_operation"] = "copy" if str(src.get("file_operation", "")).lower() == "copy" else "move"
    out["swipe_keep_direction"] = "left" if str(src.get("swipe_keep_direction", "")).lower() == "left" else "right"
    out["end_of_track"] = "stop" if str(src.get("end_of_track", "")).lower() == "stop" else "skip"
    return out


def settings_path() -> Path:
    return config_dir() / SETTINGS_JSON


def load_settings() -> dict:
    return normalize_settings(_read_json(settings_path()))


def save_settings(data: dict) -> dict:
    clean = normalize_settings(data)
    write_json_atomic(settings_path(), clean)
    return clean


def patch_settings(**changes: object) -> dict:
    data = load_settings()
    data.update(changes)
    return save_settings(data)


# --------------------------------------------------------------------------- mode presets


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
    }


def coerce_preset(blob: object) -> Optional[dict]:
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
    return {"id": pid, "name": name, "mode": mode, "labels": ls, "colors": cs, "folders": fs}


def normalize_preset_store(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    presets: list[dict] = []
    seen: set[str] = set()
    for item in data.get("presets") or []:
        p = coerce_preset(item)
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
    return {
        "presets": presets,
        "active_swipe_preset_id": aid if aid in swipe_ids else swipe_ids[0],
        "active_grid_preset_id": gid if gid in grid_ids else grid_ids[0],
    }


class PresetError(ValueError):
    pass


class PresetStore:
    """In-memory preset store backed by ``mode_presets.json``. Not thread-safe; use from one thread."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else config_dir() / MODE_PRESETS_JSON
        raw = _read_json(self.path)
        self.data = normalize_preset_store(raw)
        if raw != self.data:
            self.save()

    def save(self) -> None:
        write_json_atomic(self.path, self.data)

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

    def create(self, mode: str, name: str, labels: list, colors: list, folders: Optional[list] = None) -> dict:
        p = coerce_preset(
            {"id": new_preset_id(), "mode": norm_mode(mode), "name": name, "labels": labels, "colors": colors, "folders": folders or []}
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
        for k in ("name", "labels", "colors", "folders"):
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
