#!/usr/bin/env python3
"""
BAKKIS MUSIC SORTER (Tkinter edition)

Simple, reliable desktop app for sorting music files.
UI: Tkinter. Playback: mpv (JSON IPC) for audio and seeking.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
import queue
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Literal, Optional, Union

import tkinter as tk

ButtonKind = Literal["panel", "accent", "archive", "favorite", "transport"]
JumpUiKey = Union[int, Literal["outro", "pb_start"]]
SortingMode = Literal["swipe", "grid"]
TrackOrder = Literal["random", "in_order"]
from mutagen import File as MutagenFile
from tkinter import filedialog, messagebox, simpledialog

# Default landscape window; genre grid + right panel fit without main-window scrolling.
_DEFAULT_WIN_W = 1300
_DEFAULT_WIN_H = 940
_WIN_MIN_W = 1200
_WIN_MIN_H = 860
# Shared horizontal inset for main-column blocks (header, progress, jump, transport, sort shells, footer).
_CONTENT_GUTTER = 24
# Fixed height for Swipe vs Grid swap zone — avoids vertical jump when switching modes (inner content fills).
_ACTION_ZONE_FIXED_HEIGHT_PX = 392
# Vertical rhythm between major blocks in the main column.
_SECTION_GAP_Y_TIGHT = 4
_SECTION_GAP_Y = 8
# Sidebar reservations (queue/right · guide/left): used so main column wraplengths stay correct.
_QUEUE_PANEL_WIDTH_RESERVE = 268
_GUIDE_PANEL_WIDTH_RESERVE = 268
_GUIDE_SIDEBAR_TEXT_WRAP = 220

# Lowercase extensions including leading dot — single source of truth for scan / validation / playback checks.
SUPPORTED_AUDIO_FORMATS = frozenset(
    {
        ".mp3",
        ".wav",
        ".aiff",
        ".aif",
        ".flac",
        ".m4a",
        ".aac",
        ".alac",
        ".ogg",
    }
)


def path_has_supported_audio_format(path: Path) -> bool:
    """True if ``path`` has an extension we treat as audio everywhere in the app."""
    return path.suffix.lower() in SUPPORTED_AUDIO_FORMATS


def supported_audio_formats_display() -> str:
    """Comma-separated list for dialogs (sorted)."""
    return ", ".join(sorted(SUPPORTED_AUDIO_FORMATS))


_HAIR_SPACE = "\u200a"  # HAIR SPACE — subtle faux letter-spacing (Tk has no CSS tracking).


def _shortcut_caption_loose(s: str) -> str:
    """Loosen text inside ``[...]`` for monospace shortcut captions."""
    t = s.strip()
    if len(t) < 3 or not (t.startswith("[") and t.endswith("]")):
        return s
    body = t[1:-1]
    if len(body) <= 1:
        if len(body) == 1:
            return "[" + _HAIR_SPACE + body + _HAIR_SPACE + "]"
        return s
    return "[" + _HAIR_SPACE.join(body) + "]"


# Unified action tile layout (utility bar · keep/archive/favorite · order · genre).
_ACTION_TILE_ROW_GAP_PX = 10
_ACTION_TILE_FRAME_PAD_PX = 4
_ACTION_TILE_GRID_GAP_PX = 6
_SWIPE_SORT_GRID_PAD = 3
_ACTION_TILE_ICON_FONT = ("Helvetica Neue", 22)
_ACTION_TILE_TITLE_FONT = ("Helvetica Neue", 13, "bold")
_ACTION_TILE_SHORTCUT_FONT = ("Menlo", 10)
_ACTION_TILE_HOVER_MIX = 0.058


def _should_ignore_scan_file(name: str) -> bool:
    """Skip junk / hidden / AppleDouble files during folder scans."""
    if name == ".DS_Store":
        return True
    if name.startswith("._"):
        return True
    if name.startswith(".") and name not in (".", ".."):
        return True
    return False


def _should_skip_scan_subdirectory(name: str) -> bool:
    """Skip hidden / dot directories under the scan root (e.g. ``.Trash``, ``.git``)."""
    return bool(name.startswith(".") and name not in (".", ".."))


# Auto-play start: user picks one of ``PLAYBACK_START_OPTIONS_SEC``; if track length < start + 30s, begin at 0:00.
PLAYBACK_START_OPTIONS_SEC: tuple[float, ...] = (0.0, 30.0, 60.0, 90.0, 120.0, 180.0)
DEFAULT_PLAYBACK_START_SEC = 120.0  # 2:00
SETTINGS_JSON = "settings.json"
LEGACY_SETTINGS_JSON = "bakkis_settings.json"
KEEP_NAME = "KEEP"
ARCHIVE_NAME = "ARCHIVE"
FAVORITES_NAME = "FAVORITES"
GENRES_NAME = "GENRES"
MODE_PRESETS_JSON = "mode_presets.json"
LEGACY_SKIP_SCAN_DIR_NAMES = frozenset(
    {KEEP_NAME.lower(), ARCHIVE_NAME.lower(), FAVORITES_NAME.lower(), GENRES_NAME.lower()},
)


def _norm_sorting_mode_value(raw: object) -> SortingMode:
    s = str(raw or "").strip().lower()
    if s in ("cleanup", "swipe", "tinder"):
        return "swipe"
    if s in ("genre", "grid"):
        return "grid"
    return "swipe"


def _new_preset_id() -> str:
    return uuid.uuid4().hex


def _mode_presets_path() -> Path:
    return _user_config_dir() / MODE_PRESETS_JSON


def _default_swipe_preset_dict() -> dict:
    pid = _new_preset_id()
    return {
        "id": pid,
        "name": "Default swipe",
        "mode": "swipe",
        "labels": ["Keep", "Archive", "Favorite"],
        "colors": ["#00D6B4", "#FF2D55", "#FFF200"],
        "folders": [KEEP_NAME, ARCHIVE_NAME, FAVORITES_NAME],
    }


def _default_grid_preset_dict_from_genre_file() -> dict:
    labels, colors = load_genre_config()
    pid = _new_preset_id()
    folders = [safe_genre_folder_name(lb) for lb in labels[:9]]
    return {
        "id": pid,
        "name": "Default grid",
        "mode": "grid",
        "labels": list(labels[:9]),
        "colors": list(colors[:9]),
        "folders": folders,
    }


def _coerce_swipe_preset_blob(blob: object) -> Optional[dict]:
    if not isinstance(blob, dict):
        return None
    if str(blob.get("mode", "")).lower() != "swipe":
        return None
    pid = str(blob.get("id") or "").strip() or _new_preset_id()
    name = str(blob.get("name") or "Swipe preset").strip() or "Swipe preset"
    labels = blob.get("labels")
    colors = blob.get("colors")
    folders = blob.get("folders")
    if not isinstance(labels, list):
        labels = ["Keep", "Archive", "Favorite"]
    if not isinstance(colors, list):
        colors = ["#00D6B4", "#FF2D55", "#FFF200"]
    if not isinstance(folders, list):
        folders = [KEEP_NAME, ARCHIVE_NAME, FAVORITES_NAME]
    ls = [str(x).strip() or "?" for x in labels[:3]]
    while len(ls) < 3:
        ls.append(["Keep", "Archive", "Favorite"][len(ls)])
    default_c = ["#00D6B4", "#FF2D55", "#FFF200"]
    cs = [_normalize_hex(str(colors[i])) if i < len(colors) else default_c[i] for i in range(3)]
    fs = [str(x).strip() or f"OUT{i}" for i, x in enumerate(folders[:3])]
    while len(fs) < 3:
        fs.append([KEEP_NAME, ARCHIVE_NAME, FAVORITES_NAME][len(fs)])
    return {"id": pid, "name": name, "mode": "swipe", "labels": ls, "colors": cs, "folders": fs}


def _coerce_grid_preset_blob(blob: object) -> Optional[dict]:
    if not isinstance(blob, dict):
        return None
    if str(blob.get("mode", "")).lower() != "grid":
        return None
    pid = str(blob.get("id") or "").strip() or _new_preset_id()
    name = str(blob.get("name") or "Grid preset").strip() or "Grid preset"
    labels = blob.get("labels")
    colors = blob.get("colors")
    folders = blob.get("folders")
    if not isinstance(labels, list):
        return None
    if not isinstance(colors, list):
        colors = []
    if not isinstance(folders, list):
        folders = []
    ls: list[str] = []
    for i in range(9):
        if i < len(labels):
            ls.append(str(labels[i]).strip() or "?")
        else:
            ls.append(DEFAULT_GENRE_LABELS[i])
    cs_out: list[str] = []
    for i in range(9):
        if i < len(colors):
            cs_out.append(_coerce_palette_hex(str(colors[i]), DEFAULT_GENRE_COLORS[i]))
        else:
            cs_out.append(DEFAULT_GENRE_COLORS[i])
    fs_out: list[str] = []
    for i in range(9):
        if i < len(folders) and str(folders[i]).strip():
            fs_out.append(str(folders[i]).strip())
        else:
            fs_out.append(safe_genre_folder_name(ls[i]))
    return {"id": pid, "name": name, "mode": "grid", "labels": ls, "colors": cs_out, "folders": fs_out}


def _normalize_mode_preset_store(data: dict) -> dict:
    presets_raw = data.get("presets")
    presets: list[dict] = []
    if isinstance(presets_raw, list):
        for item in presets_raw:
            if not isinstance(item, dict):
                continue
            m = str(item.get("mode", "")).lower()
            if m == "swipe":
                co = _coerce_swipe_preset_blob(item)
                if co:
                    presets.append(co)
            elif m == "grid":
                co = _coerce_grid_preset_blob(item)
                if co:
                    presets.append(co)
    if not presets:
        presets = [_default_swipe_preset_dict(), _default_grid_preset_dict_from_genre_file()]
    swipe_ids = [p["id"] for p in presets if p.get("mode") == "swipe"]
    grid_ids = [p["id"] for p in presets if p.get("mode") == "grid"]
    aid = str(data.get("active_swipe_preset_id") or "")
    gid = str(data.get("active_grid_preset_id") or "")
    if aid not in swipe_ids:
        aid = swipe_ids[0] if swipe_ids else presets[0]["id"]
    if gid not in grid_ids:
        gid = grid_ids[0] if grid_ids else presets[-1]["id"]
    return {"presets": presets, "active_swipe_preset_id": aid, "active_grid_preset_id": gid}


def load_mode_preset_store() -> dict:
    path = _mode_presets_path()
    raw: dict = {}
    try:
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw = loaded
    except (OSError, json.JSONDecodeError):
        raw = {}
    normalized = _normalize_mode_preset_store(raw)
    try:
        if not path.is_file() or not raw.get("presets"):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(normalized, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
    return normalized


def save_mode_preset_store(store: dict) -> None:
    path = _mode_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def preset_by_id(store: dict, pid: str) -> Optional[dict]:
    for p in store.get("presets") or []:
        if isinstance(p, dict) and p.get("id") == pid:
            return p
    return None


def presets_of_mode(store: dict, mode: Literal["swipe", "grid"]) -> list[dict]:
    return [p for p in (store.get("presets") or []) if isinstance(p, dict) and p.get("mode") == mode]


def format_preset_menu_label(p: dict) -> str:
    m = str(p.get("mode", "")).lower()
    prefix = "Swipe" if m == "swipe" else "Grid" if m == "grid" else "?"
    name = str(p.get("name") or "?").strip()
    return f"{prefix} · {name}"


def active_swipe_preset(store: dict) -> dict:
    pid = str(store.get("active_swipe_preset_id") or "")
    p = preset_by_id(store, pid)
    if p and p.get("mode") == "swipe":
        co = _coerce_swipe_preset_blob(p)
        if co:
            return co
    for q in presets_of_mode(store, "swipe"):
        co = _coerce_swipe_preset_blob(q)
        if co:
            return co
    return _coerce_swipe_preset_blob(_default_swipe_preset_dict()) or _default_swipe_preset_dict()


def active_grid_preset(store: dict) -> dict:
    pid = str(store.get("active_grid_preset_id") or "")
    p = preset_by_id(store, pid)
    if p and p.get("mode") == "grid":
        co = _coerce_grid_preset_blob(p)
        if co:
            return co
    for q in presets_of_mode(store, "grid"):
        co = _coerce_grid_preset_blob(q)
        if co:
            return co
    return _coerce_grid_preset_blob(_default_grid_preset_dict_from_genre_file()) or _default_grid_preset_dict_from_genre_file()


def collect_skip_scan_dir_names(store: dict) -> frozenset[str]:
    out: set[str] = set(LEGACY_SKIP_SCAN_DIR_NAMES)
    for p in store.get("presets") or []:
        if not isinstance(p, dict):
            continue
        mode = str(p.get("mode", "")).lower()
        folders = p.get("folders")
        if not isinstance(folders, list):
            continue
        if mode == "swipe":
            for fn in folders[:3]:
                if isinstance(fn, str) and fn.strip():
                    out.add(fn.strip().lower())
        elif mode == "grid":
            for fn in folders[:9]:
                if isinstance(fn, str) and fn.strip():
                    out.add(safe_genre_folder_name(fn).lower())
                    out.add(fn.strip().lower())
    return frozenset(out)

# Background scan / preload tuning
_SCAN_QUEUE_POLL_MS = 55
_SCAN_PROGRESS_EVERY = 40
_PRELOAD_LOOKAHEAD = 8
_SESSION_TEMP_PRUNE_INTERVAL_SEC = 300.0
_REPLAY_KEY_DEBOUNCE_SEC = 0.055
_BAKKIS_TEMP_GLOB = "bakkis_*.wav"
_BAKKIS_TEMP_MAX_AGE_SEC = 48 * 3600

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

# Fixed UI palette for Genre Mode tiles (Edit Genres + tile styling only).
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
GENRE_PALETTE_HEX_SET = {hx.upper() for _, hx in GENRE_PALETTE}
GENRE_PALETTE_NAME_TO_HEX: dict[str, str] = {name: hx for name, hx in GENRE_PALETTE}
DEFAULT_GENRE_COLORS: list[str] = [hx for _, hx in GENRE_PALETTE]


def _normalize_hex(h: str) -> str:
    s = (h or "").strip()
    if not s:
        return DEFAULT_GENRE_COLORS[0]
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4 and s.startswith("#"):
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s if len(s) == 7 else DEFAULT_GENRE_COLORS[0]


def _coerce_palette_hex(h: str, fallback: str) -> str:
    n = _normalize_hex(h).upper()
    if n in GENRE_PALETTE_HEX_SET:
        return n
    fb = _normalize_hex(fallback).upper()
    return fb if fb in GENRE_PALETTE_HEX_SET else DEFAULT_GENRE_COLORS[0].upper()


def _hex_rgb(h: str) -> tuple[int, int, int]:
    hx = _normalize_hex(h)
    return int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(x)) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _fg_for_genre_bg(hex_bg: str) -> str:
    # Threshold ~0.50 so bright teal (#00D6B4, L≈0.514) matches yellow: black label + digit.
    return "#000000" if _relative_luminance(_hex_rgb(hex_bg)) > 0.50 else "#F2F2F2"


def _genre_shortcut_fg(main_fg: str) -> str:
    """Digit under genre name: dark on bright tiles; full-contrast white on saturated/dark tiles."""
    return "#1A1A1A" if main_fg == "#000000" else "#F2F2F2"


def _mix_hex_toward_black(hex_bg: str, amount: float) -> str:
    """Blend ``hex_bg`` toward black by ``amount`` in 0..1 (pressed/active)."""
    r, g, b = _hex_rgb(hex_bg)
    t = max(0.0, min(1.0, amount))
    r = int(r * (1.0 - t))
    g = int(g * (1.0 - t))
    b = int(b * (1.0 - t))
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix_hex_toward_white(hex_bg: str, amount: float) -> str:
    """Blend ``hex_bg`` toward white by ``amount`` in 0..1 (hover lift)."""
    r, g, b = _hex_rgb(hex_bg)
    t = max(0.0, min(1.0, amount))
    r = int(r + (255 - r) * t)
    g = int(g + (255 - g) * t)
    b = int(b + (255 - b) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _palette_name_for_hex(h: str) -> str:
    u = _coerce_palette_hex(h, DEFAULT_GENRE_COLORS[0]).upper()
    for name, hx in GENRE_PALETTE:
        if hx.upper() == u:
            return name
    return "Gray"


def _bundle_dir() -> Path:
    """Read-only assets next to the script, or PyInstaller extract dir (``_MEIPASS``)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _user_config_dir() -> Path:
    """Writable config: project folder when running from source; Application Support when bundled (macOS)."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            d = Path.home() / "Library" / "Application Support" / "BAKKIS MUSIC SORTER"
        else:
            d = Path.home() / ".bakkis_music_sorter"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return d
    return Path(__file__).resolve().parent


def _app_dir() -> Path:
    """Directory for user-writable JSON (settings, genre labels)."""
    return _user_config_dir()


def ensure_user_config_seeded_from_bundle() -> None:
    """First launch of the .app: copy default JSON from the bundle into Application Support."""
    if not getattr(sys, "frozen", False):
        return
    dest_root = _user_config_dir()
    bundle = _bundle_dir()
    for name in ("genre_labels.json", "bakkis_settings.json"):
        dest = dest_root / name
        if dest.exists():
            continue
        src = bundle / name
        if not src.is_file():
            continue
        try:
            shutil.copy2(src, dest)
        except OSError:
            pass
    dest_settings = dest_root / SETTINGS_JSON
    if not dest_settings.exists():
        src_new = bundle / SETTINGS_JSON
        if src_new.is_file():
            try:
                shutil.copy2(src_new, dest_settings)
            except OSError:
                pass
        elif not dest_settings.exists():
            src_legacy = bundle / LEGACY_SETTINGS_JSON
            if src_legacy.is_file():
                try:
                    shutil.copy2(src_legacy, dest_settings)
                except OSError:
                    pass


def _genre_labels_path() -> Path:
    return _user_config_dir() / "genre_labels.json"


def _settings_json_path() -> Path:
    return _user_config_dir() / SETTINGS_JSON


def _legacy_settings_json_path() -> Path:
    return _user_config_dir() / LEGACY_SETTINGS_JSON


def _default_user_settings_blob() -> dict:
    return {
        "default_folder_path": None,
        "auto_load_default_folder": False,
        "playback_start_seconds": DEFAULT_PLAYBACK_START_SEC,
        "default_mode_on_startup": "swipe",
        "startup_preset_id": None,
        "track_order_mode": "random",
        "track_order": "random",
        "include_subfolders": True,
        "show_help_guide_on_startup": False,
        "show_queue_panel_on_startup": False,
    }


def _normalize_user_settings_dict(data: dict) -> None:
    """Coerce loaded/patched settings in place; safe defaults for missing or invalid keys."""
    defaults = _default_user_settings_blob()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    dfp = data.get("default_folder_path")
    if dfp is not None:
        if not isinstance(dfp, str) or not str(dfp).strip():
            data["default_folder_path"] = None
        else:
            data["default_folder_path"] = str(dfp).strip()
    try:
        pss = float(data.get("playback_start_seconds", DEFAULT_PLAYBACK_START_SEC))
    except (TypeError, ValueError):
        pss = DEFAULT_PLAYBACK_START_SEC
    if pss not in PLAYBACK_START_OPTIONS_SEC:
        pss = min(PLAYBACK_START_OPTIONS_SEC, key=lambda x: abs(x - pss))
    data["playback_start_seconds"] = pss
    dm = data.get("default_mode_on_startup", "swipe")
    data["default_mode_on_startup"] = "swipe" if _norm_sorting_mode_value(dm) == "swipe" else "grid"
    sp = data.get("startup_preset_id")
    if sp is None or (isinstance(sp, str) and not sp.strip()):
        data["startup_preset_id"] = None
    elif isinstance(sp, str):
        data["startup_preset_id"] = sp.strip()
    else:
        data["startup_preset_id"] = None
    tr = data.get("track_order_mode") or data.get("track_order", "random")
    data["track_order_mode"] = tr if tr in ("random", "in_order") else "random"
    data["track_order"] = data["track_order_mode"]
    data["auto_load_default_folder"] = bool(data.get("auto_load_default_folder", False))
    data["include_subfolders"] = bool(data.get("include_subfolders", True))
    data["show_help_guide_on_startup"] = bool(data.get("show_help_guide_on_startup", False))
    data["show_queue_panel_on_startup"] = bool(data.get("show_queue_panel_on_startup", False))


def read_user_settings() -> dict:
    raw: dict = {}
    path = _settings_json_path()
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    if not raw:
        legacy = _legacy_settings_json_path()
        if legacy.is_file():
            try:
                with open(legacy, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw = loaded
            except (OSError, json.JSONDecodeError):
                raw = {}
    data = dict(_default_user_settings_blob())
    for k, v in raw.items():
        if k in data or k == "default_folder_path":
            data[k] = v
    _normalize_user_settings_dict(data)
    return data


def write_user_settings_blob(data: dict) -> None:
    _normalize_user_settings_dict(data)
    path = _settings_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def patch_user_settings(**kwargs: object) -> dict:
    data = read_user_settings()
    for k, v in kwargs.items():
        data[str(k)] = v
    _normalize_user_settings_dict(data)
    write_user_settings_blob(data)
    return data


def playback_start_choice_labels() -> tuple[str, ...]:
    return ("0:00", "0:30", "1:00", "1:30", "2:00", "3:00")


def playback_start_label_to_sec(label: str) -> float:
    m = {
        "0:00": 0.0,
        "0:30": 30.0,
        "1:00": 60.0,
        "1:30": 90.0,
        "2:00": 120.0,
        "3:00": 180.0,
    }
    return m.get(str(label).strip(), DEFAULT_PLAYBACK_START_SEC)


def playback_sec_to_choice_label(sec: float) -> str:
    if sec in PLAYBACK_START_OPTIONS_SEC:
        return playback_start_choice_labels()[PLAYBACK_START_OPTIONS_SEC.index(sec)]
    nearest = min(PLAYBACK_START_OPTIONS_SEC, key=lambda x: abs(x - float(sec)))
    return playback_start_choice_labels()[PLAYBACK_START_OPTIONS_SEC.index(nearest)]


def load_default_mode_on_startup() -> SortingMode:
    return _norm_sorting_mode_value(read_user_settings().get("default_mode_on_startup", "swipe"))


def load_playback_start_seconds() -> float:
    return float(read_user_settings()["playback_start_seconds"])


def load_show_help_guide_on_startup() -> bool:
    return bool(read_user_settings()["show_help_guide_on_startup"])


def load_show_queue_panel_on_startup() -> bool:
    return bool(read_user_settings()["show_queue_panel_on_startup"])


def _bundled_mpv_executable() -> Optional[Path]:
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return None
    root = Path(sys._MEIPASS)
    for candidate in (root / "mpv", root / "bin" / "mpv"):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def _which_mpv() -> Optional[str]:
    bundled = _bundled_mpv_executable()
    if bundled is not None:
        return str(bundled)
    return shutil.which("mpv")


def load_track_order() -> TrackOrder:
    v = read_user_settings().get("track_order_mode", "random")
    return v if v in ("random", "in_order") else "random"


def save_track_order_setting(order: TrackOrder) -> None:
    patch_user_settings(track_order_mode=order, track_order=order)


def load_include_subfolders() -> bool:
    return bool(read_user_settings()["include_subfolders"])


def save_include_subfolders_setting(on: bool) -> None:
    patch_user_settings(include_subfolders=bool(on))


def load_default_folder_path() -> Optional[str]:
    raw = read_user_settings().get("default_folder_path")
    if isinstance(raw, str):
        s = raw.strip()
        if s:
            return s
    return None


def save_default_folder_path(folder: Optional[Union[str, Path]]) -> None:
    if folder is None:
        patch_user_settings(default_folder_path=None)
        return
    s = str(folder).strip()
    patch_user_settings(default_folder_path=s if s else None)


def load_auto_load_default_folder() -> bool:
    return bool(read_user_settings()["auto_load_default_folder"])


def save_auto_load_default_folder(on: bool) -> None:
    patch_user_settings(auto_load_default_folder=bool(on))


def load_genre_config() -> tuple[list[str], list[str]]:
    """Load 9 genre labels + palette hexes from ``genre_labels.json`` (legacy files: colors default)."""
    path = _genre_labels_path()
    labels: list[str] = []
    colors: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_GENRE_LABELS), list(DEFAULT_GENRE_COLORS)

    raw_labels: Optional[list] = None
    if isinstance(data, dict) and isinstance(data.get("labels"), list):
        raw_labels = data["labels"]
        raw_colors = data.get("colors")
    elif isinstance(data, list):
        raw_labels = data
        raw_colors = None
    else:
        return list(DEFAULT_GENRE_LABELS), list(DEFAULT_GENRE_COLORS)

    for x in raw_labels[:9]:
        labels.append(str(x).strip() if str(x).strip() else "?")
    while len(labels) < 9:
        labels.append(DEFAULT_GENRE_LABELS[len(labels)])

    if isinstance(raw_colors, list):
        for i in range(9):
            if i < len(raw_colors):
                fb = DEFAULT_GENRE_COLORS[i] if i < len(DEFAULT_GENRE_COLORS) else DEFAULT_GENRE_COLORS[0]
                colors.append(_coerce_palette_hex(str(raw_colors[i]), fb))
            else:
                colors.append(DEFAULT_GENRE_COLORS[i])
    else:
        colors = list(DEFAULT_GENRE_COLORS)

    return labels[:9], colors[:9]


def save_genre_config(labels: list[str], colors: list[str]) -> None:
    ls = list(labels[:9])
    cs = [
        _coerce_palette_hex(colors[i], DEFAULT_GENRE_COLORS[i]) if i < len(colors) else DEFAULT_GENRE_COLORS[i]
        for i in range(9)
    ]
    while len(ls) < 9:
        ls.append(DEFAULT_GENRE_LABELS[len(ls)])
    path = _genre_labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"labels": ls, "colors": cs}, f, indent=2, ensure_ascii=False)


def safe_genre_folder_name(label: str) -> str:
    s = label.strip().replace("/", "-").replace("\\", "-").replace(":", "-")
    s = s.replace("\x00", "")
    if not s:
        s = "Genre"
    return s[:120]


def ensure_genre_folders(root: Path, folder_names: list[str]) -> None:
    base = root / GENRES_NAME
    base.mkdir(parents=True, exist_ok=True)
    for raw in folder_names[:9]:
        (base / safe_genre_folder_name(str(raw))).mkdir(parents=True, exist_ok=True)


def iter_audio_files_under(
    folder: Path,
    *,
    recursive: bool = True,
    skip_dir_names: Optional[frozenset[str]] = None,
):
    """Yield resolved audio file paths. When ``recursive`` is False, only the folder's top-level files."""
    root_folder = folder.resolve()
    skip = LEGACY_SKIP_SCAN_DIR_NAMES if skip_dir_names is None else skip_dir_names
    if recursive:
        for root, dirs, files in os.walk(root_folder, topdown=True):
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in skip and not _should_skip_scan_subdirectory(d)
            ]
            for name in files:
                if _should_ignore_scan_file(name):
                    continue
                p = (Path(root) / name).resolve()
                if path_has_supported_audio_format(p):
                    yield p
    else:
        try:
            for name in sorted(os.listdir(root_folder)):
                if _should_ignore_scan_file(name):
                    continue
                p = (root_folder / name).resolve()
                if p.is_file() and path_has_supported_audio_format(p):
                    yield p
        except OSError:
            return


def scan_audio_files(
    folder: Path,
    *,
    recursive: bool = True,
    skip_dir_names: Optional[frozenset[str]] = None,
) -> list[Path]:
    return list(iter_audio_files_under(folder, recursive=recursive, skip_dir_names=skip_dir_names))


def _relative_path_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _source_folder_display(path: Path, root: Path) -> str:
    """Human-readable parent path under ``root``, e.g. ``Promos / Label Name``."""
    rel = Path(_relative_path_posix(path, root))
    parent = rel.parent
    if parent == Path("."):
        return ""
    return " / ".join(parent.parts)


def build_track_relative_index(paths: list[Path], root: Path) -> dict[str, str]:
    """Map ``str(path.resolve())`` → relative POSIX path from ``root`` (for future use)."""
    r = root.resolve()
    out: dict[str, str] = {}
    for p in paths:
        out[str(p.resolve())] = _relative_path_posix(p, r)
    return out


def ensure_sort_folders(
    root: Path,
    swipe_output_folder_names: Optional[tuple[str, str, str]] = None,
) -> dict[str, Path]:
    if swipe_output_folder_names is None:
        swipe_output_folder_names = (KEEP_NAME, ARCHIVE_NAME, FAVORITES_NAME)
    kn, an, fn = swipe_output_folder_names
    out = {"keep": root / kn, "archive": root / an, "favorites": root / fn}
    for d in out.values():
        d.mkdir(parents=True, exist_ok=True)
    return out


def format_ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    total_sec = ms // 1000
    m, s = divmod(total_sec, 60)
    return f"{m:d}:{s:02d}"


def format_sec(sec: float) -> str:
    return format_ms(int(sec * 1000))


def _unique_destination(dest_dir: Path, name: str) -> Path:
    base = dest_dir / name
    if not base.exists():
        return base
    stem = Path(name).stem
    suf = Path(name).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem} ({n}){suf}"
        if not candidate.exists():
            return candidate
        n += 1


_MUTAGEN_TITLE_KEYS = ("title", "TITLE", "TIT2", "\xa9nam", "Title")
_MUTAGEN_ARTIST_KEYS = ("artist", "ARTIST", "TPE1", "\xa9ART", "Artist")
_MUTAGEN_BPM_KEYS = ("TBPM", "bpm", "BPM")
_MUTAGEN_KEY_KEYS = ("TKEY", "key", "initialkey", "KEY")


def _coerce_mutagen_scalar(val: object) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        if not val:
            return None
        val = val[0]
    if hasattr(val, "text"):
        raw = getattr(val, "text")
        if isinstance(raw, list):
            parts = [str(x).strip() for x in raw if str(x).strip()]
            return " / ".join(parts) if parts else None
        s = str(raw).strip()
        return s if s else None
    s = str(val).strip()
    return s if s else None


def _audio_tag_get(audio: object, keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v: object = None
        try:
            if hasattr(audio, "get"):
                v = audio.get(k)  # type: ignore[union-attr]
        except Exception:
            v = None
        tags = getattr(audio, "tags", None)
        if v is None and tags is not None:
            try:
                v = tags.get(k)  # type: ignore[union-attr]
            except Exception:
                v = None
        out = _coerce_mutagen_scalar(v)
        if out:
            return out
    return None


def _mutagen_title_artist(audio: object) -> tuple[Optional[str], Optional[str]]:
    return (
        _audio_tag_get(audio, _MUTAGEN_TITLE_KEYS),
        _audio_tag_get(audio, _MUTAGEN_ARTIST_KEYS),
    )


def _parse_filename_artist_dash_title(path: Path) -> tuple[Optional[str], Optional[str]]:
    """``Artist Name - Track Title.ext`` → ``(title, artist)`` for display."""
    stem = path.stem.strip()
    if " - " not in stem:
        return None, None
    left, _, right = stem.partition(" - ")
    artist = left.strip()
    title = right.strip()
    if not artist or not title:
        return None, None
    return title, artist


def _now_playing_main_line(
    path: Path,
    tag_title: Optional[str],
    tag_artist: Optional[str],
) -> str:
    if tag_title and tag_artist:
        return f"{tag_title} — {tag_artist}"
    if tag_title:
        return tag_title
    if tag_artist:
        return tag_artist
    ft, fa = _parse_filename_artist_dash_title(path)
    if ft and fa:
        return f"{ft} — {fa}"
    return path.name


def _mutagen_bpm_key_strings(audio: object) -> tuple[Optional[str], Optional[str]]:
    bpm_raw = _audio_tag_get(audio, _MUTAGEN_BPM_KEYS)
    key_raw = _audio_tag_get(audio, _MUTAGEN_KEY_KEYS)
    bpm_s: Optional[str] = None
    if bpm_raw:
        try:
            f = float(str(bpm_raw).replace(",", ".").strip())
            if f > 0:
                if abs(f - round(f)) < 0.02:
                    bpm_s = str(int(round(f)))
                else:
                    bpm_s = str(f).rstrip("0").rstrip(".")
        except ValueError:
            t = str(bpm_raw).strip()
            if t:
                bpm_s = t
    key_s = key_raw.strip() if key_raw else None
    return bpm_s, key_s


def _read_track_meta_uncached(path: Path) -> tuple[float, str, Optional[str], Optional[str]]:
    """Duration, ``Title — Artist`` line, optional BPM number string, optional key (e.g. ``7A``)."""
    dur = 0.0
    audio: object = None
    try:
        audio = MutagenFile(str(path))
        if audio is not None and getattr(audio, "info", None) is not None:
            dur = float(getattr(audio.info, "length", 0.0) or 0.0)
    except Exception:
        audio = None

    tt: Optional[str] = None
    ta: Optional[str] = None
    if audio is not None:
        try:
            tt, ta = _mutagen_title_artist(audio)
        except Exception:
            tt, ta = None, None

    main = _now_playing_main_line(path, tt, ta)
    bpm_s: Optional[str] = None
    key_s: Optional[str] = None
    if audio is not None:
        try:
            bpm_s, key_s = _mutagen_bpm_key_strings(audio)
        except Exception:
            bpm_s, key_s = None, None
    return dur, main, bpm_s, key_s


def get_duration_seconds(path: Path) -> float:
    return _read_track_meta_uncached(path)[0]


def _count_files_in_dir(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for p in d.iterdir() if p.is_file())


def _prune_stale_bakkis_tempfiles() -> None:
    """Remove old decoded preview WAVs left in the temp dir."""
    td = Path(tempfile.gettempdir())
    cutoff = time.time() - _BAKKIS_TEMP_MAX_AGE_SEC
    try:
        for p in td.glob(_BAKKIS_TEMP_GLOB):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("BAKKIS MUSIC SORTER")
        self.root.minsize(_WIN_MIN_W, _WIN_MIN_H)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._center_main_window(_DEFAULT_WIN_W, _DEFAULT_WIN_H)

        # Global dark theme (explicit palette; option_db + reapply prevents macOS white/gray buttons)
        self.bg = "#0B0F12"
        self.panel = "#171A1D"
        self.panel_border = "#2A2F36"
        self.trough = "#101418"
        self.fg = "#F2F2F2"
        self.time_text_fg = "#000000"  # Play/Pause label on grey transport button only
        self.transport_grey_bg = "#3A3F47"  # Play/Pause + time readout button backgrounds only
        self.transport_grey_active = "#4D5562"
        self.action_tile_border_hover = "#3D4A5C"
        self.muted = "#A0A0A0"
        self.accent = "#00D6B4"
        self.teal_on_fg = "#000000"  # text on teal (Play, active jump, Keep tile)
        self.accent_btn_press = "#00B89A"
        self.accent_active = "#00C9A8"  # tile keyboard-flash highlight
        self.archive_bg = "#FF2D55"
        self.archive_active = "#D92648"
        self.archive_fg = "#FFFFFF"
        self.favorite_bg = "#FFF200"
        self.favorite_active = "#D9CE00"
        self.favorite_on_fg = "#000000"
        self.warning = "#E8A838"
        self.text_on_accent = "#000000"
        self.skip_bg = "#171A1D"
        self.skip_active = "#222831"
        self.btn_dark_active = "#222831"
        self.btn_dark_active_fg = "#FFFFFF"
        self.disabled_fg = "#777777"
        self.mode_status_tinder_fg = self.accent
        self.mode_status_genre_fg = "#F4C842"

        self.root.configure(bg=self.bg)
        self._apply_global_tk_dark_defaults()
        self._fullscreen = False

        ensure_user_config_seeded_from_bundle()

        self._music_root: Optional[Path] = None
        self._folders: Optional[dict[str, Path]] = None
        self._queue: list[Path] = []
        self._session_total = 0
        self._display_index = 0
        self._undo_stack: list[dict] = []

        self._mode_preset_store = load_mode_preset_store()
        self._apply_startup_preset_ids_from_user_settings()
        self._genre_labels = list(DEFAULT_GENRE_LABELS)
        self._genre_colors = list(DEFAULT_GENRE_COLORS)
        self._genre_folder_names: list[str] = []
        self._swipe_labels = ["Keep", "Archive", "Favorite"]
        self._swipe_colors = [self.accent, self.archive_bg, self.favorite_bg]
        self._guide_swipe_line_labels: list[tk.Label] = []

        self._sorting_mode: SortingMode = load_default_mode_on_startup()
        self._track_order: TrackOrder = load_track_order()
        self._user_playback_start_sec: float = float(load_playback_start_seconds())
        self._var_include_subfolders = tk.BooleanVar(master=self.root, value=load_include_subfolders())
        self._var_auto_load_default = tk.BooleanVar(master=self.root, value=load_auto_load_default_folder())
        self._path_rel_to_music_root: dict[str, str] = {}
        self._apply_track_labels_from_preset_store()
        self._genre_title_labels: list[tk.Label] = []
        self._genre_tile_inners: list[tk.Frame] = []
        self._grid_kaf_keep_inner: Optional[tk.Frame] = None
        self._grid_kaf_archive_inner: Optional[tk.Frame] = None
        self._grid_kaf_favorite_inner: Optional[tk.Frame] = None
        self.cleanup_actions: Optional[tk.Frame] = None
        self.cleanup_outer: Optional[tk.Frame] = None
        self.genre_actions: Optional[tk.Frame] = None
        self._tile_mode_cleanup_inner: Optional[tk.Frame] = None
        self._tile_mode_genre_inner: Optional[tk.Frame] = None
        self._tile_order_random_inner: Optional[tk.Frame] = None
        self._tile_order_in_order_inner: Optional[tk.Frame] = None
        self._tile_guide_inner: Optional[tk.Frame] = None
        self._tile_settings_inner: Optional[tk.Frame] = None
        self._tile_queue_inner: Optional[tk.Frame] = None
        self._hidden_chrome_host: Optional[tk.Frame] = None
        self._swipe_skip_inner: Optional[tk.Frame] = None
        self._swipe_undo_inner: Optional[tk.Frame] = None
        self._grid_skip_inner: Optional[tk.Frame] = None
        self._grid_undo_inner: Optional[tk.Frame] = None
        self.lbl_mode_header: Optional[tk.Label] = None
        self.lbl_source_folder: Optional[tk.Label] = None
        self.lbl_session_stats: Optional[tk.Label] = None
        self.lbl_now_playing_tag: Optional[tk.Label] = None
        self._now_playing_main_wrap: Optional[tk.Frame] = None
        self._now_playing_main_inner: Optional[tk.Frame] = None
        self._now_playing_wraplength: int = 1100
        self._now_playing_summary_text: str = ""
        self._now_playing_active_plain_label: Optional[tk.Label] = None
        self._session_kept = 0
        self._session_archived = 0
        self._session_favorites = 0
        self._session_skipped = 0
        self._session_genre = 0
        self._session_started_monotonic = 0.0
        self._last_replay_key_ts = 0.0
        self._last_temp_prune_monotonic = 0.0

        self._duration_sec = 0.0
        self._desired_start_sec = 0.0
        self._playback_start_label = "0:00"
        self._is_playing = False
        self._audio_offset_in_source = 0.0
        self._play_started_monotonic = 0.0
        self._mpv_clock_anchor_pos = 0.0
        self._mpv_clock_anchor_mono = 0.0
        self._mpv_progress_sync_counter = 0

        self._mpv_proc: Optional[subprocess.Popen] = None
        self._mpv_ipc_sock: Optional[socket.socket] = None
        self._mpv_sock_path: Optional[str] = None
        self._mpv_ipc_lock = threading.Lock()
        self._mpv_reader_thread: Optional[threading.Thread] = None
        self._mpv_reader_stop = threading.Event()
        self._mpv_spawn_lock = threading.Lock()
        self._mpv_reply_lock = threading.Lock()
        self._mpv_reply_wait: dict[int, threading.Event] = {}
        self._mpv_reply_payload: dict[int, dict] = {}
        self._mpv_request_id = itertools.count(1)
        self._mpv_loaded_audio_path: Optional[str] = None
        self._mpv_cached_time_pos = 0.0
        self._playout_handled = False

        self._tiles_enabled = False
        self._jump_buttons: list[tk.Label] = []
        self._preset_jump_buttons: dict[int, tk.Label] = {}
        self._jump_btn_outro: Optional[tk.Label] = None
        self._jump_btn_minus: Optional[tk.Label] = None
        self._jump_btn_plus: Optional[tk.Label] = None
        self._jump_btn_pb_start: Optional[tk.Label] = None
        self._jump_mouse_error_lbl: Optional[tk.Label] = None
        self._tinder_kb_jump_error_digit: Optional[int] = None
        self._tinder_kb_jump_error_plus30: bool = False
        # Which jump control shows teal (last preset used from UI/keyboard, or default on new track)
        self._jump_highlight_key: JumpUiKey = 0
        self._shortcut_panel_visible = load_show_help_guide_on_startup()
        self._settings_top: Optional[tk.Toplevel] = None
        self.shortcut_frame: Optional[tk.Frame] = None
        self.footer_outer: Optional[tk.Frame] = None
        self._body: Optional[tk.Frame] = None
        self._main_scroll_outer: Optional[tk.Frame] = None
        self._main_panel: Optional[tk.Frame] = None
        self._bottom_dock: Optional[tk.Frame] = None
        self._main_column: Optional[tk.Frame] = None
        self._main_upper: Optional[tk.Frame] = None
        self._main_action_shell: Optional[tk.Frame] = None
        self._queue_sidebar_outer: Optional[tk.Frame] = None
        self._queue_sidebar_inner: Optional[tk.Frame] = None
        self._queue_section_frame: Optional[tk.Frame] = None
        self._sidebar_spring: Optional[tk.Frame] = None
        self._lbl_queue_now: Optional[tk.Label] = None
        self._lbl_queue_up: list[tk.Label] = []
        self._lbl_queue_recent: list[tk.Label] = []
        self._queue_panel_visible = False
        self._guide_sidebar_outer: Optional[tk.Frame] = None
        self._guide_sidebar_inner: Optional[tk.Frame] = None
        self._footer_logo_photo: Optional[object] = None
        self._load_folder_ring: Optional[tk.Frame] = None
        self.btn_load_default_folder: Optional[tk.Button] = None
        self.lbl_default_folder_path: Optional[tk.Label] = None
        self._flash_after_ids: dict[int, str] = {}
        self._transport_play_icon_label: Optional[tk.Label] = None
        self._last_shortcut_keysym: Optional[str] = None
        self._last_shortcut_time: float = 0.0
        self._key_repeat_suppress_sec = 0.085

        self._track_meta_cache: dict[str, tuple[float, str, Optional[str], Optional[str]]] = {}
        self._meta_cache_lock = threading.Lock()
        self._scan_id = 0
        self._scan_progress_queue: queue.Queue = queue.Queue()
        self._folder_scan_alive = False
        self._preload_session_id = 0
        self._autoplay_after_id: Optional[str] = None
        self._mpv_play_retry_count = 0

        self._build_ui()
        self._sync_swipe_tiles_from_preset()
        self._refresh_shortcut_guide_swipe_lines()
        if load_show_queue_panel_on_startup():
            self._apply_queue_panel_visibility(True)
        _prune_stale_bakkis_tempfiles()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after_idle(self._update_content_wraplengths)
        self._bind_keys()
        self._set_buttons_enabled(False)
        self.root.after_idle(self.root.focus_set)
        # macOS: bring window forward (often buried behind Terminal when launched from .command)
        self.root.lift()
        self.root.after(80, lambda: self._raise_main_window())
        self.root.after(150, self._refresh_all_widget_chrome)
        self.root.after(220, self._maybe_autoload_default_folder)
        self.root.after(0, self._mpv_start_idle)
        self._tick()

    def _apply_startup_preset_ids_from_user_settings(self) -> None:
        pid = read_user_settings().get("startup_preset_id")
        if not isinstance(pid, str) or not pid.strip():
            return
        pid = pid.strip()
        changed = False
        for p in self._mode_preset_store.get("presets") or []:
            if not isinstance(p, dict) or str(p.get("id")) != pid:
                continue
            m = str(p.get("mode", "")).lower()
            if m == "swipe":
                self._mode_preset_store["active_swipe_preset_id"] = pid
                changed = True
            elif m == "grid":
                self._mode_preset_store["active_grid_preset_id"] = pid
                changed = True
            break
        if changed:
            try:
                save_mode_preset_store(self._mode_preset_store)
            except OSError:
                pass

    def _apply_track_labels_from_preset_store(self) -> None:
        sw = active_swipe_preset(self._mode_preset_store)
        gr = active_grid_preset(self._mode_preset_store)
        self._swipe_labels = [str(x) for x in sw.get("labels", [])[:3]]
        while len(self._swipe_labels) < 3:
            self._swipe_labels.append(["Keep", "Archive", "Favorite"][len(self._swipe_labels)])
        scol = sw.get("colors", [])
        self._swipe_colors = [
            _normalize_hex(str(scol[i])) if i < len(scol) else ["#00D6B4", "#FF2D55", "#FFF200"][i] for i in range(3)
        ]
        self._genre_labels = [str(x) for x in gr.get("labels", [])[:9]]
        while len(self._genre_labels) < 9:
            self._genre_labels.append(DEFAULT_GENRE_LABELS[len(self._genre_labels)])
        gcols = gr.get("colors", [])
        self._genre_colors = [
            _coerce_palette_hex(str(gcols[i]), DEFAULT_GENRE_COLORS[i]) if i < len(gcols) else DEFAULT_GENRE_COLORS[i]
            for i in range(9)
        ]
        self._genre_folder_names = [str(x) for x in gr.get("folders", [])[:9]]
        while len(self._genre_folder_names) < 9:
            self._genre_folder_names.append(safe_genre_folder_name(self._genre_labels[len(self._genre_folder_names)]))

    def _refresh_shortcut_guide_swipe_lines(self) -> None:
        lines = [
            f"{self._swipe_labels[0]} (→)",
            f"{self._swipe_labels[1]} (↑)",
            f"{self._swipe_labels[2]} (←)",
            "Enter  Skip",
            "Z  Undo last step",
            "⌃Z  Undo all (back to folder load)",
        ]
        for i, lb in enumerate(self._guide_swipe_line_labels):
            if i < len(lines):
                try:
                    lb.configure(text=lines[i])
                except tk.TclError:
                    pass

    def _sync_swipe_tiles_from_preset(self) -> None:
        inners = (self._tile_keep_inner, self._tile_archive_inner, self._tile_favorite_inner)
        for i, inner in enumerate(inners):
            if inner is None:
                continue
            hx = self._swipe_colors[i] if i < len(self._swipe_colors) else self.accent
            active = _mix_hex_toward_black(hx, 0.22)
            fg_m = _fg_for_genre_bg(hx)
            sc_m = _genre_shortcut_fg(fg_m)
            inner._tile_bg = hx  # type: ignore[attr-defined]
            inner._tile_active_bg = active  # type: ignore[attr-defined]
            inner._tile_fg = fg_m  # type: ignore[attr-defined]
            inner._tile_shortcut_fg = sc_m  # type: ignore[attr-defined]
            w = getattr(inner, "_tile_widgets", ())
            if len(w) >= 2:
                try:
                    w[1].configure(text=self._swipe_labels[i].strip().upper())
                except tk.TclError:
                    pass
            try:
                self._set_action_tile_enabled(inner, self._tiles_enabled)
            except tk.TclError:
                pass

    def _upsert_preset_in_store(self, preset: dict) -> None:
        pid = str(preset.get("id") or "")
        if not pid:
            return
        presets = [p for p in (self._mode_preset_store.get("presets") or []) if isinstance(p, dict)]
        replaced = False
        for j, p in enumerate(presets):
            if str(p.get("id")) == pid:
                presets[j] = preset
                replaced = True
                break
        if not replaced:
            presets.append(preset)
        self._mode_preset_store["presets"] = presets
        save_mode_preset_store(self._mode_preset_store)

    def _open_edit_swipe_preset_dialog(self) -> None:
        self._prepare_native_dialog()
        sw = active_swipe_preset(self._mode_preset_store)
        top = tk.Toplevel(self.root)
        top.title("Edit swipe preset")
        top.configure(bg=self.bg)
        try:
            top.transient(self.root)
        except tk.TclError:
            pass
        labels_e: list[tk.Entry] = []
        colors_e: list[tk.Entry] = []
        folders_e: list[tk.Entry] = []
        row_titles = ("Right → keep", "Up ↑ archive", "Left ← favorites")
        for i in range(3):
            row = tk.Frame(top, bg=self.bg)
            row.pack(fill="x", padx=14, pady=4)
            tk.Label(row, text=row_titles[i], bg=self.bg, fg=self.accent, width=18, anchor="e", font=("Helvetica", 10)).pack(
                side="left",
            )
            le = tk.Entry(
                row,
                width=16,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                highlightcolor=self.accent,
                font=("Helvetica", 11),
            )
            le.insert(0, sw["labels"][i])
            le.pack(side="left", padx=(6, 4))
            labels_e.append(le)
            ce = tk.Entry(
                row,
                width=10,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                font=("Helvetica", 11),
            )
            ce.insert(0, sw["colors"][i])
            ce.pack(side="left", padx=4)
            colors_e.append(ce)
            fe = tk.Entry(
                row,
                width=14,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                font=("Helvetica", 11),
            )
            fe.insert(0, sw["folders"][i])
            fe.pack(side="left", padx=4)
            folders_e.append(fe)

        def save_sw() -> None:
            nls = [e.get().strip() or "?" for e in labels_e]
            ncs = [_normalize_hex(e.get()) for e in colors_e]
            nfs = [e.get().strip() or f"OUT{j}" for j, e in enumerate(folders_e)]
            blob = _coerce_swipe_preset_blob(
                {
                    "id": sw["id"],
                    "name": sw.get("name", "Swipe"),
                    "mode": "swipe",
                    "labels": nls,
                    "colors": ncs,
                    "folders": nfs,
                },
            )
            if not blob:
                return
            self._upsert_preset_in_store(blob)
            self._apply_track_labels_from_preset_store()
            self._sync_swipe_tiles_from_preset()
            self._refresh_shortcut_guide_swipe_lines()
            if self._music_root is not None:
                trip = tuple(str(x) for x in active_swipe_preset(self._mode_preset_store)["folders"][:3])
                self._folders = ensure_sort_folders(self._music_root, trip)  # type: ignore[arg-type]
            self._refresh_stats()
            top.destroy()
            self.root.after(10, self._refresh_all_widget_chrome)

        btn_row = tk.Frame(top, bg=self.bg)
        btn_row.pack(pady=(12, 14))
        bs = tk.Button(btn_row, text="Save", command=save_sw, font=("Helvetica", 11, "bold"), padx=16, pady=6)
        bc = tk.Button(btn_row, text="Cancel", command=top.destroy, font=("Helvetica", 11, "bold"), padx=16, pady=6)
        self._style_tk_button_accent(bs)
        self._style_tk_button_transport_grey(bc)
        bs.pack(side="left", padx=6)
        bc.pack(side="left", padx=6)

    def _center_main_window(self, w: int, h: int) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _on_root_configure(self, event: object) -> None:
        try:
            w = getattr(event, "widget", None)
            if w is None or str(w) != str(self.root):
                return
        except Exception:
            return
        self._update_content_wraplengths()

    def _update_content_wraplengths(self) -> None:
        try:
            rw = int(self.root.winfo_width())
        except tk.TclError:
            return
        sidebar_reserve = 0
        if self._queue_panel_visible:
            sidebar_reserve += _QUEUE_PANEL_WIDTH_RESERVE
        if self._shortcut_panel_visible:
            sidebar_reserve += _GUIDE_PANEL_WIDTH_RESERVE
        inner = max(420, rw - 80 - sidebar_reserve)
        try:
            self.lbl_path.configure(wraplength=inner)
            if self.lbl_now_playing_tag is not None:
                self.lbl_now_playing_tag.configure(wraplength=inner)
            self._now_playing_wraplength = inner
            pl = self._now_playing_active_plain_label
            if pl is not None:
                pl.configure(wraplength=inner)
            if self.lbl_source_folder is not None:
                self.lbl_source_folder.configure(wraplength=inner)
        except tk.TclError:
            pass
        try:
            if self._sorting_mode == "grid":
                self.lbl_stats.configure(wraplength=inner)
            else:
                self.lbl_stats.configure(wraplength=0)
        except tk.TclError:
            pass
        try:
            if self.lbl_session_stats is not None:
                self.lbl_session_stats.configure(wraplength=_GUIDE_SIDEBAR_TEXT_WRAP)
        except tk.TclError:
            pass
        try:
            if self._genre_title_labels:
                cell_w = max(56, (inner - 40) // 3)
                g_wrap = max(40, cell_w - 18)
                for lab in self._genre_title_labels:
                    try:
                        lab.configure(wraplength=g_wrap)
                    except tk.TclError:
                        pass
        except tk.TclError:
            pass

    def _raise_main_window(self) -> None:
        """Bring the main window forward without -topmost (that hides native macOS sheets behind Tk)."""
        try:
            self.root.attributes("-topmost", False)
            self.root.lift()
            self.root.after(200, self.root.lift)
        except tk.TclError:
            pass

    def _prepare_native_dialog(self) -> None:
        """macOS: avoid folder/message dialogs opening behind the Tk window."""
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.root.lift()
        self.root.update_idletasks()

    def _perf_log(self, message: str) -> None:
        print(f"[bakkis perf] {message}", flush=True)

    # --- mpv JSON IPC (single subprocess, idle at launch) ---

    def _mpv_start_idle(self) -> None:
        exe = _which_mpv()
        if not exe:
            try:
                self.lbl_seek_hint.configure(
                    text="mpv is required for playback. Install with: brew install mpv",
                )
            except tk.TclError:
                pass
            return
        with self._mpv_spawn_lock:
            if self._mpv_proc is not None and self._mpv_proc.poll() is None:
                return
            if self._mpv_proc is not None and self._mpv_proc.poll() is not None:
                self._mpv_proc = None
            # Predictable path under /tmp; unique per process so two app instances never share one socket.
            self._mpv_sock_path = f"/tmp/bakkis_mpv_{os.getpid()}.sock"
            try:
                if os.path.exists(self._mpv_sock_path):
                    os.unlink(self._mpv_sock_path)
            except OSError:
                pass
            try:
                self._mpv_proc = subprocess.Popen(
                    [
                        exe,
                        "--idle=yes",
                        "--force-window=no",
                        "--no-video",
                        "--really-quiet",
                        "--no-terminal",
                        "--no-config",
                        # Low-latency local playback (single persistent process; all control via IPC).
                        "--cache=no",
                        "--demuxer-max-bytes=4MiB",
                        "--audio-buffer=0.1",
                        f"--input-ipc-server={self._mpv_sock_path}",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                self._perf_log(f"mpv_start_failed: {e}")
                try:
                    self.lbl_seek_hint.configure(
                        text="mpv is required for playback. Install with: brew install mpv",
                    )
                except tk.TclError:
                    pass
                return

            self._mpv_reader_stop.clear()
            self._mpv_reader_thread = threading.Thread(target=self._mpv_connect_and_read_loop, daemon=True)
            self._mpv_reader_thread.start()

    def _mpv_connect_and_read_loop(self) -> None:
        sock_path = self._mpv_sock_path
        if sock_path is None:
            return
        deadline = time.monotonic() + 8.0
        n_try = 0
        while time.monotonic() < deadline and not self._mpv_reader_stop.is_set():
            try:
                if os.path.exists(sock_path):
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(sock_path)
                    with self._mpv_ipc_lock:
                        self._mpv_ipc_sock = s
                    break
            except OSError:
                n_try += 1
                time.sleep(0.012 if n_try < 25 else 0.04)
        else:
            self._perf_log("mpv_ipc_connect_failed")
            return

        buf = b""
        try:
            while not self._mpv_reader_stop.is_set():
                try:
                    chunk = self._mpv_ipc_sock.recv(65536) if self._mpv_ipc_sock else b""
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    self._mpv_dispatch_ipc(obj)
        finally:
            try:
                if self._mpv_ipc_sock is not None:
                    self._mpv_ipc_sock.close()
            except OSError:
                pass
            self._mpv_ipc_sock = None

    def _mpv_dispatch_ipc(self, obj: dict) -> None:
        if "request_id" in obj:
            try:
                rid = int(obj["request_id"])
            except (TypeError, ValueError):
                return
            ev: Optional[threading.Event] = None
            with self._mpv_reply_lock:
                ev = self._mpv_reply_wait.pop(rid, None)
                if ev is not None:
                    self._mpv_reply_payload[rid] = obj
            if ev is not None:
                ev.set()
            return
        if obj.get("event") == "end-file":
            reason = str(obj.get("reason", ""))
            err = obj.get("error")
            try:
                self.root.after(0, lambda r=reason, e=err: self._on_mpv_end_file_event(r, e))
            except tk.TclError:
                pass

    def _mpv_ipc_send_obj(self, obj: dict) -> bool:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._mpv_ipc_lock:
            sock = self._mpv_ipc_sock
            if sock is None:
                return False
            try:
                sock.sendall(line.encode("utf-8"))
                return True
            except OSError:
                return False

    def _mpv_request(self, cmd: list, timeout: float = 1.25) -> Optional[dict]:
        rid = next(self._mpv_request_id)
        ev = threading.Event()
        with self._mpv_reply_lock:
            self._mpv_reply_wait[rid] = ev
        ok = self._mpv_ipc_send_obj({"command": cmd, "request_id": rid})
        if not ok:
            with self._mpv_reply_lock:
                self._mpv_reply_wait.pop(rid, None)
            return None
        if not ev.wait(timeout):
            with self._mpv_reply_lock:
                self._mpv_reply_wait.pop(rid, None)
            return None
        with self._mpv_reply_lock:
            return self._mpv_reply_payload.pop(rid, None)

    def _mpv_command_ok(self, cmd: list, *, timeout: float = 1.25) -> bool:
        rep = self._mpv_request(cmd, timeout=timeout)
        if rep is None:
            return False
        err = rep.get("error")
        return err in (None, "success")

    def _mpv_get_property(self, name: str, *, timeout: float = 1.25) -> Optional[object]:
        rep = self._mpv_request(["get_property", name], timeout=timeout)
        if rep is None:
            return None
        if rep.get("error") not in (None, "success"):
            return None
        return rep.get("data")

    def _mpv_get_time_pos(self) -> Optional[float]:
        v = self._mpv_get_property("time-pos", timeout=0.45)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _mpv_poll_until_decoder_ready(self, max_wait_sec: float = 25.0) -> bool:
        """True once mpv reports something loadable (time-pos or positive duration)."""
        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            tp = self._mpv_get_property("time-pos", timeout=1.0)
            if tp is not None:
                return True
            dur = self._mpv_get_property("duration", timeout=1.0)
            if dur is not None:
                try:
                    if float(dur) > 0.0:
                        return True
                except (TypeError, ValueError):
                    pass
            time.sleep(0.04)
        return False

    def _mpv_set_playback_clock_anchor(self, pos_sec: float) -> None:
        """Monotonic anchor so the UI can advance time without polling mpv every tick."""
        self._mpv_clock_anchor_pos = max(0.0, pos_sec)
        self._mpv_clock_anchor_mono = time.monotonic()
        self._mpv_cached_time_pos = self._mpv_clock_anchor_pos

    def _mpv_set_pause(self, paused: bool, *, timeout: float = 0.35) -> bool:
        return self._mpv_command_ok(["set_property", "pause", paused], timeout=timeout)

    def _mpv_seek_absolute(self, sec: float, *, timeout: float = 0.35, perf_log: bool = True) -> bool:
        t0 = time.perf_counter()
        ok = self._mpv_command_ok(["seek", sec, "absolute"], timeout=timeout)
        if perf_log:
            self._perf_log(
                f"mpv_jump_seek_ipc_ms={(time.perf_counter() - t0) * 1000.0:.2f} "
                f"target={sec:.3f}s ok={ok} tmo={timeout}",
            )
        return ok

    def _mpv_stop_media(self) -> None:
        self._mpv_command_ok(["stop"], timeout=0.35)
        self._mpv_loaded_audio_path = None

    def _on_mpv_end_file_event(self, reason: str, _error: object) -> None:
        # Only natural EOF advances UI / auto-next; stop/replace/error fire other reasons.
        if reason != "eof":
            return
        if self._current_path() is None:
            return
        self._is_playing = False
        self._set_play_button_text("Play")
        end_pos = self._duration_sec if self._duration_sec > 0 else self._audio_offset_in_source
        self._audio_offset_in_source = end_pos
        self._mpv_set_playback_clock_anchor(end_pos)
        self._set_elapsed_seconds(end_pos)
        self._draw_progress_bar(end_pos)
        if (
            not self._playout_handled
            and len(self._queue) > 1
            and self._tiles_enabled
        ):
            self._playout_handled = True
            self._autoplay_next_after_playout()

    def _mpv_shutdown(self) -> None:
        self._mpv_reader_stop.set()
        try:
            self._mpv_ipc_send_obj({"command": ["quit"]})
        except Exception:
            pass
        try:
            if self._mpv_ipc_sock is not None:
                self._mpv_ipc_sock.close()
        except OSError:
            pass
        self._mpv_ipc_sock = None
        proc = self._mpv_proc
        self._mpv_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        try:
            sp = self._mpv_sock_path
            if sp and os.path.exists(sp):
                os.unlink(sp)
        except OSError:
            pass

    def _mark_playback_started(self, source_offset_sec: float) -> None:
        self._audio_offset_in_source = source_offset_sec
        self._mpv_set_playback_clock_anchor(source_offset_sec)
        self._play_started_monotonic = time.monotonic()
        self._is_playing = True
        self._set_play_button_text("Pause")

    def _get_track_meta(self, path: Path) -> tuple[float, str, Optional[str], Optional[str]]:
        key = str(path.resolve())
        with self._meta_cache_lock:
            hit = self._track_meta_cache.get(key)
        if hit is not None:
            return hit
        t0 = time.perf_counter()
        meta = _read_track_meta_uncached(path)
        with self._meta_cache_lock:
            self._track_meta_cache[key] = meta
        self._perf_log(f"meta_read_uncached: {(time.perf_counter() - t0) * 1000.0:.1f}ms {path.name}")
        return meta

    def _clear_now_playing_main_inner(self) -> None:
        inner = self._now_playing_main_inner
        if inner is None:
            return
        try:
            for ch in inner.winfo_children():
                ch.destroy()
        except tk.TclError:
            pass
        self._now_playing_active_plain_label = None

    def _set_now_playing_main_plain(self, text: str) -> None:
        """Single centered headline (idle / scanning / done); hides NOW PLAYING kicker."""
        nt = self.lbl_now_playing_tag
        if nt is not None:
            try:
                nt.pack_forget()
            except tk.TclError:
                pass
        self._clear_now_playing_main_inner()
        self._now_playing_summary_text = text
        inner = self._now_playing_main_inner
        if inner is None:
            return
        try:
            lb = tk.Label(
                inner,
                text=text,
                bg=self.bg,
                fg=self.fg,
                font=("Helvetica", 22, "bold"),
                wraplength=self._now_playing_wraplength,
                justify="center",
                anchor="center",
            )
            lb.pack()
            self._now_playing_active_plain_label = lb
        except tk.TclError:
            pass

    def _apply_track_identity_header(
        self,
        headline: str,
        bpm: Optional[str],
        key: Optional[str],
    ) -> None:
        """NOW PLAYING + one row: Title — Artist • BPM • key (teal bullets)."""
        nt = self.lbl_now_playing_tag
        wrap = self._now_playing_main_wrap
        inner = self._now_playing_main_inner
        if nt is None or wrap is None or inner is None:
            self._set_now_playing_main_plain(headline)
            return
        tail: list[str] = []
        if bpm:
            tail.append(f"{bpm} BPM")
        if key:
            tail.append(key)
        self._now_playing_summary_text = headline + (" • " + " • ".join(tail) if tail else "")
        title_font = ("Helvetica", 22, "bold")
        try:
            self._now_playing_active_plain_label = None
            self._clear_now_playing_main_inner()
            nt.configure(text="NOW PLAYING")
            if nt.winfo_manager() == "":
                nt.pack(fill="x", pady=(4, 3), before=wrap)
            tk.Label(
                inner,
                text=headline,
                bg=self.bg,
                fg=self.fg,
                font=title_font,
                anchor="center",
            ).pack(side="left")
            if bpm:
                tk.Label(inner, text=" • ", bg=self.bg, fg=self.accent, font=title_font, anchor="center").pack(
                    side="left",
                )
                tk.Label(
                    inner,
                    text=f"{bpm} BPM",
                    bg=self.bg,
                    fg=self.fg,
                    font=title_font,
                    anchor="center",
                ).pack(side="left")
            if key:
                tk.Label(inner, text=" • ", bg=self.bg, fg=self.accent, font=title_font, anchor="center").pack(
                    side="left",
                )
                tk.Label(inner, text=key, bg=self.bg, fg=self.fg, font=title_font, anchor="center").pack(
                    side="left",
                )
        except tk.TclError:
            pass

    def _schedule_track_meta_preload(self) -> None:
        paths = [p for p in self._queue[1 : 1 + _PRELOAD_LOOKAHEAD] if p is not None]
        if not paths:
            return
        self._preload_session_id += 1
        sid = self._preload_session_id

        def worker() -> None:
            for p in paths:
                if sid != self._preload_session_id:
                    return
                key = str(p.resolve())
                with self._meta_cache_lock:
                    if key in self._track_meta_cache:
                        continue
                meta = _read_track_meta_uncached(p)
                with self._meta_cache_lock:
                    if sid != self._preload_session_id:
                        return
                    self._track_meta_cache.setdefault(key, meta)

        threading.Thread(target=worker, daemon=True).start()

    def _folder_scan_worker(self, root_folder: Path, sid: int, recursive: bool, skip_names: frozenset[str]) -> None:
        t0 = time.perf_counter()
        paths: list[Path] = []
        n = 0
        last_prog = 0.0
        try:
            root_resolved = root_folder.resolve()
            for p in iter_audio_files_under(root_resolved, recursive=recursive, skip_dir_names=skip_names):
                if sid != self._scan_id:
                    return
                paths.append(p)
                n += 1
                now = time.perf_counter()
                if n == 1 or n % _SCAN_PROGRESS_EVERY == 0 or (now - last_prog) > 0.22:
                    last_prog = now
                    self._scan_progress_queue.put(("progress", sid, n, str(p)))
            if sid != self._scan_id:
                return
            dt = time.perf_counter() - t0
            self._scan_progress_queue.put(("done", sid, paths, dt, root_resolved))
        except Exception as e:
            self._scan_progress_queue.put(("error", sid, str(e)))

    def _poll_scan_progress(self) -> None:
        try:
            while True:
                item = self._scan_progress_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, msg_sid, count, _last_path = item
                    if msg_sid != self._scan_id:
                        continue
                    try:
                        self.lbl_track.configure(text=f"Scanning folders… {count} tracks")
                    except tk.TclError:
                        pass
                elif kind == "done":
                    _, msg_sid, paths, dt, scan_root = item
                    if msg_sid != self._scan_id:
                        continue
                    self._folder_scan_alive = False
                    self._perf_log(f"folder_scan: {dt:.3f}s files={len(paths)}")
                    if not paths:
                        self._prepare_native_dialog()
                        shallow = not self._var_include_subfolders.get()
                        fmt = supported_audio_formats_display()
                        hint = (
                            f"No supported audio files were found in that folder (top level only).\n\n"
                            f"Supported formats: {fmt}\n\n"
                            'Turn on "Include subfolders" to scan inside subdirectories.'
                            if shallow
                            else (
                                f"No supported audio files were found in that folder or its subfolders.\n\n"
                                f"Supported formats: {fmt}"
                            )
                        )
                        messagebox.showinfo("No audio files", hint, parent=self.root)
                        try:
                            self._set_now_playing_main_plain("Load a folder to begin")
                            self.lbl_track.configure(text="")
                            self.lbl_path.configure(text="")
                            if self.lbl_source_folder is not None:
                                self.lbl_source_folder.configure(text="")
                        except tk.TclError:
                            pass
                        self._path_rel_to_music_root = {}
                        self.root.after(10, self._refresh_all_widget_chrome)
                        return
                    paths = self._apply_scan_track_order(paths)
                    root = scan_root
                    self._music_root = root
                    self._path_rel_to_music_root = build_track_relative_index(paths, root)
                    sw = active_swipe_preset(self._mode_preset_store)
                    trip = tuple(str(x) for x in sw["folders"][:3])
                    self._folders = ensure_sort_folders(root, trip)
                    ensure_genre_folders(root, list(active_grid_preset(self._mode_preset_store)["folders"][:9]))
                    self._queue = paths
                    self._session_total = len(paths)
                    self._display_index = 1
                    self._undo_stack.clear()
                    self._reset_session_counters()
                    self._stop_playback()
                    self._set_buttons_enabled(True)
                    self._refresh_stats()
                    self.load_current_track(autoplay=True)
                    self.root.after(10, self._refresh_all_widget_chrome)
                    return
                elif kind == "error":
                    _, msg_sid, err = item
                    if msg_sid != self._scan_id:
                        continue
                    self._folder_scan_alive = False
                    self._perf_log(f"folder_scan_error: {err}")
                    messagebox.showerror("Scan failed", err, parent=self.root)
                    try:
                        self._set_now_playing_main_plain("Load a folder to begin")
                        self.lbl_track.configure(text="")
                        if self.lbl_source_folder is not None:
                            self.lbl_source_folder.configure(text="")
                    except tk.TclError:
                        pass
                    self._path_rel_to_music_root = {}
                    self.root.after(10, self._refresh_all_widget_chrome)
                    return
                # abandoned: ignore
        except queue.Empty:
            pass
        if self._folder_scan_alive:
            self.root.after(_SCAN_QUEUE_POLL_MS, self._poll_scan_progress)

    def _apply_global_tk_dark_defaults(self) -> None:
        """Lock Tk defaults to dark colors so Aqua redraws cannot fall back to white/light gray."""
        r = self.root
        # Button class defaults (widgetDefault priority = 20)
        r.option_add("*Button.background", self.panel, 20)
        r.option_add("*Button.foreground", self.fg, 20)
        r.option_add("*Button.activeBackground", self.btn_dark_active, 20)
        r.option_add("*Button.activeForeground", self.btn_dark_active_fg, 20)
        r.option_add("*Button.highlightBackground", self.panel, 20)
        r.option_add("*Button.highlightColor", self.panel, 20)
        r.option_add("*Button.disabledForeground", self.disabled_fg, 20)
        r.option_add("*Button.relief", "flat", 20)
        r.option_add("*Button.borderWidth", "1", 20)
        r.option_add("*Button.highlightThickness", "0", 20)
        r.option_add("*Button.cursor", "", 20)
        r.option_add("*Frame.borderWidth", "0", 20)
        r.option_add("*Frame.relief", "flat", 20)
        r.option_add("*Frame.cursor", "", 20)
        r.option_add("*Label.borderWidth", "0", 20)
        r.option_add("*Label.highlightThickness", "0", 20)
        r.option_add("*Label.cursor", "", 20)
        r.option_add("*Canvas.highlightThickness", "0", 20)
        r.option_add("*Canvas.borderWidth", "0", 20)
        r.option_add("*Canvas.cursor", "", 20)
        r.option_add("*Dialog.background", self.bg, 20)

    def _style_tk_button_dark_panel(self, b: tk.Button) -> None:
        """Panel-style tk.Button (jump row, ±30s)."""
        b.configure(
            bg=self.panel,
            fg=self.fg,
            activebackground=self.btn_dark_active,
            activeforeground=self.btn_dark_active_fg,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
            overrelief="flat",
            cursor="",
        )

    def _style_tk_button_accent(self, b: tk.Button) -> None:
        """Teal primary (active jump, etc.): black label stays readable on macOS."""
        b.configure(
            bg=self.accent,
            fg=self.teal_on_fg,
            activebackground=self.accent_btn_press,
            activeforeground=self.teal_on_fg,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
            highlightbackground=self.accent,
            highlightcolor=self.accent,
            overrelief="flat",
            cursor="",
        )

    def _style_tk_button_transport_grey(self, b: tk.Button) -> None:
        """Play / Pause: grey background; text stays time_text_fg (black)."""
        g = self.transport_grey_bg
        t = self.time_text_fg
        b.configure(
            bg=g,
            fg=t,
            activebackground=self.transport_grey_active,
            activeforeground=t,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
            highlightbackground=g,
            highlightcolor=g,
            overrelief="flat",
            cursor="",
        )

    def _style_tk_option_menu_transport(self, om: tk.OptionMenu) -> None:
        """Same readable treatment as transport buttons (used in Settings dropdowns)."""
        g = self.transport_grey_bg
        t = self.time_text_fg
        om.configure(
            bg=g,
            fg=t,
            activebackground=self.transport_grey_active,
            activeforeground=t,
            disabledforeground=self.disabled_fg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
            relief="flat",
            borderwidth=1,
            cursor="",
        )
        om["menu"].configure(
            bg=g,
            fg=t,
            activebackground=self.accent,
            activeforeground=self.teal_on_fg,
            bd=0,
        )

    def _style_tk_button_jump_active(self, b: tk.Button) -> None:
        """Active jump preset: teal + glow; black text."""
        glow = "#00F0DC"
        b.configure(
            bg=self.accent,
            fg=self.teal_on_fg,
            activebackground=self.accent_btn_press,
            activeforeground=self.teal_on_fg,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=2,
            highlightbackground=glow,
            highlightcolor=glow,
            overrelief="flat",
            cursor="",
        )

    def _mode_status_text(self, mode: SortingMode) -> str:
        label = "SWIPE MODE" if mode == "swipe" else "GRID MODE"
        return " ".join(label)

    def _mode_status_fg(self, mode: SortingMode) -> str:
        return self.mode_status_tinder_fg if mode == "swipe" else self.mode_status_genre_fg

    def _refresh_mode_status_label(self) -> None:
        if self.lbl_mode_header is None:
            return
        try:
            self.lbl_mode_header.configure(
                text=self._mode_status_text(self._sorting_mode),
                fg=self._mode_status_fg(self._sorting_mode),
            )
        except tk.TclError:
            pass

    def _is_active_jump_label(self, lbl: tk.Label) -> bool:
        sel = self._jump_highlight_key
        if lbl is self._jump_btn_pb_start and sel == "pb_start":
            return True
        if lbl is self._jump_btn_outro and sel == "outro":
            return True
        for sec, btn in self._preset_jump_buttons.items():
            if btn is lbl and isinstance(sel, int) and sec == sel:
                return True
        return False

    def _recompute_desired_start_and_highlight(self) -> None:
        """Used by first track load and live Settings changes for default playback start."""
        want = float(self._user_playback_start_sec)
        if self._duration_sec <= 0.0 or self._duration_sec < want + 30.0:
            self._desired_start_sec = 0.0
            self._playback_start_label = "0:00"
        else:
            self._desired_start_sec = want
            self._playback_start_label = format_sec(want)
        self._set_playback_start_line(self._playback_start_label)
        outro_sec = self._outro_jump_seconds()
        ds = self._desired_start_sec
        used_config_start = self._duration_sec > 0.0 and self._duration_sec >= want + 30.0
        if self._duration_sec >= 3.0 and abs(ds - outro_sec) <= 0.75:
            if used_config_start and abs(ds - want) <= 0.75:
                self._jump_highlight_key = "pb_start"
            else:
                self._jump_highlight_key = "outro"
        else:
            if used_config_start and abs(ds - want) <= 0.75:
                self._jump_highlight_key = "pb_start"
            else:
                self._jump_highlight_key = int(round(ds))
        self._sync_pb_start_jump_button_label()

    def _apply_user_playback_start_change(self, new_sec: float) -> None:
        new_sec = float(new_sec)
        self._user_playback_start_sec = new_sec
        patch_user_settings(playback_start_seconds=new_sec)
        if self._current_path() is None:
            self._refresh_all_widget_chrome()
            return
        self._recompute_desired_start_and_highlight()
        if not self._is_playing:
            self._audio_offset_in_source = self._desired_start_sec
            self._set_elapsed_seconds(self._desired_start_sec)
            self._draw_progress_bar(self._desired_start_sec)
        self._refresh_all_widget_chrome()

    def _style_jump_label_idle(self, lbl: tk.Label) -> None:
        """Inactive jump tile: same contrast idea as sort tiles (dark panel + light text)."""
        base = getattr(lbl, "_jump_base_text", None)
        font_idle = getattr(lbl, "_jump_idle_font", None)
        try:
            if isinstance(base, str):
                lbl.configure(text=base)
            if font_idle is not None:
                lbl.configure(font=font_idle)
        except tk.TclError:
            pass
        lbl.configure(
            bg=self.panel,
            fg=self.fg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
        )

    def _style_jump_label_active(self, lbl: tk.Label) -> None:
        """Selected preset / Outro: teal + START / time caption on the tile."""
        glow = "#00F0DC"
        time_only = getattr(lbl, "_jump_time_only", None)
        active_font = getattr(lbl, "_jump_active_font", None)
        try:
            if isinstance(time_only, str) and time_only:
                lbl.configure(text=f"START\n{time_only}")
                if active_font is not None:
                    lbl.configure(font=active_font)
        except tk.TclError:
            pass
        lbl.configure(
            bg=self.accent,
            fg=self.teal_on_fg,
            highlightthickness=2,
            highlightbackground=glow,
            highlightcolor=glow,
        )

    def _style_jump_label_disabled(self, lbl: tk.Label) -> None:
        base = getattr(lbl, "_jump_base_text", None)
        font_idle = getattr(lbl, "_jump_idle_font", None)
        try:
            if isinstance(base, str):
                lbl.configure(text=base)
            if font_idle is not None:
                lbl.configure(font=font_idle)
        except tk.TclError:
            pass
        lbl.configure(
            bg=self.trough,
            fg=self.disabled_fg,
            highlightthickness=1,
            highlightbackground=self.trough,
            highlightcolor=self.trough,
        )

    def _style_jump_label_error_hold(self, lbl: Optional[tk.Label]) -> None:
        """Preset or +30s past track end — red until mouse up (genre: mouse only)."""
        if lbl is None:
            return
        try:
            lbl.configure(
                bg=self.archive_bg,
                fg=self.archive_fg,
                highlightthickness=1,
                highlightbackground=self.archive_active,
                highlightcolor=self.archive_active,
            )
        except tk.TclError:
            pass

    def _absolute_jump_past_track_end(self, target_sec: float) -> bool:
        """Preset time mark is after the current track length (mode-agnostic math)."""
        if self._duration_sec <= 0:
            return False
        return target_sec > self._duration_sec + 1e-3

    def _delta_plus_past_track_end(self) -> bool:
        """+30s from current playhead would land past the end (mode-agnostic math)."""
        if self._duration_sec <= 0:
            return False
        pos = self._current_position_in_source() if self._is_playing else self._audio_offset_in_source
        return pos + 30.0 > self._duration_sec + 1e-3

    def _tinder_jump_target_beyond_track(self, target_sec: float) -> bool:
        """Keyboard / Tinder: preset past end."""
        if self._sorting_mode != "swipe":
            return False
        return self._absolute_jump_past_track_end(target_sec)

    def _tinder_delta_plus_would_exceed(self) -> bool:
        """Keyboard + Tinder only: +30s would pass track end (genre keeps D key usable)."""
        if self._sorting_mode != "swipe":
            return False
        return self._delta_plus_past_track_end()

    def _clear_tinder_jump_mouse_error(self) -> None:
        if self._jump_mouse_error_lbl is None:
            return
        lw = self._jump_mouse_error_lbl
        self._jump_mouse_error_lbl = None
        try:
            delattr(lw, "_tinder_hold_invalid")
        except AttributeError:
            pass
        self._refresh_all_widget_chrome()

    def _on_global_button_release(self, _event: object) -> None:
        """End of click: clear red invalid-jump state (over-duration preset or +30s past end)."""
        if self._jump_mouse_error_lbl is not None:
            self._clear_tinder_jump_mouse_error()

    def _on_tinder_digit_key_release(self, digit: int, _event: object) -> None:
        if self._tinder_kb_jump_error_digit != digit:
            return
        self._tinder_kb_jump_error_digit = None
        self._refresh_all_widget_chrome()

    def _on_tinder_delta_plus_key_release(self, _event: object) -> None:
        if not self._tinder_kb_jump_error_plus30:
            return
        self._tinder_kb_jump_error_plus30 = False
        self._refresh_all_widget_chrome()

    def _style_tk_button_load_inner(self, b: tk.Button) -> None:
        """Load folder inner: app bg + teal text; never system white."""
        b.configure(
            bg=self.bg,
            fg=self.accent,
            activebackground=self.panel,
            activeforeground=self.btn_dark_active_fg,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
            highlightbackground=self.bg,
            highlightcolor=self.bg,
            overrelief="flat",
            cursor="",
        )

    def _apply_all_tk_button_chrome(self) -> None:
        """Reassert all tk.Button colors (macOS resets them after dialogs / state changes)."""
        try:
            if self._load_folder_ring is not None:
                self._load_folder_ring.configure(bg=self.accent)
            self._style_tk_button_load_inner(self.btn_load)
            if self.btn_load_default_folder is not None:
                self._style_tk_button_transport_grey(self.btn_load_default_folder)
        except tk.TclError:
            pass
        try:
            self._apply_order_toggle_chrome()
        except tk.TclError:
            pass
        try:
            self._apply_transport_utility_row_chrome()
        except tk.TclError:
            pass
        sel: JumpUiKey = self._jump_highlight_key
        sec_for_btn = {btn: sec for sec, btn in self._preset_jump_buttons.items()}
        for b in self._jump_buttons:
            try:
                if not self._tiles_enabled:
                    self._style_jump_label_disabled(b)
                    continue
                if b is self._jump_btn_minus or b is self._jump_btn_plus:
                    self._style_jump_label_idle(b)
                elif b is self._jump_btn_pb_start:
                    if sel == "pb_start":
                        self._style_jump_label_active(b)
                    else:
                        self._style_jump_label_idle(b)
                elif b is self._jump_btn_outro:
                    if sel == "outro":
                        self._style_jump_label_active(b)
                    else:
                        self._style_jump_label_idle(b)
                elif b in sec_for_btn:
                    sec = sec_for_btn[b]
                    if isinstance(sel, int) and sec == sel:
                        self._style_jump_label_active(b)
                    else:
                        self._style_jump_label_idle(b)
                else:
                    self._style_jump_label_idle(b)
            except tk.TclError:
                pass
        try:
            self._apply_mode_switch_chrome()
        except tk.TclError:
            pass
        try:
            for _hdr in (self._tile_guide_inner, self._tile_settings_inner, self._tile_queue_inner):
                if _hdr is not None:
                    self._restore_static_header_action_tile(_hdr)
        except tk.TclError:
            pass

    def _reapply_action_tiles(self) -> None:
        """Reassert Keep/Archive/Favorite + genre grid colors (Frames + Labels, not affected by *Button option_db)."""
        genre_inners = list(self._genre_tile_inners)
        for inner in (
            self._tile_keep_inner,
            self._tile_archive_inner,
            self._tile_favorite_inner,
            self._grid_kaf_keep_inner,
            self._grid_kaf_archive_inner,
            self._grid_kaf_favorite_inner,
            *genre_inners,
        ):
            if inner is None:
                continue
            try:
                inner.configure(highlightbackground=self.panel_border, highlightcolor=self.panel_border)
                self._set_action_tile_enabled(inner, self._tiles_enabled)
            except tk.TclError:
                pass

    def _refresh_all_widget_chrome(self) -> None:
        """Buttons + action tiles after folder load, track change, play/pause, help toggle, focus."""
        self._apply_all_tk_button_chrome()
        self._reapply_action_tiles()
        self._reapply_time_display_labels()

    def _time_display_label_kw(self) -> dict:
        """tk.Label only — Buttons drew a light native backing on macOS. Teal text on app bg."""
        g = self.bg
        return {
            "bg": g,
            "fg": self.accent,
            "highlightthickness": 0,
            "highlightbackground": g,
            "highlightcolor": g,
            "bd": 0,
            "padx": 0,
            "pady": 0,
        }

    def _readonly_time_label(
        self,
        parent: tk.Misc,
        *,
        text: str,
        font: tuple,
        width: Optional[int],
        anchor: str,
    ) -> tk.Label:
        justify = "center" if anchor == "center" else ("right" if anchor in ("e", "ne", "se") else "left")
        kw: dict = dict(
            text=text,
            font=font,
            anchor=anchor,
            justify=justify,
            cursor="",
            **self._time_display_label_kw(),
        )
        if width is not None and width > 0:
            kw["width"] = width
        return tk.Label(parent, **kw)

    def _raise_time_text_above_scrubber(self) -> None:
        """Canvas is packed after elapsed and draws on top of it in Z-order; keep time above the bar."""
        try:
            self.lbl_elapsed.lift(self.progress_canvas)
            self.lbl_total.lift(self.progress_canvas)
        except tk.TclError:
            pass

    def _set_elapsed_seconds(self, sec: float) -> None:
        self.lbl_elapsed.configure(text=format_sec(sec), **self._time_display_label_kw())

    def _set_total_seconds(self, sec: float) -> None:
        self.lbl_total.configure(text=format_sec(sec), **self._time_display_label_kw())

    def _set_playback_start_line(self, time_part: str) -> None:
        """Legacy hook: playback start is shown on the active jump tile; keep for callers."""
        del time_part  # unused — _playback_start_label is still updated by recompute paths

    def _reapply_time_display_labels(self) -> None:
        """Reassert colors without changing text (dialogs / focus / periodic tick)."""
        kw = self._time_display_label_kw()
        for w in (self.lbl_elapsed, self.lbl_total, self.lbl_track):
            try:
                w.configure(**kw)
            except tk.TclError:
                pass
        self._raise_time_text_above_scrubber()

    def _outro_jump_seconds(self) -> float:
        """Last minute of the track (60s before end), clamped to >= 0."""
        if self._duration_sec <= 0:
            return 0.0
        return max(0.0, self._duration_sec - 60.0)

    def jump_to_outro(self) -> None:
        self.jump_to(self._outro_jump_seconds(), jump_ui_key="outro")

    def _jump_to_playback_start_slot(self) -> None:
        if not self._tiles_enabled:
            return
        ds = float(self._desired_start_sec)
        if self._absolute_jump_past_track_end(ds):
            if self._jump_btn_pb_start is not None:
                self._style_jump_label_error_hold(self._jump_btn_pb_start)
            return
        self.jump_to(ds, jump_ui_key="pb_start")

    def _sync_pb_start_jump_button_label(self) -> None:
        lb = self._jump_btn_pb_start
        if lb is None:
            return
        part = str(self._playback_start_label)
        base = f"START\n{part}"
        lb._jump_base_text = base  # type: ignore[attr-defined]
        lb._jump_time_only = part  # type: ignore[attr-defined]
        if self._is_active_jump_label(lb):
            return
        idle_font = getattr(lb, "_jump_idle_font", ("Helvetica", 13, "bold"))
        try:
            lb.configure(text=base, font=idle_font)
        except tk.TclError:
            pass

    def _set_play_button_text(self, text: str) -> None:
        """Sync play/pause glyph + title on the transport tile; re-chrome the app."""
        is_pause = text.strip().lower().startswith("pause")
        glyph = "❚❚" if is_pause else "▶"
        title = "PAUSE" if is_pause else "PLAY"
        if self._transport_play_icon_label is not None:
            try:
                self._transport_play_icon_label.configure(text=glyph)
            except tk.TclError:
                pass
        bp = getattr(self, "btn_play", None)
        if bp is not None:
            tl = getattr(bp, "_utility_title", None)
            if isinstance(tl, tk.Label):
                try:
                    tl.configure(text=title)
                except tk.TclError:
                    pass
        self._refresh_all_widget_chrome()

    def _dj_button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        kind: ButtonKind = "panel",
        width: int = 0,
        padx: int = 10,
        pady: int = 10,
    ) -> tk.Button:
        if kind == "accent":
            bg, fg = self.accent, self.teal_on_fg
            abg, afg = self.accent_btn_press, self.teal_on_fg
        elif kind == "transport":
            bg, fg = self.transport_grey_bg, self.time_text_fg
            abg, afg = self.transport_grey_active, self.time_text_fg
        elif kind == "archive":
            bg, fg = self.archive_bg, self.archive_fg
            abg, afg = self.archive_active, self.archive_fg
        elif kind == "favorite":
            bg, fg = self.favorite_bg, self.favorite_on_fg
            abg, afg = self.favorite_active, self.favorite_on_fg
        else:
            bg, fg = self.skip_bg, self.fg
            abg, afg = self.skip_active, self.fg

        b = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=abg,
            activeforeground=afg,
            disabledforeground=self.disabled_fg,
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
            highlightbackground=bg,
            highlightcolor=bg,
            overrelief="flat",
            takefocus=False,
            padx=padx,
            pady=pady,
            font=("Helvetica", 11, "bold"),
            width=width,
            cursor="",
        )
        if kind == "accent":
            self._style_tk_button_accent(b)
        elif kind == "transport":
            self._style_tk_button_transport_grey(b)
        return b

    def _load_folder_button(self, parent: tk.Misc, command: Callable[[], None]) -> tuple[tk.Frame, tk.Button]:
        """Teal outline = Frame ring; inner tk.Button uses dark chrome (no system-white fill)."""
        ring = tk.Frame(parent, bg=self.accent)
        b = tk.Button(
            ring,
            text="📂  Load folder",
            command=command,
            font=("Helvetica", 12, "bold"),
            takefocus=True,
            padx=14,
            pady=8,
        )
        self._style_tk_button_load_inner(b)
        b.pack(padx=2, pady=2)
        return ring, b

    def _jump_time_button(
        self,
        parent: tk.Misc,
        base_text: str,
        command: Callable[[], None],
        *,
        hint_suffix: Optional[str] = None,
        width: int = 8,
        tinder_target_sec: Optional[float] = None,
        tinder_delta_plus: bool = False,
    ) -> tk.Label:
        """Clickable Label tiles — macOS Tk Buttons often render unusable light-grey fills."""
        idle_font = ("Helvetica Neue", 11, "bold")
        active_font = ("Helvetica Neue", 10, "bold")

        def on_press(_e: object) -> None:
            if not self._tiles_enabled:
                return
            if tinder_target_sec is not None and self._absolute_jump_past_track_end(float(tinder_target_sec)):
                if self._sorting_mode in ("swipe", "grid"):
                    lbl._tinder_hold_invalid = True  # type: ignore[attr-defined]
                    self._jump_mouse_error_lbl = lbl
                    self._style_jump_label_error_hold(lbl)
                    return
            if tinder_delta_plus and self._delta_plus_past_track_end():
                if self._sorting_mode in ("swipe", "grid"):
                    lbl._tinder_hold_invalid = True  # type: ignore[attr-defined]
                    self._jump_mouse_error_lbl = lbl
                    self._style_jump_label_error_hold(lbl)
                    return
            command()

        opt: dict = dict(
            text=base_text,
            font=idle_font,
            padx=6,
            pady=7,
            anchor="center",
            justify="center",
            cursor="",
            bd=0,
        )
        if width > 0:
            opt["width"] = width
        lbl = tk.Label(parent, **opt)
        lbl._jump_base_text = base_text  # type: ignore[attr-defined]
        lbl._jump_idle_font = idle_font  # type: ignore[attr-defined]
        lbl._jump_active_font = active_font  # type: ignore[attr-defined]
        if tinder_target_sec is not None:
            lbl._jump_time_only = format_sec(float(tinder_target_sec))  # type: ignore[attr-defined]
        elif "Outro" in base_text or "OUTRO" in base_text:
            lbl._jump_time_only = "Outro"  # type: ignore[attr-defined]
        else:
            lbl._jump_time_only = ""  # type: ignore[attr-defined]
        lbl.bind("<ButtonPress-1>", on_press)
        if hint_suffix:

            def _hint_enter(_e: object) -> None:
                if self._sorting_mode == "grid" and (hint_suffix or "").strip().isdigit():
                    return
                if self._is_active_jump_label(lbl):
                    return
                try:
                    lbl.configure(text=base_text + hint_suffix)
                except tk.TclError:
                    pass

            def _hint_leave(_e: object) -> None:
                if self._is_active_jump_label(lbl):
                    return
                try:
                    lbl.configure(text=base_text, font=idle_font)
                except tk.TclError:
                    pass

            lbl.bind("<Enter>", _hint_enter)
            lbl.bind("<Leave>", _hint_leave)
        self._style_jump_label_idle(lbl)
        return lbl

    def _action_tile(
        self,
        parent: tk.Misc,
        icon: str,
        title: str,
        shortcut: str,
        bg: str,
        active_bg: str,
        fg: str,
        shortcut_fg: str,
        command: Callable[[], None],
        *,
        clickable_when_tiles_disabled: bool = False,
        show_icon: bool = True,
        icon_font: tuple | None = None,
        title_font: tuple | None = None,
        shortcut_font: tuple | None = None,
        row_gap: int | None = None,
    ) -> tuple[tk.Frame, tk.Frame]:
        """Unified tile: icon / title / shortcut — same layout as transport row."""
        outer = tk.Frame(parent, bg=self.bg, cursor="")
        inner = tk.Frame(
            outer,
            bg=bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
            cursor="",
        )
        inner._tile_hovering = False  # type: ignore[attr-defined]
        inner._tile_flash_when_disabled = bool(clickable_when_tiles_disabled)  # type: ignore[attr-defined]
        inner._is_genre_tile = False  # type: ignore[attr-defined]
        gap = row_gap if row_gap is not None else _ACTION_TILE_ROW_GAP_PX
        sc_text = _shortcut_caption_loose(shortcut)
        _icon_font = icon_font if icon_font is not None else _ACTION_TILE_ICON_FONT
        _title_font = title_font if title_font is not None else _ACTION_TILE_TITLE_FONT
        _shortcut_font = shortcut_font if shortcut_font is not None else _ACTION_TILE_SHORTCUT_FONT

        def on_click(_e: object) -> None:
            if not self._tiles_enabled and not clickable_when_tiles_disabled:
                return
            command()
            self._flash_tile_widget(inner)

        ic = tk.Label(
            inner,
            text=icon,
            bg=bg,
            fg=fg,
            font=_icon_font,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        tl = tk.Label(
            inner,
            text=title,
            bg=bg,
            fg=fg,
            font=_title_font,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        sc = tk.Label(
            inner,
            text=sc_text,
            bg=bg,
            fg=shortcut_fg,
            font=_shortcut_font,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        inner.rowconfigure(4, weight=1)
        if show_icon:
            ic.grid(row=1, column=0, sticky="")
            tl.grid(row=2, column=0, sticky="", pady=(gap, 0))
            sc.grid(row=3, column=0, sticky="", pady=(2, 0))
            bind_targets = (outer, inner, ic, tl, sc)
            inner._tile_widgets = (ic, tl, sc)  # type: ignore[attr-defined]
        else:
            tl.grid(row=2, column=0, sticky="", pady=(gap, 0))
            sc.grid(row=3, column=0, sticky="", pady=(2, 0))
            bind_targets = (outer, inner, tl, sc)
            inner._tile_widgets = (tl, sc)  # type: ignore[attr-defined]
        for w in bind_targets:
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", lambda _e, inn=inner: self._on_unified_action_tile_enter(inn))
            w.bind("<Leave>", lambda _e, inn=inner: self._on_unified_action_tile_leave(inn))
        inner._tile_bg = bg  # type: ignore[attr-defined]
        inner._tile_active_bg = active_bg  # type: ignore[attr-defined]
        inner._tile_fg = fg  # type: ignore[attr-defined]
        inner._tile_shortcut_fg = shortcut_fg  # type: ignore[attr-defined]
        inner.pack(
            fill="both",
            expand=True,
            padx=_ACTION_TILE_FRAME_PAD_PX,
            pady=_ACTION_TILE_FRAME_PAD_PX,
        )
        return outer, inner

    def _transport_utility_cell_click_ok(self, role: str) -> bool:
        if role == "undo":
            return self._tiles_enabled or bool(self._undo_stack)
        return self._tiles_enabled

    def _build_transport_utility_cell(
        self,
        parent: tk.Misc,
        icon: str,
        title: str,
        shortcut: str,
        command: Callable[[], None],
        *,
        role: str,
        clickable_when_tiles_disabled: bool = False,
    ) -> tuple[tk.Frame, tk.Frame]:
        """Transport control: icon + title + shortcut (same information density as action tiles)."""
        outer = tk.Frame(parent, bg=self.bg, cursor="")
        outer._utility_transport_cell = True  # type: ignore[attr-defined]
        outer._utility_role = role  # type: ignore[attr-defined]
        outer._utility_command = command  # type: ignore[attr-defined]
        outer._utility_click_ok = bool(clickable_when_tiles_disabled)  # type: ignore[attr-defined]

        inner = tk.Frame(
            outer,
            bg=self.skip_bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
            cursor="",
        )
        outer._utility_inner = inner  # type: ignore[attr-defined]
        inner._utility_hovering = False  # type: ignore[attr-defined]

        sc_text = _shortcut_caption_loose(shortcut)
        icon_lab = tk.Label(
            inner,
            text=icon,
            bg=self.skip_bg,
            fg=self.fg,
            font=("Helvetica Neue", 28),
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        title_lab = tk.Label(
            inner,
            text=title.upper(),
            bg=self.skip_bg,
            fg=self.fg,
            font=("Helvetica Neue", 12, "bold"),
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        sub = tk.Label(
            inner,
            text=sc_text,
            bg=self.skip_bg,
            fg=self.muted,
            font=("Menlo", 10),
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        outer._utility_icon = icon_lab  # type: ignore[attr-defined]
        outer._utility_title = title_lab  # type: ignore[attr-defined]
        outer._utility_sub = sub  # type: ignore[attr-defined]
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        inner.rowconfigure(4, weight=1)
        icon_lab.grid(row=1, column=0, sticky="")
        title_lab.grid(row=2, column=0, sticky="", pady=(5, 0))
        sub.grid(row=3, column=0, sticky="", pady=(2, 0))

        for w in (outer, inner, icon_lab, title_lab, sub):
            w.bind("<Button-1>", lambda e, o=outer: self._on_transport_utility_cell_press(o, e))
            w.bind("<Enter>", lambda _e, o=outer: self._on_transport_utility_cell_enter(o))
            w.bind("<Leave>", lambda _e, o=outer: self._on_transport_utility_cell_leave(o))

        inner.pack(fill="both", expand=True, padx=6, pady=6)
        return outer, inner

    def _on_transport_utility_cell_press(self, outer: tk.Frame, _e: object) -> None:
        role = str(getattr(outer, "_utility_role", ""))
        undo_ok = self._tiles_enabled or bool(self._undo_stack)
        if role == "undo" and getattr(outer, "_utility_click_ok", False):
            if not undo_ok:
                return
        elif not self._tiles_enabled:
            return
        cmd = getattr(outer, "_utility_command", None)
        if callable(cmd):
            cmd()
        self._flash_transport_utility_cell(outer)

    def _on_transport_utility_cell_enter(self, outer: tk.Frame, _e: Optional[object] = None) -> None:
        role = str(getattr(outer, "_utility_role", ""))
        if not self._transport_utility_cell_click_ok(role):
            return
        inner = getattr(outer, "_utility_inner", None)
        if inner is None or bool(getattr(inner, "_utility_hovering", False)):
            return
        icon_lab = getattr(outer, "_utility_icon", None)
        title_lab = getattr(outer, "_utility_title", None)
        sub = getattr(outer, "_utility_sub", None)
        if not isinstance(icon_lab, tk.Label) or not isinstance(sub, tk.Label):
            return
        inner._utility_hovering = True  # type: ignore[attr-defined]
        hb = self.action_tile_border_hover
        try:
            base_inner_bg = str(inner.cget("bg"))
        except tk.TclError:
            base_inner_bg = self.skip_bg
        hover_bg = _mix_hex_toward_white(base_inner_bg, _ACTION_TILE_HOVER_MIX)
        try:
            inner.configure(bg=hover_bg, highlightbackground=hb, highlightcolor=hb, cursor="hand2")
            icon_lab.configure(
                bg=hover_bg,
                fg=self.teal_on_fg if role == "play" else self.fg,
                cursor="hand2",
            )
            if isinstance(title_lab, tk.Label):
                title_lab.configure(
                    bg=hover_bg,
                    fg=self.teal_on_fg if role == "play" else self.fg,
                    cursor="hand2",
                )
            sub.configure(
                bg=hover_bg,
                fg="#1A1A1A" if role == "play" else self.muted,
                cursor="hand2",
            )
            outer.configure(cursor="hand2")
        except tk.TclError:
            pass

    def _on_transport_utility_cell_leave(self, outer: tk.Frame, _e: Optional[object] = None) -> None:
        inner = getattr(outer, "_utility_inner", None)
        if inner is not None:
            inner._utility_hovering = False  # type: ignore[attr-defined]
        try:
            outer.configure(cursor="")
        except tk.TclError:
            pass
        self._apply_transport_utility_cell_chrome(outer)

    def _apply_transport_utility_cell_chrome(self, outer: tk.Frame) -> None:
        inner = getattr(outer, "_utility_inner", None)
        icon_lab = getattr(outer, "_utility_icon", None)
        title_lab = getattr(outer, "_utility_title", None)
        sub = getattr(outer, "_utility_sub", None)
        if inner is None or not isinstance(icon_lab, tk.Label) or not isinstance(sub, tk.Label):
            return
        role = str(getattr(outer, "_utility_role", ""))
        undo_ok = self._tiles_enabled or bool(self._undo_stack)
        if role == "undo" and getattr(outer, "_utility_click_ok", False):
            on = undo_ok
        else:
            on = self._tiles_enabled
        inner._utility_hovering = False  # type: ignore[attr-defined]
        hlb = self.panel_border if on else self.trough
        title_l = title_lab if isinstance(title_lab, tk.Label) else None
        if on:
            if role == "play":
                bg_n = self.accent
                fg_i = self.teal_on_fg
                fg_t = self.teal_on_fg
                fg_s = "#1A1A1A"
                hlb = self.accent
            else:
                bg_n = self.skip_bg
                fg_i = self.fg
                fg_t = self.fg
                fg_s = self.muted
            try:
                inner.configure(
                    bg=bg_n,
                    highlightthickness=1,
                    highlightbackground=hlb,
                    highlightcolor=hlb,
                    cursor="hand2",
                )
                icon_lab.configure(bg=bg_n, fg=fg_i, cursor="hand2")
                if title_l is not None:
                    title_l.configure(bg=bg_n, fg=fg_t, cursor="hand2")
                sub.configure(bg=bg_n, fg=fg_s, cursor="hand2")
            except tk.TclError:
                pass
        else:
            try:
                inner.configure(
                    bg=self.trough,
                    highlightthickness=1,
                    highlightbackground=hlb,
                    highlightcolor=hlb,
                    cursor="",
                )
                icon_lab.configure(bg=self.trough, fg=self.disabled_fg, cursor="")
                if title_l is not None:
                    title_l.configure(bg=self.trough, fg=self.disabled_fg, cursor="")
                sub.configure(bg=self.trough, fg=self.disabled_fg, cursor="")
            except tk.TclError:
                pass

    def _apply_transport_utility_row_chrome(self) -> None:
        for name in ("btn_play", "btn_replay", "btn_skip", "btn_undo"):
            outer = getattr(self, name, None)
            if outer is None or not getattr(outer, "_utility_transport_cell", False):
                continue
            self._apply_transport_utility_cell_chrome(outer)

    def _flash_transport_utility_cell(self, outer: tk.Frame) -> None:
        role = str(getattr(outer, "_utility_role", ""))
        allow = self._tiles_enabled or (role == "undo" and bool(self._undo_stack))
        if not allow:
            return
        inner = getattr(outer, "_utility_inner", None)
        icon_lab = getattr(outer, "_utility_icon", None)
        title_lab = getattr(outer, "_utility_title", None)
        sub = getattr(outer, "_utility_sub", None)
        if inner is None or not isinstance(icon_lab, tk.Label) or not isinstance(sub, tk.Label):
            return
        key = id(outer)
        prev_after = self._flash_after_ids.pop(key, None)
        if prev_after is not None:
            try:
                outer.after_cancel(prev_after)
            except Exception:
                pass

        def restore() -> None:
            self._flash_after_ids.pop(key, None)
            self._refresh_all_widget_chrome()

        try:
            inner.configure(bg=self.accent_active, highlightbackground=self.accent_active, highlightcolor=self.accent_active)
            icon_lab.configure(bg=self.accent_active, fg=self.fg)
            if isinstance(title_lab, tk.Label):
                title_lab.configure(bg=self.accent_active, fg=self.fg)
            sub.configure(bg=self.accent_active, fg=self.fg)
        except tk.TclError:
            pass
        self._flash_after_ids[key] = outer.after(95, restore)

    def _genre_action_tile(
        self,
        parent: tk.Misc,
        genre_name: str,
        digit: str,
        bg: str,
        active_bg: str,
        fg: str,
        shortcut_fg: str,
        command: Callable[[], None],
    ) -> tuple[tk.Frame, tk.Frame]:
        """Genre slot: same three-row system (♪ / name / [n])."""
        outer = tk.Frame(parent, bg=self.bg, cursor="")
        inner = tk.Frame(
            outer,
            bg=bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
            cursor="",
        )
        inner._tile_hovering = False  # type: ignore[attr-defined]
        inner._tile_flash_when_disabled = False  # type: ignore[attr-defined]
        inner._is_genre_tile = True  # type: ignore[attr-defined]
        gap = _ACTION_TILE_ROW_GAP_PX
        sc_text = _shortcut_caption_loose(f"[{digit.strip()}]")

        def on_click(_e: object) -> None:
            if not self._tiles_enabled:
                return
            command()
            self._flash_tile_widget(inner)

        ic = tk.Label(
            inner,
            text="♪",
            bg=bg,
            fg=fg,
            font=_ACTION_TILE_ICON_FONT,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        tl = tk.Label(
            inner,
            text=genre_name.strip().upper(),
            bg=bg,
            fg=fg,
            font=_ACTION_TILE_TITLE_FONT,
            anchor="center",
            justify="center",
            wraplength=96,
            highlightthickness=0,
            cursor="",
        )
        sc = tk.Label(
            inner,
            text=sc_text,
            bg=bg,
            fg=shortcut_fg,
            font=_ACTION_TILE_SHORTCUT_FONT,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        inner.rowconfigure(4, weight=1)
        ic.grid(row=1, column=0, sticky="")
        tl.grid(row=2, column=0, sticky="", pady=(gap, gap))
        sc.grid(row=3, column=0, sticky="")
        for w in (outer, inner, ic, tl, sc):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", lambda _e, inn=inner: self._on_unified_action_tile_enter(inn))
            w.bind("<Leave>", lambda _e, inn=inner: self._on_unified_action_tile_leave(inn))
        inner._tile_bg = bg  # type: ignore[attr-defined]
        inner._tile_active_bg = active_bg  # type: ignore[attr-defined]
        inner._tile_fg = fg  # type: ignore[attr-defined]
        inner._tile_shortcut_fg = shortcut_fg  # type: ignore[attr-defined]
        inner._tile_widgets = (ic, tl, sc)  # type: ignore[attr-defined]
        inner._genre_name_label = tl  # type: ignore[attr-defined]
        inner.pack(
            fill="both",
            expand=True,
            padx=_ACTION_TILE_FRAME_PAD_PX,
            pady=_ACTION_TILE_FRAME_PAD_PX,
        )
        return outer, inner

    def _unified_action_tile_hover_ok(self, inner: tk.Frame) -> bool:
        if getattr(inner, "_is_genre_tile", False):
            return self._tiles_enabled
        if inner in (
            self._tile_mode_cleanup_inner,
            self._tile_mode_genre_inner,
            self._tile_order_random_inner,
            self._tile_order_in_order_inner,
            self._tile_guide_inner,
            self._tile_settings_inner,
            self._tile_queue_inner,
        ):
            return True
        return self._tiles_enabled

    def _restore_static_header_action_tile(self, inner: tk.Frame) -> None:
        """Guide / Settings / Queue: idle skip-style tile (always available, not tied to queue loaded)."""
        inner._tile_hovering = False  # type: ignore[attr-defined]
        bg = self.skip_bg
        hlb = self.panel_border
        fg_t = self.fg
        fg_sc = self.muted
        try:
            inner.configure(
                bg=bg,
                highlightthickness=1,
                highlightbackground=hlb,
                highlightcolor=hlb,
                cursor="",
            )
        except tk.TclError:
            return
        widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
        if not widgets:
            return
        shortcut_w = widgets[-1]
        for w in widgets:
            try:
                w.configure(bg=bg, fg=fg_sc if w is shortcut_w else fg_t, cursor="")
            except tk.TclError:
                pass

    def _restore_unified_action_tile_surface(self, inner: tk.Frame) -> None:
        inner._tile_hovering = False  # type: ignore[attr-defined]
        if getattr(inner, "_is_genre_tile", False):
            self._apply_genre_tile_colors()
            return
        if inner in (self._tile_mode_cleanup_inner, self._tile_mode_genre_inner):
            self._apply_mode_switch_chrome()
            return
        if inner in (self._tile_guide_inner, self._tile_settings_inner, self._tile_queue_inner):
            self._restore_static_header_action_tile(inner)
            return
        if inner in (self._tile_order_random_inner, self._tile_order_in_order_inner):
            self._apply_order_toggle_chrome()
            return
        self._set_action_tile_enabled(inner, self._tiles_enabled)

    def _on_unified_action_tile_enter(self, inner: tk.Frame, _e: Optional[object] = None) -> None:
        if getattr(inner, "_tile_hovering", False):
            return
        if not self._unified_action_tile_hover_ok(inner):
            return
        widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
        if not widgets:
            return
        sc = widgets[-1]
        try:
            base = inner.cget("bg")
        except tk.TclError:
            return
        hover_bg = _mix_hex_toward_white(base, _ACTION_TILE_HOVER_MIX)
        hb = self.action_tile_border_hover
        fg_main = str(getattr(inner, "_tile_fg", self.fg))
        fg_sc = str(getattr(inner, "_tile_shortcut_fg", self.muted))
        inner._tile_hovering = True  # type: ignore[attr-defined]
        try:
            inner.configure(bg=hover_bg, highlightbackground=hb, highlightcolor=hb, cursor="hand2")
            for w in widgets:
                w.configure(bg=hover_bg, fg=fg_sc if w is sc else fg_main, cursor="hand2")
        except tk.TclError:
            pass
        try:
            inner.master.configure(cursor="hand2")  # type: ignore[union-attr]
        except tk.TclError:
            pass

    def _on_unified_action_tile_leave(self, inner: tk.Frame, _e: Optional[object] = None) -> None:
        try:
            inner.master.configure(cursor="")  # type: ignore[union-attr]
        except tk.TclError:
            pass
        self._restore_unified_action_tile_surface(inner)

    def _set_action_tile_enabled(self, inner: tk.Frame, on: bool) -> None:
        inner._tile_hovering = False  # type: ignore[attr-defined]
        widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
        if not widgets:
            return
        shortcut_w = widgets[-1]
        bg_n = str(getattr(inner, "_tile_bg", self.panel))
        fg_n = str(getattr(inner, "_tile_fg", self.fg))
        sc_fg = str(getattr(inner, "_tile_shortcut_fg", self.muted))
        hlb = self.panel_border if on else self.trough
        if on:
            inner.configure(
                cursor="hand2",
                bg=bg_n,
                highlightthickness=1,
                highlightbackground=hlb,
                highlightcolor=hlb,
            )
            for w in widgets:
                w.configure(
                    bg=bg_n,
                    fg=fg_n if w is not shortcut_w else sc_fg,
                    highlightthickness=0,
                    cursor="hand2",
                )
        else:
            inner.configure(
                cursor="",
                bg=self.trough,
                highlightthickness=1,
                highlightbackground=hlb,
                highlightcolor=hlb,
            )
            for w in widgets:
                w.configure(bg=self.trough, fg=self.disabled_fg, highlightthickness=0, cursor="")

    def _on_help_key(self, _e: object) -> Optional[str]:
        if not self._shortcuts_active():
            return None
        self._toggle_guide_panel_visibility()
        if self._tile_guide_inner is not None:
            self._flash_tile_widget(self._tile_guide_inner)
        return "break"

    def _apply_dual_tile_selection_chrome(
        self,
        inner_left: tk.Frame,
        inner_right: tk.Frame,
        *,
        left_selected: bool,
    ) -> None:
        """Two compact tiles: left active = teal selection ring; same rules as Swipe / Grid mode row."""
        for inner, active in (
            (inner_left, left_selected),
            (inner_right, not left_selected),
        ):
            inner._tile_hovering = False  # type: ignore[attr-defined]
            if active:
                inner._tile_bg = self.accent  # type: ignore[attr-defined]
                inner._tile_active_bg = self.accent_active  # type: ignore[attr-defined]
                inner._tile_fg = self.teal_on_fg  # type: ignore[attr-defined]
                inner._tile_shortcut_fg = "#1A1A1A"  # type: ignore[attr-defined]
                bg = self.accent
                hlb = self.accent
            else:
                inner._tile_bg = self.skip_bg  # type: ignore[attr-defined]
                inner._tile_active_bg = self.skip_active  # type: ignore[attr-defined]
                inner._tile_fg = self.fg  # type: ignore[attr-defined]
                inner._tile_shortcut_fg = self.muted  # type: ignore[attr-defined]
                bg = self.skip_bg
                hlb = self.panel_border
            try:
                inner.configure(bg=bg, highlightthickness=1, highlightbackground=hlb, highlightcolor=hlb, cursor="")
            except tk.TclError:
                pass
            widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
            if not widgets:
                continue
            shortcut_w = widgets[-1]
            fg_t = str(inner._tile_fg)  # type: ignore[attr-defined]
            fg_sc = str(inner._tile_shortcut_fg)  # type: ignore[attr-defined]
            for w in widgets:
                try:
                    w.configure(bg=bg, fg=fg_t if w is not shortcut_w else fg_sc, cursor="")
                except tk.TclError:
                    pass

    def _apply_mode_switch_chrome(self) -> None:
        """Same skip-style tile chrome as the action row; active mode uses teal like Keep."""
        if self._tile_mode_cleanup_inner is None or self._tile_mode_genre_inner is None:
            return
        self._apply_dual_tile_selection_chrome(
            self._tile_mode_cleanup_inner,
            self._tile_mode_genre_inner,
            left_selected=(self._sorting_mode == "swipe"),
        )

    def _reset_jump_time_button_captions(self) -> None:
        """Clear hover suffix if visible (e.g. after switching to genre mode)."""
        for b in self._jump_buttons:
            bt = getattr(b, "_jump_base_text", None)
            if isinstance(bt, str):
                try:
                    b.configure(text=bt)
                except tk.TclError:
                    pass

    def _apply_scan_track_order(self, paths: list[Path]) -> list[Path]:
        if self._track_order == "random":
            out = list(paths)
            random.shuffle(out)
            return out
        return sorted(paths, key=lambda p: str(p.resolve()).lower())

    def _apply_order_toggle_chrome(self) -> None:
        if self._tile_order_random_inner is None or self._tile_order_in_order_inner is None:
            return
        self._apply_dual_tile_selection_chrome(
            self._tile_order_random_inner,
            self._tile_order_in_order_inner,
            left_selected=(self._track_order == "random"),
        )

    def _reorder_loaded_queue_preserving_current(self) -> None:
        """Rebuild queue order without changing the current track or undo stack."""
        if not self._queue:
            return
        was_playing = self._is_playing
        paths = list(self._queue)
        current = paths[0]
        if self._track_order == "random":
            rest = list(paths[1:])
            random.shuffle(rest)
            self._queue = [current] + rest
        else:
            sorted_all = sorted(paths, key=lambda p: str(p.resolve()).lower())
            i = sorted_all.index(current)
            self._queue = sorted_all[i:] + sorted_all[:i]
        self.load_current_track(autoplay=was_playing)

    def _on_pick_track_order(self, order: TrackOrder) -> None:
        if order == self._track_order:
            return
        if self._queue:
            self._prepare_native_dialog()
            if not messagebox.askyesno(
                "Reload track order?",
                "Reload track list with new order?",
                parent=self.root,
            ):
                return
        self._track_order = order
        save_track_order_setting(order)
        self._apply_order_toggle_chrome()
        if self._queue:
            self._reorder_loaded_queue_preserving_current()
        self._refresh_all_widget_chrome()

    def _set_sorting_mode(self, mode: SortingMode) -> None:
        if mode == self._sorting_mode:
            return
        self._sorting_mode = mode
        self._jump_mouse_error_lbl = None
        self._tinder_kb_jump_error_digit = None
        self._tinder_kb_jump_error_plus30 = False
        self._reset_jump_time_button_captions()
        self._sync_pb_start_jump_button_label()
        if self.lbl_mode_header is not None:
            self._refresh_mode_status_label()
        self._apply_mode_switch_chrome()
        if self.cleanup_outer is not None and self.genre_actions is not None:
            if mode == "swipe":
                self.genre_actions.pack_forget()
                self.cleanup_outer.pack(fill="both", expand=True, padx=_CONTENT_GUTTER, pady=(4, 8))
            else:
                self.cleanup_outer.pack_forget()
                self.genre_actions.pack(fill="both", expand=True, padx=_CONTENT_GUTTER, pady=(4, 8))
        self._refresh_stats()
        self._refresh_session_stats()
        self._refresh_all_widget_chrome()
        if self._music_root is not None:
            trip = tuple(str(x) for x in active_swipe_preset(self._mode_preset_store)["folders"][:3])
            self._folders = ensure_sort_folders(self._music_root, trip)

    def _sync_genre_tile_titles(self) -> None:
        for i, lab in enumerate(self._genre_title_labels):
            if i < len(self._genre_labels):
                try:
                    lab.configure(text=self._genre_labels[i].strip().upper())
                except tk.TclError:
                    pass

    def _genre_tile_active_bg(self, hx: str) -> str:
        return _mix_hex_toward_black(hx, 0.22)

    def _apply_genre_tile_colors(self) -> None:
        for i, inner in enumerate(self._genre_tile_inners):
            if i >= len(self._genre_colors):
                break
            hx = self._genre_colors[i]
            active = self._genre_tile_active_bg(hx)
            fg_m = _fg_for_genre_bg(hx)
            sc_m = _genre_shortcut_fg(fg_m)
            inner._tile_bg = hx  # type: ignore[attr-defined]
            inner._tile_active_bg = active  # type: ignore[attr-defined]
            inner._tile_fg = fg_m  # type: ignore[attr-defined]
            inner._tile_shortcut_fg = sc_m  # type: ignore[attr-defined]
            try:
                self._set_action_tile_enabled(inner, self._tiles_enabled)
            except tk.TclError:
                pass

    def _reset_genres_to_defaults_and_persist(self) -> None:
        gid = str(self._mode_preset_store.get("active_grid_preset_id") or "")
        gp = preset_by_id(self._mode_preset_store, gid)
        if not gp or gp.get("mode") != "grid":
            return
        blob = _coerce_grid_preset_blob(
            {
                "id": gid,
                "name": gp.get("name", "Grid"),
                "mode": "grid",
                "labels": list(DEFAULT_GENRE_LABELS),
                "colors": list(DEFAULT_GENRE_COLORS),
                "folders": [safe_genre_folder_name(lb) for lb in DEFAULT_GENRE_LABELS],
            },
        )
        if blob:
            self._upsert_preset_in_store(blob)
        self._apply_track_labels_from_preset_store()
        save_genre_config(self._genre_labels, self._genre_colors)
        if self._music_root is not None:
            ensure_genre_folders(self._music_root, self._genre_folder_names)
        self._sync_genre_tile_titles()
        self._apply_genre_tile_colors()
        self._refresh_stats()
        self.root.after(10, self._refresh_all_widget_chrome)

    def _open_edit_genres_dialog(self) -> None:
        self._prepare_native_dialog()
        top = tk.Toplevel(self.root)
        top.title("Edit grid preset")
        top.configure(bg=self.bg)
        try:
            top.transient(self.root)
        except tk.TclError:
            pass
        entries: list[tk.Entry] = []
        folder_entries: list[tk.Entry] = []
        color_vars: list[tk.StringVar] = []
        palette_names = [n for n, _ in GENRE_PALETTE]
        for i in range(9):
            row = tk.Frame(top, bg=self.bg)
            row.pack(fill="x", padx=14, pady=4)
            tk.Label(row, text=f"{i + 1}", bg=self.bg, fg=self.accent, width=3, anchor="e", font=("Helvetica", 11)).pack(
                side="left",
            )
            e = tk.Entry(
                row,
                width=18,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                highlightcolor=self.accent,
                font=("Helvetica", 11),
            )
            e.insert(0, self._genre_labels[i])
            e.pack(side="left", fill="x", expand=True, padx=(8, 4))
            entries.append(e)

            fe = tk.Entry(
                row,
                width=14,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                highlightcolor=self.accent,
                font=("Helvetica", 10),
            )
            fn0 = self._genre_folder_names[i] if i < len(self._genre_folder_names) else safe_genre_folder_name(self._genre_labels[i])
            fe.insert(0, fn0)
            fe.pack(side="left", padx=(0, 4))
            folder_entries.append(fe)

            var_c = tk.StringVar(value=_palette_name_for_hex(self._genre_colors[i]))
            color_vars.append(var_c)
            om = tk.OptionMenu(row, var_c, *palette_names)
            om.config(
                bg=self.panel,
                fg=self.fg,
                activebackground=self.btn_dark_active,
                activeforeground=self.btn_dark_active_fg,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                font=("Helvetica", 10),
            )
            om.pack(side="left", padx=(0, 6))
            try:
                om["menu"].configure(
                    bg=self.panel,
                    fg=self.fg,
                    activebackground=self.btn_dark_active,
                    activeforeground=self.btn_dark_active_fg,
                )
            except (tk.TclError, KeyError):
                pass
            sw = tk.Frame(
                row,
                width=28,
                height=22,
                bg=self._genre_colors[i],
                highlightthickness=1,
                highlightbackground=self.panel_border,
            )
            sw.pack(side="left")
            sw.pack_propagate(False)

            def _bind_swatch_watch(idx: int, v: tk.StringVar, swatch: tk.Frame) -> None:
                def _upd(_a: str = "", _b: str = "", _c: str = "") -> None:
                    name = v.get()
                    hx = GENRE_PALETTE_NAME_TO_HEX.get(name, DEFAULT_GENRE_COLORS[idx])
                    try:
                        swatch.configure(bg=hx)
                    except tk.TclError:
                        pass

                v.trace_add("write", _upd)
                _upd()

            _bind_swatch_watch(i, var_c, sw)

        def reset_form_to_defaults() -> None:
            for idx in range(9):
                ent = entries[idx]
                ent.delete(0, tk.END)
                ent.insert(0, DEFAULT_GENRE_LABELS[idx])
                folder_entries[idx].delete(0, tk.END)
                folder_entries[idx].insert(0, safe_genre_folder_name(DEFAULT_GENRE_LABELS[idx]))
                color_vars[idx].set(_palette_name_for_hex(DEFAULT_GENRE_COLORS[idx]))

        def save_and_close() -> None:
            new_labels: list[str] = []
            new_colors: list[str] = []
            new_folders: list[str] = []
            for j, ent in enumerate(entries):
                t = ent.get().strip()
                new_labels.append(t if t else DEFAULT_GENRE_LABELS[j])
                name = color_vars[j].get()
                new_colors.append(GENRE_PALETTE_NAME_TO_HEX.get(name, DEFAULT_GENRE_COLORS[j]))
                rawf = folder_entries[j].get().strip()
                new_folders.append(rawf if rawf else safe_genre_folder_name(new_labels[j]))
            gid = str(self._mode_preset_store.get("active_grid_preset_id") or "")
            gp = preset_by_id(self._mode_preset_store, gid) or {}
            blob = _coerce_grid_preset_blob(
                {
                    "id": gid,
                    "name": str(gp.get("name", "Grid")),
                    "mode": "grid",
                    "labels": new_labels[:9],
                    "colors": new_colors[:9],
                    "folders": new_folders[:9],
                },
            )
            if blob:
                self._upsert_preset_in_store(blob)
            self._apply_track_labels_from_preset_store()
            if self._music_root is not None:
                ensure_genre_folders(self._music_root, self._genre_folder_names)
            self._sync_genre_tile_titles()
            self._apply_genre_tile_colors()
            self._refresh_stats()
            top.destroy()
            self.root.after(10, self._refresh_all_widget_chrome)

        btn_row = tk.Frame(top, bg=self.bg)
        btn_row.pack(pady=(12, 14))
        br = tk.Button(
            btn_row,
            text="Reset to defaults",
            command=reset_form_to_defaults,
            font=("Helvetica", 11, "bold"),
            padx=14,
            pady=6,
        )
        self._style_tk_button_transport_grey(br)
        br.pack(side="left", padx=(0, 8))
        bs = tk.Button(btn_row, text="Save", command=save_and_close, font=("Helvetica", 11, "bold"), padx=16, pady=6)
        bc = tk.Button(btn_row, text="Cancel", command=top.destroy, font=("Helvetica", 11, "bold"), padx=16, pady=6)
        self._style_tk_button_accent(bs)
        self._style_tk_button_transport_grey(bc)
        bs.pack(side="left", padx=6)
        bc.pack(side="left", padx=6)

        top.minsize(880, 520)
        top.update_idletasks()
        tw = max(880, top.winfo_reqwidth())
        th = max(520, top.winfo_reqheight())
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
        x = max(0, (sw - tw) // 2)
        y = max(0, (sh - th) // 2)
        top.geometry(f"{tw}x{th}+{x}+{y}")

    def _skip_flash_widget(self) -> Optional[tk.Misc]:
        return getattr(self, "btn_skip", None)

    def _undo_flash_widget(self) -> Optional[tk.Misc]:
        return getattr(self, "btn_undo", None)

    def _kbd_skip(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self.skip_current()
            self._flash_control(self._skip_flash_widget())
            return "break"

        return handler

    def _kbd_undo(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self.undo_last()
            self._flash_control(self._undo_flash_widget())
            return "break"

        return handler

    def _kbd_undo_all(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self.undo_all_to_session_start()
            self._flash_control(self._undo_flash_widget())
            return "break"

        return handler

    def _kbd_cleanup_sort(self, keysym: str, bucket: str, flash: tk.Misc) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._sorting_mode != "swipe":
                return "break"
            if self._suppress_key_repeat(keysym):
                return "break"
            self.sort_current(bucket)
            self._flash_control(flash)
            return "break"

        return handler

    def _dispatch_digit_key(self, digit_1_to_9: int, _e: object) -> Optional[str]:
        if not self._shortcuts_active():
            return None
        if not self._tiles_enabled:
            return "break"
        if self._sorting_mode == "grid":
            self._tinder_kb_jump_error_digit = None
            gsym = f"GenreKey-{digit_1_to_9}"
            if self._suppress_key_repeat(gsym):
                return "break"
            idx = digit_1_to_9 - 1
            if 0 <= idx < len(self._genre_tile_inners):
                self.sort_current_genre(idx)
                self._flash_control(self._genre_tile_inners[idx])
            return "break"
        if digit_1_to_9 == 9:
            self._tinder_kb_jump_error_digit = None
            if self._suppress_key_repeat("9"):
                return "break"
            self.jump_to_outro()
            self._flash_control(self._jump_btn_outro)
            return "break"
        sec_map = {1: 0, 2: 30, 3: 60, 4: 90, 5: 120, 6: 180, 7: 240, 8: 300}
        sec = sec_map[digit_1_to_9]
        if self._tinder_jump_target_beyond_track(float(sec)):
            btn = self._preset_jump_buttons.get(sec)
            self._style_jump_label_error_hold(btn)
            self._tinder_kb_jump_error_digit = digit_1_to_9
            return "break"
        self._tinder_kb_jump_error_digit = None
        if self._suppress_key_repeat(f"Key-{digit_1_to_9}"):
            return "break"
        btn = self._preset_jump_buttons.get(sec)
        self.jump_to(float(sec), jump_ui_key=sec)
        self._flash_control(btn)
        return "break"

    def _build_ui(self) -> None:
        self._body = tk.Frame(self.root, bg=self.bg)
        self._body.pack(fill="both", expand=True)

        self._main_scroll_outer = tk.Frame(self._body, bg=self.bg)
        self._main_scroll_outer.pack(side="left", fill="both", expand=True)
        # Main column + bottom dock: grid keeps the dock from overlapping the content area.
        self._main_scroll_outer.grid_columnconfigure(0, weight=1)
        self._main_scroll_outer.grid_rowconfigure(0, weight=1)

        self._main_panel = tk.Frame(self._main_scroll_outer, bg=self.bg)
        self._main_panel.grid(row=0, column=0, sticky="nsew")

        self._bottom_dock = tk.Frame(self._main_scroll_outer, bg=self.bg)
        self._bottom_dock.grid(row=1, column=0, sticky="ew")

        self._main_column = tk.Frame(self._main_panel, bg=self.bg)
        self._main_column.pack(fill="both", expand=True)
        self._main_column.columnconfigure(0, weight=1)
        self._main_column.rowconfigure(0, weight=1)
        self._main_column.rowconfigure(1, weight=0, minsize=_ACTION_ZONE_FIXED_HEIGHT_PX)
        self._main_upper = tk.Frame(self._main_column, bg=self.bg)
        self._main_upper.grid(row=0, column=0, sticky="nsew")
        self._main_action_shell = tk.Frame(
            self._main_column,
            bg=self.bg,
            height=_ACTION_ZONE_FIXED_HEIGHT_PX,
        )
        self._main_action_shell.grid(row=1, column=0, sticky="nsew")
        self._main_action_shell.grid_propagate(False)
        self._action_swap_host = tk.Frame(self._main_action_shell, bg=self.bg)
        self._action_swap_host.pack(fill="both", expand=True)

        top = tk.Frame(self._main_upper, bg=self.bg)
        top.pack(fill="x", padx=_CONTENT_GUTTER, pady=(10, 4))
        # Equal side columns keep the title in the true horizontal center while resizing.
        top.columnconfigure(0, weight=1, uniform="top_hdr")
        top.columnconfigure(1, weight=0)
        top.columnconfigure(2, weight=1, uniform="top_hdr")

        top_left_col = tk.Frame(top, bg=self.bg)
        top_left_col.grid(row=0, column=0, sticky="nw")

        hdr_util = tk.Frame(top_left_col, bg=self.bg)
        hdr_util.pack(anchor="w", fill="x")
        for hu in range(2):
            hdr_util.columnconfigure(hu, weight=1, uniform="hdr_util_col")
        _o_gd, self._tile_guide_inner = self._action_tile(
            hdr_util,
            "📖",
            "Guide",
            "[H]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self._toggle_guide_panel_visibility,
            clickable_when_tiles_disabled=True,
        )
        _o_st, self._tile_settings_inner = self._action_tile(
            hdr_util,
            "⚙️",
            "Settings",
            "[,]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self._open_settings_dialog,
            clickable_when_tiles_disabled=True,
        )
        _o_gd.grid(row=0, column=0, sticky="nsew", padx=(0, _ACTION_TILE_GRID_GAP_PX))
        _o_st.grid(row=0, column=1, sticky="nsew")

        load_row = tk.Frame(top_left_col, bg=self.bg)
        load_row.pack(anchor="w", fill="x", pady=(_SECTION_GAP_Y, 0))
        self._load_folder_ring, self.btn_load = self._load_folder_button(load_row, self.pick_folder)
        self._load_folder_ring.pack(side="left", fill="x", expand=True)

        def_row = tk.Frame(top_left_col, bg=self.bg)
        def_row.pack(anchor="w", fill="x", pady=(8, 0))
        self.btn_load_default_folder = tk.Button(
            def_row,
            text="Load Default Folder",
            command=self._on_load_default_folder,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(self.btn_load_default_folder)
        self.btn_load_default_folder.pack(side="left", fill="x", expand=True)

        self.lbl_default_folder_path = tk.Label(
            top_left_col,
            text="",
            bg=self.bg,
            fg=self.muted,
            font=("Helvetica", 9),
            anchor="w",
            justify="left",
        )
        self.lbl_default_folder_path.pack(anchor="w", pady=(4, 0), fill="x")
        self._refresh_default_folder_label()

        self._hidden_chrome_host = tk.Frame(self.root, bg=self.bg)
        o_rnd, self._tile_order_random_inner = self._action_tile(
            self._hidden_chrome_host,
            "🔀",
            "Random",
            "[U]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._on_pick_track_order("random"),
            clickable_when_tiles_disabled=True,
        )
        o_io, self._tile_order_in_order_inner = self._action_tile(
            self._hidden_chrome_host,
            "📋",
            "In order",
            "[O]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._on_pick_track_order("in_order"),
            clickable_when_tiles_disabled=True,
        )
        o_rnd.pack(side="left", padx=(0, _ACTION_TILE_GRID_GAP_PX), fill="x", expand=True)
        o_io.pack(side="left", fill="x", expand=True)
        self._apply_order_toggle_chrome()

        title_mid = tk.Frame(top, bg=self.bg)
        title_mid.grid(row=0, column=1, sticky="")
        self.lbl_mode_header = tk.Label(
            title_mid,
            text=self._mode_status_text(self._sorting_mode),
            bg=self.bg,
            fg=self._mode_status_fg(self._sorting_mode),
            font=("Helvetica", 11, "bold"),
            anchor="center",
            justify="center",
        )
        self.lbl_mode_header.pack(pady=(0, 2))
        titles = tk.Frame(title_mid, bg=self.bg)
        titles.pack()
        tk.Label(titles, text="BAKKIS", bg=self.bg, fg=self.fg, font=("Helvetica", 20, "bold")).pack(side="left")
        tk.Label(titles, text=" ", bg=self.bg, font=("Helvetica", 5)).pack(side="left")
        tk.Label(titles, text="MUSIC SORTER", bg=self.bg, fg=self.accent, font=("Helvetica", 20, "bold")).pack(side="left")

        top_right_col = tk.Frame(top, bg=self.bg)
        top_right_col.grid(row=0, column=2, sticky="ne")
        right_stack = tk.Frame(top_right_col, bg=self.bg)
        right_stack.pack(anchor="ne", fill="x")

        queue_row = tk.Frame(right_stack, bg=self.bg)
        queue_row.pack(fill="x", anchor="e")
        _o_q, self._tile_queue_inner = self._action_tile(
            queue_row,
            "☰",
            "Queue",
            "[Q]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self._toggle_queue_panel_visibility,
            clickable_when_tiles_disabled=True,
        )
        _o_q.pack(fill="x", expand=True, anchor="e")

        mode_outer = tk.Frame(right_stack, bg=self.bg)
        mode_outer.pack(anchor="e", fill="x", pady=(_SECTION_GAP_Y, 0))
        mode_tiles = tk.Frame(mode_outer, bg=self.bg)
        mode_tiles.pack(anchor="e", fill="x")
        for mc in range(2):
            mode_tiles.columnconfigure(mc, weight=1, uniform="mode_pick_col")
        _o_mc, self._tile_mode_cleanup_inner = self._action_tile(
            mode_tiles,
            "💘",
            "Swipe",
            "[S]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._set_sorting_mode("swipe"),
            clickable_when_tiles_disabled=True,
        )
        _o_mg, self._tile_mode_genre_inner = self._action_tile(
            mode_tiles,
            "🎵",
            "Grid",
            "[G]",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._set_sorting_mode("grid"),
            clickable_when_tiles_disabled=True,
        )
        _o_mc.grid(row=0, column=0, sticky="nsew", padx=(0, _ACTION_TILE_GRID_GAP_PX))
        _o_mg.grid(row=0, column=1, sticky="nsew")

        player_card = tk.Frame(
            self._main_upper,
            bg=self.bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
        )
        player_card.pack(fill="x", padx=_CONTENT_GUTTER, pady=(_SECTION_GAP_Y_TIGHT, _SECTION_GAP_Y))
        card_inner = tk.Frame(player_card, bg=self.bg)
        card_inner.pack(fill="x", padx=12, pady=(10, 10))

        status_above_progress = tk.Frame(card_inner, bg=self.bg)
        status_above_progress.pack(fill="x", pady=(0, 2))
        self.lbl_stats = tk.Label(
            status_above_progress,
            text="",
            bg=self.bg,
            fg=self.muted,
            font=("Helvetica", 10),
            justify="center",
            anchor="center",
            wraplength=0,
        )
        self.lbl_stats.pack(fill="x")

        self.lbl_track = self._readonly_time_label(
            card_inner,
            text="",
            font=("Helvetica", 12, "bold"),
            width=None,
            anchor="center",
        )
        self.lbl_track.pack(fill="x", pady=(2, 0))

        self.lbl_now_playing_tag = tk.Label(
            card_inner,
            text="",
            bg=self.bg,
            fg=self.accent,
            font=("Helvetica", 9),
            wraplength=1100,
            justify="center",
            anchor="center",
        )

        self._now_playing_main_wrap = tk.Frame(card_inner, bg=self.bg)
        self._now_playing_main_wrap.pack(fill="x", pady=(2, 4))
        tk.Frame(self._now_playing_main_wrap, bg=self.bg).pack(side="left", fill="x", expand=True)
        self._now_playing_main_inner = tk.Frame(self._now_playing_main_wrap, bg=self.bg)
        self._now_playing_main_inner.pack(side="left")
        tk.Frame(self._now_playing_main_wrap, bg=self.bg).pack(side="left", fill="x", expand=True)

        self._set_now_playing_main_plain("Load a folder to begin")

        progress_row = tk.Frame(card_inner, bg=self.bg)
        progress_row.pack(fill="x", pady=(0, 2))

        self.lbl_elapsed = self._readonly_time_label(
            progress_row,
            text="0:00",
            font=("Helvetica", 13, "bold"),
            width=7,
            anchor="e",
        )
        self.lbl_elapsed.pack(side="left", padx=(0, 12))

        self.progress_canvas = tk.Canvas(
            progress_row,
            height=28,
            bg=self.trough,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            bd=0,
        )
        self.progress_canvas.pack(side="left", fill="x", expand=True)
        self.progress_canvas.bind("<Button-1>", self._on_progress_click)
        self.progress_canvas.bind("<Configure>", self._on_progress_canvas_configure)

        self.lbl_total = self._readonly_time_label(
            progress_row,
            text="0:00",
            font=("Helvetica", 13, "bold"),
            width=7,
            anchor="w",
        )
        self.lbl_total.pack(side="left", padx=(12, 0))

        self.root.after_idle(self._raise_time_text_above_scrubber)

        paths_strip = tk.Frame(card_inner, bg=self.bg)
        paths_strip.pack(fill="x", pady=(4, 0))
        self.lbl_source_folder = tk.Label(
            paths_strip,
            text="",
            bg=self.bg,
            fg=self.muted,
            wraplength=1100,
            justify="center",
            font=("Helvetica", 10),
            anchor="center",
        )
        self.lbl_source_folder.pack(fill="x", pady=(0, 2))
        self.lbl_path = tk.Label(
            paths_strip,
            text="",
            bg=self.bg,
            fg=self.muted,
            wraplength=1100,
            justify="center",
            font=("Helvetica", 10),
        )
        self.lbl_path.pack(fill="x", pady=(0, 0))

        undo_sc = "[⌘Z]" if sys.platform == "darwin" else "[Z]"

        transport_bar = tk.Frame(self._main_upper, bg=self.bg)
        transport_bar.pack(fill="x", padx=_CONTENT_GUTTER, pady=(_SECTION_GAP_Y_TIGHT, _SECTION_GAP_Y_TIGHT))
        transport_bar.rowconfigure(0, minsize=72)
        u_uniform = "transport_util"
        for col in range(4):
            transport_bar.columnconfigure(col, weight=1, uniform=u_uniform)
        u_gap = 10

        def _place_transport_cell(col: int, outer: tk.Frame) -> None:
            px = (0, u_gap) if col < 3 else (0, 0)
            outer.grid(row=0, column=col, sticky="nsew", padx=px)

        _o_play, _ = self._build_transport_utility_cell(
            transport_bar,
            "▶",
            "Play",
            "[Space]",
            self.toggle_play,
            role="play",
        )
        self.btn_play = _o_play
        self._transport_play_icon_label = getattr(self.btn_play, "_utility_icon", None)
        _place_transport_cell(0, _o_play)

        _o_rep, _ = self._build_transport_utility_cell(
            transport_bar,
            "↻",
            "Replay",
            "[R]",
            self._replay_active_jump_point,
            role="replay",
        )
        self.btn_replay = _o_rep
        _place_transport_cell(1, _o_rep)

        _o_sk, _skip_inner = self._build_transport_utility_cell(
            transport_bar,
            "⏭",
            "Skip",
            "[Enter]",
            self.skip_current,
            role="skip",
        )
        self.btn_skip = _o_sk
        self._swipe_skip_inner = _skip_inner
        self._grid_skip_inner = _skip_inner
        _place_transport_cell(2, _o_sk)

        _o_un, _undo_inner = self._build_transport_utility_cell(
            transport_bar,
            "↶",
            "Undo",
            undo_sc,
            self.undo_last,
            role="undo",
            clickable_when_tiles_disabled=True,
        )
        self.btn_undo = _o_un
        self._swipe_undo_inner = _undo_inner
        self._grid_undo_inner = _undo_inner
        _place_transport_cell(3, _o_un)

        jump = tk.Frame(self._main_upper, bg=self.bg)
        jump.pack(fill="x", padx=_CONTENT_GUTTER, pady=(_SECTION_GAP_Y_TIGHT, _SECTION_GAP_Y_TIGHT))
        jl = tk.Label(jump, text="JUMP TO", bg=self.bg, fg=self.accent, font=("Helvetica Neue", 9, "bold"))
        jl.pack(anchor="w", pady=(0, 3))

        row1 = tk.Frame(jump, bg=self.bg)
        row1.pack(fill="x")
        self._jump_btn_pb_start = self._jump_time_button(
            row1,
            "START\n0:00",
            self._jump_to_playback_start_slot,
            hint_suffix=None,
            width=0,
            tinder_target_sec=None,
        )
        self._jump_buttons.append(self._jump_btn_pb_start)
        self._jump_btn_pb_start.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self._sync_pb_start_jump_button_label()

        for base, hint, sec in [
            ("0:00", "  1", 0),
            ("0:30", "  2", 30),
            ("1:00", "  3", 60),
            ("1:30", "  4", 90),
            ("2:00", "  5", 120),
        ]:
            b = self._jump_time_button(
                row1,
                base,
                lambda s=sec: self.jump_to(float(s), jump_ui_key=s),
                hint_suffix=hint,
                width=0,
                tinder_target_sec=float(sec),
            )
            self._jump_buttons.append(b)
            self._preset_jump_buttons[sec] = b
            b.pack(side="left", padx=(0, 4), fill="x", expand=True)

        row2_presets = tk.Frame(jump, bg=self.bg)
        row2_presets.pack(fill="x", pady=(4, 0))
        for base, hint, sec in [
            ("3:00", "  6", 180),
            ("4:00", "  7", 240),
            ("5:00", "  8", 300),
        ]:
            b = self._jump_time_button(
                row2_presets,
                base,
                lambda s=sec: self.jump_to(float(s), jump_ui_key=s),
                hint_suffix=hint,
                width=0,
                tinder_target_sec=float(sec),
            )
            self._jump_buttons.append(b)
            self._preset_jump_buttons[sec] = b
            b.pack(side="left", padx=(0, 4), fill="x", expand=True)
        b_out = self._jump_time_button(row2_presets, "OUTRO", self.jump_to_outro, hint_suffix="  9", width=0)
        self._jump_buttons.append(b_out)
        self._jump_btn_outro = b_out
        b_out.pack(side="left", padx=(0, 0), fill="x", expand=True)

        row2 = tk.Frame(jump, bg=self.bg)
        row2.pack(fill="x", pady=(4, 0))
        b_m = self._jump_time_button(row2, "← −30s", lambda: self.jump_delta(-30), hint_suffix="  A", width=16)
        self._jump_buttons.append(b_m)
        self._jump_btn_minus = b_m
        b_m.pack(side="left", padx=(0, 4), fill="x", expand=True)
        b_p = self._jump_time_button(
            row2,
            "+30s →",
            lambda: self.jump_delta(30),
            hint_suffix="  D",
            width=16,
            tinder_delta_plus=True,
        )
        self._jump_buttons.append(b_p)
        self._jump_btn_plus = b_p
        b_p.pack(side="left", fill="x", expand=True)

        self.lbl_seek_hint = tk.Label(
            jump,
            text="",
            bg=self.bg,
            fg=self.warning,
            font=("Helvetica Neue", 10),
        )
        self.lbl_seek_hint.pack(anchor="w", pady=(4, 0))

        self._apply_mode_switch_chrome()

        self.cleanup_outer = tk.Frame(self._action_swap_host, bg=self.bg)
        self.cleanup_outer.pack(fill="both", expand=True, padx=_CONTENT_GUTTER, pady=(4, 8))
        self.cleanup_actions = tk.Frame(self.cleanup_outer, bg=self.bg)
        self.cleanup_actions.pack(fill="both", expand=True)

        tinder_sort_row = tk.Frame(self.cleanup_actions, bg=self.bg)
        tinder_sort_row.pack(fill="both", expand=True)
        for tc in range(3):
            tinder_sort_row.columnconfigure(tc, weight=1, uniform="tinder_sort_col")
        tinder_sort_row.rowconfigure(0, weight=1)
        _sgp = _SWIPE_SORT_GRID_PAD
        _swipe_icon_font = ("Helvetica Neue", 36)
        _swipe_title_font = ("Helvetica Neue", 15, "bold")
        _swipe_hint_font = ("Menlo", 9)

        o_k, self._tile_keep_inner = self._action_tile(
            tinder_sort_row,
            "😊",
            "KEEP",
            "[1]  Swipe →",
            self.accent,
            self.accent_active,
            self.teal_on_fg,
            "#1B3B38",
            lambda: self.sort_current("keep"),
            icon_font=_swipe_icon_font,
            title_font=_swipe_title_font,
            shortcut_font=_swipe_hint_font,
            row_gap=5,
        )
        o_k.grid(row=0, column=0, sticky="nsew", padx=_sgp, pady=_sgp)
        o_a, self._tile_archive_inner = self._action_tile(
            tinder_sort_row,
            "☹️",
            "ARCHIVE",
            "[2]  Swipe ↑",
            self.archive_bg,
            self.archive_active,
            self.archive_fg,
            "#3B1B1B",
            lambda: self.sort_current("archive"),
            icon_font=_swipe_icon_font,
            title_font=_swipe_title_font,
            shortcut_font=_swipe_hint_font,
            row_gap=5,
        )
        o_a.grid(row=0, column=1, sticky="nsew", padx=_sgp, pady=_sgp)
        o_f, self._tile_favorite_inner = self._action_tile(
            tinder_sort_row,
            "🤩",
            "FAVORITE",
            "[3]  Swipe ←",
            self.favorite_bg,
            self.favorite_active,
            self.favorite_on_fg,
            "#3B3818",
            lambda: self.sort_current("favorites"),
            icon_font=_swipe_icon_font,
            title_font=_swipe_title_font,
            shortcut_font=_swipe_hint_font,
            row_gap=5,
        )
        o_f.grid(row=0, column=2, sticky="nsew", padx=_sgp, pady=_sgp)

        # Aliases for keyboard flash (tile inner frames)
        self.btn_keep = self._tile_keep_inner  # type: ignore[assignment]
        self.btn_archive = self._tile_archive_inner  # type: ignore[assignment]
        self.btn_favorite = self._tile_favorite_inner  # type: ignore[assignment]

        self.genre_actions = tk.Frame(self._action_swap_host, bg=self.bg)
        genre_grid = tk.Frame(self.genre_actions, bg=self.bg)
        genre_grid.pack(fill="both", expand=True)
        for gr in range(3):
            genre_grid.rowconfigure(gr, weight=1, uniform="genre_row")
        for gc in range(3):
            genre_grid.columnconfigure(gc, weight=1, uniform="genre_col")
        self._genre_tile_inners.clear()
        self._genre_title_labels.clear()
        for i in range(9):
            digit = str(i + 1)
            row, col = divmod(i, 3)
            name = self._genre_labels[i]
            hx = self._genre_colors[i] if i < len(self._genre_colors) else DEFAULT_GENRE_COLORS[i]
            act = self._genre_tile_active_bg(hx)
            fg_m = _fg_for_genre_bg(hx)
            sc_m = _genre_shortcut_fg(fg_m)
            o_g, inn_g = self._genre_action_tile(
                genre_grid,
                name,
                digit,
                hx,
                act,
                fg_m,
                sc_m,
                lambda idx=i: self.sort_current_genre(idx),
            )
            o_g.grid(row=row, column=col, sticky="nsew", padx=_ACTION_TILE_GRID_GAP_PX, pady=_ACTION_TILE_GRID_GAP_PX)
            name_lab = getattr(inn_g, "_genre_name_label", None)
            if isinstance(name_lab, tk.Label):
                self._genre_title_labels.append(name_lab)
            self._genre_tile_inners.append(inn_g)
        self._apply_genre_tile_colors()

        grid_kaf_row = tk.Frame(self.genre_actions, bg=self.bg)
        grid_kaf_row.pack(fill="x", pady=(8, 2))
        for gkc in range(3):
            grid_kaf_row.columnconfigure(gkc, weight=1, uniform="grid_kaf_col")
        _gk = _ACTION_TILE_GRID_GAP_PX
        o_gkk, self._grid_kaf_keep_inner = self._action_tile(
            grid_kaf_row,
            "😊",
            "KEEP",
            "[→]",
            self.accent,
            self.accent_active,
            self.teal_on_fg,
            "#1A1A1A",
            lambda: self.sort_current("keep"),
        )
        o_gkk.grid(row=0, column=0, sticky="nsew", padx=(0, _gk), pady=0)
        o_gka, self._grid_kaf_archive_inner = self._action_tile(
            grid_kaf_row,
            "☹️",
            "ARCHIVE",
            "[↑]",
            self.archive_bg,
            self.archive_active,
            self.archive_fg,
            "#1A1A1A",
            lambda: self.sort_current("archive"),
        )
        o_gka.grid(row=0, column=1, sticky="nsew", padx=(0, _gk), pady=0)
        o_gkf, self._grid_kaf_favorite_inner = self._action_tile(
            grid_kaf_row,
            "🤩",
            "FAVORITE",
            "[←]",
            self.favorite_bg,
            self.favorite_active,
            self.favorite_on_fg,
            "#1A1A1A",
            lambda: self.sort_current("favorites"),
        )
        o_gkf.grid(row=0, column=2, sticky="nsew", padx=(0, 0), pady=0)

        tk.Label(
            self.genre_actions,
            text="Edit genres in Settings",
            bg=self.bg,
            fg=self.muted,
            font=("Helvetica", 9),
            anchor="center",
        ).pack(fill="x", pady=(4, 0))

        self.genre_actions.pack_forget()
        if self._sorting_mode == "grid":
            self.cleanup_outer.pack_forget()
            self.genre_actions.pack(fill="both", expand=True, padx=_CONTENT_GUTTER, pady=(4, 8))

        self._build_queue_sidebar()
        self._build_guide_sidebar()

        self.footer_outer = tk.Frame(self._bottom_dock, bg=self.bg)
        self.footer_outer.pack(fill="x", padx=0, pady=(0, 6))
        self._build_branding_footer(self.footer_outer)

        self._apply_guide_panel_visibility(self._shortcut_panel_visible)
        self._sync_queue_tile_title()

    def _queue_ellipsis(self, text: str, max_chars: int = 36) -> str:
        s = text.strip()
        if len(s) <= max_chars:
            return s
        if max_chars <= 1:
            return "…"
        return s[: max_chars - 1].rstrip() + "…"

    def _describe_undo_for_recent_panel(self, item: dict) -> str:
        kind = item.get("kind")
        if kind == "skip":
            raw = item.get("path")
            p = raw if isinstance(raw, Path) else Path(str(raw))
            return f"↷ Skip · {self._queue_ellipsis(p.name)}"
        if kind == "move":
            raw = item.get("from")
            p = raw if isinstance(raw, Path) else Path(str(raw))
            name = self._queue_ellipsis(p.name)
            sb = item.get("session_bucket")
            if sb == "keep":
                return f"✓ Keep · {name}"
            if sb == "archive":
                return f"✕ Archive · {name}"
            if sb == "favorites":
                return f"★ Favorite · {name}"
            if sb in ("genre", "grid"):
                g = self._queue_ellipsis(p.parent.name if p.parent else "")
                return f"{g} · {name}"
            return name
        return ""

    def _refresh_queue_panel(self) -> None:
        if self._lbl_queue_now is None:
            return
        title = self._now_playing_summary_text.strip()
        now_t = self._queue_ellipsis(title, 42) if title else "—"
        try:
            self._lbl_queue_now.configure(text=now_t)
        except tk.TclError:
            pass

        q = self._queue
        for i, lb in enumerate(self._lbl_queue_up):
            try:
                slot = i + 1
                if slot < len(q):
                    line = f"{i + 1}. {self._queue_ellipsis(q[slot].name)}"
                    lb.configure(text=line, fg=self.fg)
                else:
                    lb.configure(text="", fg=self.fg)
            except tk.TclError:
                pass

        chunk = self._undo_stack[-5:]
        recent_lines = list(reversed(chunk))
        for i, lb in enumerate(self._lbl_queue_recent):
            try:
                if i < len(recent_lines):
                    lb.configure(text=self._describe_undo_for_recent_panel(recent_lines[i]), fg=self.muted)
                else:
                    lb.configure(text="", fg=self.muted)
            except tk.TclError:
                pass

    def _sync_guide_tile_title(self) -> None:
        inner = self._tile_guide_inner
        if inner is None:
            return
        w = getattr(inner, "_tile_widgets", ())
        if len(w) >= 2:
            t = "Hide guide" if self._shortcut_panel_visible else "Guide"
            try:
                w[1].configure(text=t)
            except tk.TclError:
                pass

    def _sync_queue_tile_title(self) -> None:
        inner = self._tile_queue_inner
        if inner is None:
            return
        w = getattr(inner, "_tile_widgets", ())
        if len(w) >= 2:
            t = "Hide queue" if self._queue_panel_visible else "Queue"
            try:
                w[1].configure(text=t)
            except tk.TclError:
                pass

    def _apply_queue_panel_visibility(self, visible: bool) -> None:
        self._queue_panel_visible = visible
        self._sync_queue_tile_title()
        if self._queue_sidebar_outer is not None:
            try:
                if visible:
                    self._queue_sidebar_outer.pack(side="right", fill="y", padx=(0, 16), pady=(18, 24))
                else:
                    self._queue_sidebar_outer.pack_forget()
            except tk.TclError:
                pass
        self._refresh_queue_panel()
        self._refresh_session_stats()
        self._update_content_wraplengths()

    def _toggle_queue_panel_visibility(self) -> None:
        self._apply_queue_panel_visibility(not self._queue_panel_visible)

    def _kbd_toggle_queue_panel(self, _e: object) -> Optional[str]:
        if not self._shortcuts_active():
            return None
        self._toggle_queue_panel_visibility()
        if self._tile_queue_inner is not None:
            self._flash_tile_widget(self._tile_queue_inner)
        return "break"

    def _apply_guide_panel_visibility(self, visible: bool) -> None:
        self._shortcut_panel_visible = visible
        self._sync_guide_tile_title()
        if self._guide_sidebar_outer is not None:
            try:
                if visible:
                    if self._main_scroll_outer is not None:
                        self._guide_sidebar_outer.pack(
                            side="left",
                            before=self._main_scroll_outer,
                            fill="y",
                            padx=(16, 0),
                            pady=(18, 24),
                        )
                    else:
                        self._guide_sidebar_outer.pack(
                            side="left", fill="y", padx=(16, 0), pady=(18, 24)
                        )
                else:
                    self._guide_sidebar_outer.pack_forget()
            except tk.TclError:
                pass
        self._update_content_wraplengths()
        self._refresh_all_widget_chrome()

    def _toggle_guide_panel_visibility(self) -> None:
        self._apply_guide_panel_visibility(not self._shortcut_panel_visible)

    def _build_queue_sidebar(self) -> None:
        """Build the RIGHT sidebar (Queue + Session Stats). Initial visibility set by caller."""
        if self._body is None:
            return
        outer = tk.Frame(self._body, bg=self.bg, width=248)
        outer.pack_propagate(False)
        self._queue_sidebar_outer = outer

        inner = tk.Frame(outer, bg=self.panel, highlightthickness=1, highlightbackground=self.panel_border)
        inner.pack(fill="both", expand=True)
        self._queue_sidebar_inner = inner

        qf = tk.Frame(inner, bg=self.panel)
        qf.pack(fill="x")
        self._queue_section_frame = qf

        hdr_small = ("Helvetica", 8, "bold")
        tk.Label(qf, text="QUEUE", bg=self.panel, fg=self.accent, font=("Helvetica", 10, "bold"), anchor="w").pack(
            fill="x", padx=10, pady=(10, 8)
        )

        tk.Label(qf, text="NOW PLAYING", bg=self.panel, fg=self.muted, font=hdr_small, anchor="w").pack(
            fill="x", padx=10, pady=(0, 4)
        )
        now_box = tk.Frame(qf, bg=self.panel, highlightthickness=1, highlightbackground=self.accent)
        now_box.pack(fill="x", padx=10, pady=(0, 10))
        self._lbl_queue_now = tk.Label(
            now_box,
            text="—",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=198,
        )
        self._lbl_queue_now.pack(fill="x", padx=8, pady=8)

        tk.Label(qf, text="UP NEXT", bg=self.panel, fg=self.muted, font=hdr_small, anchor="w").pack(
            fill="x", padx=10, pady=(0, 4)
        )
        up_wrap = tk.Frame(qf, bg=self.panel)
        up_wrap.pack(fill="x", padx=10, pady=(0, 10))
        self._lbl_queue_up.clear()
        for _ in range(5):
            lb = tk.Label(
                up_wrap,
                text="",
                bg=self.panel,
                fg=self.fg,
                font=("Helvetica", 9),
                anchor="w",
                justify="left",
                wraplength=198,
            )
            lb.pack(fill="x", pady=(0, 3))
            self._lbl_queue_up.append(lb)

        tk.Frame(qf, height=1, bg=self.panel_border).pack(fill="x", padx=10, pady=(4, 8))

        tk.Label(qf, text="RECENTLY SORTED", bg=self.panel, fg=self.muted, font=hdr_small, anchor="w").pack(
            fill="x", padx=10, pady=(0, 4)
        )
        rec_wrap = tk.Frame(qf, bg=self.panel)
        rec_wrap.pack(fill="x", padx=10, pady=(0, 12))
        self._lbl_queue_recent.clear()
        for _ in range(5):
            lb = tk.Label(
                rec_wrap,
                text="",
                bg=self.panel,
                fg=self.muted,
                font=("Helvetica", 9),
                anchor="w",
                justify="left",
                wraplength=198,
            )
            lb.pack(fill="x", pady=(0, 4))
            self._lbl_queue_recent.append(lb)

        self._sidebar_spring = tk.Frame(inner, bg=self.panel)
        self._sidebar_spring.pack(fill="both", expand=True)
        self.lbl_session_stats = tk.Label(
            inner,
            text="",
            bg=self.panel,
            fg=self.muted,
            font=("Helvetica", 9),
            justify="left",
            anchor="w",
            wraplength=_GUIDE_SIDEBAR_TEXT_WRAP,
        )
        self.lbl_session_stats.pack(fill="x", padx=8, pady=(4, 10))

        self._refresh_queue_panel()

    def _build_guide_sidebar(self) -> None:
        """Build the LEFT sidebar (collapsible Help Guide). Initial visibility set by caller."""
        if self._body is None:
            return
        outer = tk.Frame(self._body, bg=self.bg, width=248)
        outer.pack_propagate(False)
        self._guide_sidebar_outer = outer

        inner = tk.Frame(outer, bg=self.bg)
        inner.pack(fill="both", expand=True)
        self._guide_sidebar_inner = inner

        self.shortcut_frame = self._build_shortcut_guide_panel(inner)
        self.shortcut_frame.pack(fill="both", expand=True)

    def _build_shortcut_guide_panel(self, parent: tk.Misc) -> tk.Frame:
        outer = tk.Frame(parent, bg=self.panel, highlightthickness=1, highlightbackground=self.panel_border)
        inner_pad = tk.Frame(outer, bg=self.panel)
        inner_pad.pack(fill="both", expand=True, padx=10, pady=8)
        hdr = ("Helvetica", 11, "bold")
        body = ("Helvetica", 9)
        meta = ("Helvetica", 8)
        stack = tk.Frame(inner_pad, bg=self.panel)
        stack.pack(fill="x")
        wl = _GUIDE_SIDEBAR_TEXT_WRAP
        self._guide_swipe_line_labels.clear()
        swipe_f = tk.Frame(stack, bg=self.panel)
        swipe_f.pack(fill="x", pady=(0, 10))
        tk.Label(swipe_f, text="▶ SWIPE", bg=self.panel, fg=self.accent, font=hdr, anchor="w").pack(anchor="w")
        swipe_intro = [
            f"{self._swipe_labels[0]} (→)",
            f"{self._swipe_labels[1]} (←)",
            f"{self._swipe_labels[2]} (↑)",
            "Enter  Skip",
            "Z  Undo last step",
            "⌃Z  Undo all (back to folder load)",
        ]
        for line in swipe_intro:
            lb = tk.Label(
                swipe_f,
                text=line,
                bg=self.panel,
                fg=self.fg,
                font=body,
                anchor="w",
                justify="left",
                wraplength=wl,
            )
            lb.pack(anchor="w", pady=(2, 0), fill="x")
            self._guide_swipe_line_labels.append(lb)
        specs = [
            (
                "♪ PLAYBACK",
                [
                    "Space  Play / Pause",
                    "↓  Replay from playback start",
                    "R  Replay active jump (preset / Outro / default start)",
                    "Q  Show / hide queue list",
                ],
            ),
            (
                "⏱ JUMP TO",
                [
                    "Swipe mode: 1–9 jump",
                    "1–5  → 0:00 … 2:00",
                    "6–8  → 3:00 … 5:00",
                    "9  → Outro (last 60s)",
                    "5  = 2:00 (long-track default)",
                    "Grid mode: use mouse here",
                ],
            ),
            (
                "↔ SCRUB",
                [
                    "A  −30 seconds",
                    "D  +30 seconds",
                ],
            ),
            (
                "◆ MODE",
                [
                    "Center: Swipe / Grid status above the title",
                    "Header left: Guide · Settings · load folder rows",
                    "Header right: Queue · Swipe · Grid tiles",
                    "H  Guide · Q  Queue",
                    "S or T  Swipe · G  Grid",
                    "U  Random order · O  In order (also in Settings → Default track order)",
                    "1–9  Sort to grid folder (grid mode)",
                    "Enter  Skip · Z  Undo",
                    "⌃Z  Undo all",
                    "Default start time: Settings — active jump tile shows START",
                    "Edit genres in Settings → Genre settings (labels, colors, folders)",
                ],
            ),
        ]
        for _, (title, lines) in enumerate(specs):
            col_f = tk.Frame(stack, bg=self.panel)
            col_f.pack(fill="x", pady=(0, 10))
            tk.Label(col_f, text=title, bg=self.panel, fg=self.accent, font=hdr, anchor="w").pack(anchor="w")
            for line in lines:
                tk.Label(
                    col_f,
                    text=line,
                    bg=self.panel,
                    fg=self.fg,
                    font=body,
                    anchor="w",
                    justify="left",
                    wraplength=wl,
                ).pack(anchor="w", pady=(2, 0), fill="x")
        tk.Label(
            inner_pad,
            text="Press H to hide or show this guide",
            bg=self.panel,
            fg=self.muted,
            font=meta,
            wraplength=wl,
        ).pack(anchor="w", pady=(4, 0))
        return outer

    def _build_branding_footer(self, parent: tk.Misc) -> None:
        footer = tk.Frame(parent, bg=self.bg, highlightthickness=0, cursor="")
        footer.pack(fill="x", padx=_CONTENT_GUTTER, pady=(4, 8))

        rule = tk.Frame(footer, height=1, bg=self.panel_border)
        rule.pack(fill="x", pady=(6, 8))

        logo_path = _bundle_dir() / "bakkislogo.png"
        if logo_path.is_file():
            try:
                from PIL import Image, ImageTk

                img = Image.open(logo_path).convert("RGBA")
                h = max(22, min(34, img.height))
                w = max(1, int(img.width * (h / float(img.height))))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                img = img.resize((w, h), resample)
                self._footer_logo_photo = ImageTk.PhotoImage(img)
                tk.Label(
                    footer,
                    image=self._footer_logo_photo,
                    bg=self.bg,
                    highlightthickness=0,
                    cursor="",
                ).pack(pady=(0, 4))
            except Exception:
                self._footer_logo_photo = None

        tk.Label(
            footer,
            text="BAKKIS MUSIC SORTER",
            bg=self.bg,
            fg=self.muted,
            font=("Helvetica", 10),
            highlightthickness=0,
            cursor="",
        ).pack(side="bottom", pady=(0, 6))

    def _shortcuts_active(self) -> bool:
        """Ignore shortcuts while a system dialog or other toplevel has focus."""
        try:
            w = self.root.focus_get()
            if w is None:
                return True
            return str(w.winfo_toplevel()) == str(self.root)
        except tk.TclError:
            return False

    def _suppress_key_repeat(self, keysym: str) -> bool:
        now = time.monotonic()
        # Allow rapid Z / Shift+Z so multi-step undo is not eaten by the 85 ms gate.
        if keysym in ("z", "Z"):
            self._last_shortcut_keysym = keysym
            self._last_shortcut_time = now
            return False
        if keysym == self._last_shortcut_keysym and (now - self._last_shortcut_time) < self._key_repeat_suppress_sec:
            return True
        self._last_shortcut_keysym = keysym
        self._last_shortcut_time = now
        return False

    def _flash_control(self, w: Optional[tk.Misc]) -> None:
        if w is None:
            return
        if getattr(w, "_utility_transport_cell", False):
            self._flash_transport_utility_cell(w)  # type: ignore[arg-type]
            return
        if isinstance(w, tk.Button):
            self._flash_button_widget(w)
        elif isinstance(w, tk.Label) and w in self._jump_buttons:
            self._flash_jump_label_widget(w)
        elif isinstance(w, tk.Frame):
            self._flash_tile_widget(w)

    def _flash_button_widget(self, btn: tk.Button) -> None:
        try:
            if str(btn.cget("state")) == "disabled":
                return
        except tk.TclError:
            return
        key = id(btn)
        prev_after = self._flash_after_ids.pop(key, None)
        if prev_after is not None:
            try:
                btn.after_cancel(prev_after)
            except Exception:
                pass

        def restore() -> None:
            self._flash_after_ids.pop(key, None)
            self._refresh_all_widget_chrome()

        btn.configure(
            bg=self.accent_active,
            fg=self.teal_on_fg,
            activebackground=self.accent_btn_press,
            activeforeground=self.teal_on_fg,
        )
        self._flash_after_ids[key] = btn.after(95, restore)

    def _flash_jump_label_widget(self, lbl: tk.Label) -> None:
        try:
            if not self._tiles_enabled:
                return
        except tk.TclError:
            return
        key = id(lbl)
        prev_after = self._flash_after_ids.pop(key, None)
        if prev_after is not None:
            try:
                lbl.after_cancel(prev_after)
            except Exception:
                pass

        def restore() -> None:
            self._flash_after_ids.pop(key, None)
            self._refresh_all_widget_chrome()

        lbl.configure(bg=self.accent_active, fg=self.teal_on_fg)
        self._flash_after_ids[key] = lbl.after(95, restore)

    def _flash_tile_widget(self, inner: tk.Frame) -> None:
        allow = self._tiles_enabled or bool(getattr(inner, "_tile_flash_when_disabled", False))
        if not allow:
            return
        key = id(inner)
        prev_after = self._flash_after_ids.pop(key, None)
        if prev_after is not None:
            try:
                inner.after_cancel(prev_after)
            except Exception:
                pass
        widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
        orig_bg = inner.cget("bg")
        orig_children: list[tuple[tk.Label, str, str]] = []
        for ch in widgets:
            orig_children.append((ch, ch.cget("bg"), ch.cget("fg")))

        def restore() -> None:
            try:
                inner.configure(bg=orig_bg)
                for ch, obg, ofg in orig_children:
                    ch.configure(bg=obg, fg=ofg)
            except tk.TclError:
                pass
            self._flash_after_ids.pop(key, None)
            self._refresh_all_widget_chrome()

        if bool(getattr(inner, "_is_genre_tile", False)):
            flash_bg = str(getattr(inner, "_tile_active_bg", self.accent_active))
            fg_flash = _fg_for_genre_bg(flash_bg)
            sc_flash = _genre_shortcut_fg(fg_flash)
            inner.configure(bg=flash_bg)
            shortcut_l = widgets[-1]
            for ch in widgets:
                ch.configure(bg=flash_bg, fg=sc_flash if ch is shortcut_l else fg_flash)
        else:
            inner.configure(bg=self.accent_active)
            for ch in widgets:
                ch.configure(bg=self.accent_active, fg=self.fg)
        self._flash_after_ids[key] = inner.after(95, restore)

    def _kbd(self, keysym: str, fn: Callable[[], None], flash: Optional[tk.Misc] = None) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            fn()
            if flash is not None:
                self._flash_control(flash)
            return "break"

        return handler

    def _kbd_delta_plus_30(self, keysym: str) -> Callable[[object], Optional[str]]:
        """+30s / D key: red hold in Tinder if jump would pass track end (same idea as over-duration presets)."""

        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if not self._tiles_enabled:
                return "break"
            if self._tinder_delta_plus_would_exceed():
                self._style_jump_label_error_hold(self._jump_btn_plus)
                self._tinder_kb_jump_error_plus30 = True
                return "break"
            self._tinder_kb_jump_error_plus30 = False
            if self._suppress_key_repeat(keysym):
                return "break"
            self.jump_delta(30)
            self._flash_control(self._jump_btn_plus)
            return "break"

        return handler

    def _kbd_replay(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if not self._tiles_enabled:
                return "break"
            now = time.monotonic()
            if now - self._last_replay_key_ts < _REPLAY_KEY_DEBOUNCE_SEC:
                return "break"
            self._last_replay_key_ts = now
            if self._suppress_key_repeat(keysym):
                return "break"
            self._replay_active_jump_point()
            self._flash_control(self.btn_replay)
            return "break"

        return handler

    def _replay_from_playback_start(self) -> None:
        path = self._current_path()
        if path is None:
            return
        start = self._desired_start_sec
        self._play_from_position(path, start)

    def _replay_active_jump_point(self) -> None:
        path = self._current_path()
        if path is None:
            return
        sel = self._jump_highlight_key
        if sel == "outro":
            sec = self._outro_jump_seconds()
        elif isinstance(sel, int):
            sec = float(sel)
        else:
            sec = self._desired_start_sec
        if self._duration_sec > 0:
            sec = min(max(0.0, sec), self._duration_sec)
        self._play_from_position(path, sec)

    def _toggle_fullscreen(self, _e: object | None = None) -> Optional[str]:
        self._fullscreen = not self._fullscreen
        try:
            self.root.attributes("-fullscreen", self._fullscreen)
        except tk.TclError:
            pass
        return "break"

    def _on_escape_key(self, _e: object) -> Optional[str]:
        if self._fullscreen:
            self._fullscreen = False
            try:
                self.root.attributes("-fullscreen", False)
            except tk.TclError:
                pass
            return "break"
        return None

    def _bind_keys(self) -> None:
        b = self.root.bind_all
        b("<space>", self._kbd("space", self.toggle_play, self.btn_play))
        b("<Return>", self._kbd_skip("Return"))
        b("<KP_Enter>", self._kbd_skip("KP_Enter"))
        b("<Right>", self._kbd_cleanup_sort("Right", "keep", self.btn_keep))
        b("<Left>", self._kbd_cleanup_sort("Left", "archive", self.btn_archive))
        b("<Up>", self._kbd_cleanup_sort("Up", "favorites", self.btn_favorite))
        b("<Down>", self._kbd("Down", self._replay_from_playback_start, self.btn_play))
        b("<KeyPress-s>", self._kbd("s", lambda: self._set_sorting_mode("swipe"), self._tile_mode_cleanup_inner))
        b("<KeyPress-S>", self._kbd("S", lambda: self._set_sorting_mode("swipe"), self._tile_mode_cleanup_inner))
        b("<KeyPress-t>", self._kbd("t", lambda: self._set_sorting_mode("swipe"), self._tile_mode_cleanup_inner))
        b("<KeyPress-T>", self._kbd("T", lambda: self._set_sorting_mode("swipe"), self._tile_mode_cleanup_inner))
        b("<KeyPress-g>", self._kbd("g", lambda: self._set_sorting_mode("grid"), self._tile_mode_genre_inner))
        b("<KeyPress-G>", self._kbd("G", lambda: self._set_sorting_mode("grid"), self._tile_mode_genre_inner))
        b("<KeyPress-u>", self._kbd("u", lambda: self._on_pick_track_order("random"), self._tile_order_random_inner))
        b("<KeyPress-U>", self._kbd("U", lambda: self._on_pick_track_order("random"), self._tile_order_random_inner))
        b("<KeyPress-o>", self._kbd("o", lambda: self._on_pick_track_order("in_order"), self._tile_order_in_order_inner))
        b("<KeyPress-O>", self._kbd("O", lambda: self._on_pick_track_order("in_order"), self._tile_order_in_order_inner))
        b("<KeyPress-r>", self._kbd_replay("r"))
        b("<KeyPress-R>", self._kbd_replay("R"))
        b("<KeyPress-z>", self._kbd_undo("z"))
        b("<KeyPress-Z>", self._kbd_undo("Z"))
        b("<Control-Key-z>", self._kbd_undo_all("Control-z"))
        b("<Control-Key-Z>", self._kbd_undo_all("Control-Z"))

        for d in range(1, 10):

            def _digit_handler(n: int) -> Callable[[object], Optional[str]]:
                def handler(e: object) -> Optional[str]:
                    return self._dispatch_digit_key(n, e)

                return handler

            dh = _digit_handler(d)
            b(f"<Key-{d}>", dh)
            b(f"<KP_{d}>", dh)

        for d in range(1, 10):

            def _digit_key_up(n: int) -> Callable[[object], None]:
                def inner(ev: object) -> None:
                    self._on_tinder_digit_key_release(n, ev)

                return inner

            ku = _digit_key_up(d)
            self.root.bind_all(f"<KeyRelease-{d}>", ku, add="+")
            self.root.bind_all(f"<KeyRelease-KP_{d}>", ku, add="+")

        self.root.bind_all("<ButtonRelease-1>", self._on_global_button_release, add="+")
        self.root.bind_all("<KeyRelease-d>", self._on_tinder_delta_plus_key_release, add="+")
        self.root.bind_all("<KeyRelease-D>", self._on_tinder_delta_plus_key_release, add="+")

        b("<KeyPress-a>", self._kbd("a", lambda: self.jump_delta(-30), self._jump_btn_minus))
        b("<KeyPress-A>", self._kbd("A", lambda: self.jump_delta(-30), self._jump_btn_minus))
        b("<KeyPress-d>", self._kbd_delta_plus_30("d"))
        b("<KeyPress-D>", self._kbd_delta_plus_30("D"))
        b("<KeyPress-h>", self._on_help_key)
        b("<KeyPress-H>", self._on_help_key)
        b("<KeyPress-q>", self._kbd_toggle_queue_panel)
        b("<KeyPress-Q>", self._kbd_toggle_queue_panel)
        self.root.bind_all("<Command-f>", self._toggle_fullscreen)
        self.root.bind_all("<Command-F>", self._toggle_fullscreen)
        # Escape on main window only: leaves Toplevel dialogs so Cancel keeps default Escape behavior when focused there.
        self.root.bind("<Escape>", self._on_escape_key)
        self.root.bind("<FocusIn>", self._on_app_focus_in, add="+")

    def _on_app_focus_in(self, event: object) -> None:
        """After native dialogs, refresh button colors (macOS Tk white-button glitch)."""
        try:
            w = getattr(event, "widget", None)
            if w is None or str(w) != str(self.root):
                return
        except Exception:
            return
        self.root.after(10, self._refresh_all_widget_chrome)

    def _set_buttons_enabled(self, on: bool) -> None:
        self._tiles_enabled = on
        # Load folder must stay enabled so users can pick a folder when the queue is empty or finished.
        self.btn_load.configure(state="normal")
        self._refresh_all_widget_chrome()

    def _genre_stats_text(self) -> str:
        """Per-genre file counts under GENRES/ plus queue length — single line, no newlines."""
        root = self._music_root
        if root is None:
            return ""
        base_gen = root / GENRES_NAME
        parts: list[str] = []
        for idx in range(9):
            lab = self._genre_labels[idx]
            fn = self._genre_folder_names[idx] if idx < len(self._genre_folder_names) else safe_genre_folder_name(lab)
            d = base_gen / safe_genre_folder_name(fn)
            n = _count_files_in_dir(d) if d.is_dir() else 0
            parts.append(f"{lab}: {n}")
        nq = len(self._queue)
        return " · ".join(parts) + f"   ·   Queue: {nq}"

    def _refresh_stats(self) -> None:
        if not self._music_root or not self._folders:
            self.lbl_stats.configure(text="")
            self._update_content_wraplengths()
            return
        if self._sorting_mode == "grid":
            self.lbl_stats.configure(text=self._genre_stats_text())
        else:
            k = _count_files_in_dir(self._folders["keep"])
            a = _count_files_in_dir(self._folders["archive"])
            f = _count_files_in_dir(self._folders["favorites"])
            s0, s1, s2 = (self._swipe_labels + ["?", "?", "?"])[:3]
            base = f"{s0}: {k}   {s1}: {a}   {s2}: {f}"
            self.lbl_stats.configure(text=base)
        self._update_content_wraplengths()

    def _reset_session_counters(self) -> None:
        self._session_kept = 0
        self._session_archived = 0
        self._session_favorites = 0
        self._session_skipped = 0
        self._session_genre = 0
        self._session_started_monotonic = time.monotonic()
        self._refresh_session_stats()

    def _refresh_session_stats(self) -> None:
        if self.lbl_session_stats is None:
            return
        if not self._music_root:
            self.lbl_session_stats.configure(text="")
            return
        tot = (
            self._session_kept
            + self._session_archived
            + self._session_favorites
            + self._session_skipped
            + self._session_genre
        )
        elapsed = int(time.monotonic() - self._session_started_monotonic)
        em = elapsed // 60
        es = elapsed % 60
        if em >= 1:
            sess = f"{em}m" if es == 0 else f"{em}m {es}s"
        else:
            sess = f"{elapsed}s"
        if tot <= 0:
            self.lbl_session_stats.configure(text=f"Session: {sess}", fg=self.muted)
            return
        kr = (100.0 * self._session_kept / tot) if tot > 0 else 0.0
        parts = [
            f"Sorted: {tot}",
            f"Kept: {self._session_kept}",
            f"Archived: {self._session_archived}",
            f"Favorites: {self._session_favorites}",
            f"Skipped: {self._session_skipped}",
        ]
        if self._sorting_mode == "grid":
            parts.append(f"Genres sorted: {self._session_genre}")
        line1 = "  ·  ".join(parts)
        line2 = f"Keep rate: {kr:.0f}%  ·  Session: {sess}"
        self.lbl_session_stats.configure(text=f"{line1}\n{line2}", fg=self.muted)

    def _session_undo_bucket(self, bucket: Optional[str]) -> None:
        if bucket == "keep":
            self._session_kept = max(0, self._session_kept - 1)
        elif bucket == "archive":
            self._session_archived = max(0, self._session_archived - 1)
        elif bucket == "favorites":
            self._session_favorites = max(0, self._session_favorites - 1)
        elif bucket in ("genre", "grid"):
            self._session_genre = max(0, self._session_genre - 1)
        self._refresh_session_stats()

    def _cleanup_temp_audio(self) -> None:
        """Legacy hook (ffmpeg temp WAVs); mpv playback leaves no preview temps."""

    def _on_include_subfolders_toggle(self) -> None:
        save_include_subfolders_setting(bool(self._var_include_subfolders.get()))

    def _on_auto_load_default_toggle(self) -> None:
        save_auto_load_default_folder(bool(self._var_auto_load_default.get()))

    def _close_settings_dialog(self, top: tk.Toplevel) -> None:
        try:
            top.destroy()
        except tk.TclError:
            pass
        self._settings_top = None
        self.root.after(10, self._refresh_all_widget_chrome)

    def _open_settings_dialog(self) -> None:
        st = getattr(self, "_settings_top", None)
        if st is not None:
            try:
                if st.winfo_exists():
                    st.lift()
                    return
            except tk.TclError:
                pass
        self._prepare_native_dialog()
        top = tk.Toplevel(self.root)
        self._settings_top = top
        top.title("Settings")
        top.configure(bg=self.bg)
        top.minsize(560, 520)
        try:
            top.transient(self.root)
        except tk.TclError:
            pass

        def close() -> None:
            self._close_settings_dialog(top)

        top.protocol("WM_DELETE_WINDOW", close)

        outer = tk.Frame(top, bg=self.bg)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(outer, text="Settings", bg=self.bg, fg=self.accent, font=("Helvetica", 14, "bold")).pack(anchor="w")

        body = tk.Frame(outer, bg=self.panel, highlightthickness=1, highlightbackground=self.panel_border)
        body.pack(fill="both", expand=True, pady=(10, 0))
        inner = tk.Frame(body, bg=self.panel)
        inner.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(inner, text="Default Music Folder", bg=self.panel, fg=self.fg, font=("Helvetica", 11, "bold")).pack(
            anchor="w",
        )
        lbl_path = tk.Label(
            inner,
            text="",
            bg=self.panel,
            fg=self.muted,
            font=("Helvetica", 9),
            anchor="w",
            justify="left",
            wraplength=440,
        )
        lbl_path.pack(anchor="w", pady=(4, 2))
        lbl_missing = tk.Label(
            inner,
            text="",
            bg=self.panel,
            fg=self.warning,
            font=("Helvetica", 9),
            anchor="w",
            wraplength=440,
        )
        lbl_missing.pack(anchor="w", pady=(0, 6))

        def refresh_folder_labels() -> None:
            p = load_default_folder_path()
            if not p:
                lbl_path.configure(text="No default folder set", fg=self.muted)
                lbl_missing.configure(text="")
                return
            display = p if len(p) <= 80 else "…" + p[-77:]
            lbl_path.configure(text=f"Current: {display}", fg=self.muted)
            try:
                ok = Path(p).expanduser().resolve().is_dir()
            except (OSError, RuntimeError):
                ok = False
            if ok:
                lbl_missing.configure(text="")
            else:
                lbl_missing.configure(
                    text="This folder is no longer available.",
                    fg=self.warning,
                )

        refresh_folder_labels()

        row_fold = tk.Frame(inner, bg=self.panel)
        row_fold.pack(anchor="w", pady=(0, 10))

        def on_set_def() -> None:
            try:
                self._prepare_native_dialog()
                folder = filedialog.askdirectory(title="Choose default music folder", parent=top)
                if not folder:
                    return
                path = Path(folder).resolve()
                save_default_folder_path(path)
                self._refresh_default_folder_label()
                refresh_folder_labels()
                if messagebox.askyesno(
                    "Default folder",
                    "Load this folder now?",
                    parent=top,
                ):
                    self._load_folder_from_path(path, missing_message=self._default_folder_missing_message())
            finally:
                self.root.after(10, self._refresh_all_widget_chrome)

        def on_clear_def() -> None:
            save_default_folder_path(None)
            self._refresh_default_folder_label()
            refresh_folder_labels()

        def on_load_def() -> None:
            self._prepare_native_dialog()
            p = load_default_folder_path()
            if not p:
                messagebox.showinfo(
                    "Default folder",
                    "No default folder is set. Use Set Default Folder first.",
                    parent=top,
                )
                return
            self._load_folder_from_path(Path(p), missing_message=self._default_folder_missing_message())
            refresh_folder_labels()

        b_set = tk.Button(
            row_fold,
            text="Set Default Folder",
            command=on_set_def,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(b_set)
        b_set.pack(side="left")
        b_clear = tk.Button(
            row_fold,
            text="Clear Default Folder",
            command=on_clear_def,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(b_clear)
        b_clear.pack(side="left", padx=(8, 0))
        b_load = tk.Button(
            row_fold,
            text="Load Default Folder",
            command=on_load_def,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(b_load)
        b_load.pack(side="left", padx=(8, 0))

        tk.Checkbutton(
            inner,
            text="Auto-load default folder on startup",
            variable=self._var_auto_load_default,
            command=self._on_auto_load_default_toggle,
            bg=self.panel,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.panel,
            activeforeground=self.fg,
            highlightthickness=0,
            font=("Helvetica", 10),
        ).pack(anchor="w", pady=(6, 12))

        sep = tk.Frame(inner, bg=self.panel_border, height=1)
        sep.pack_propagate(False)
        sep.pack(fill="x", pady=(0, 10))

        tk.Label(
            inner,
            text="Default start time (new tracks)",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w")
        row_st = tk.Frame(inner, bg=self.panel)
        row_st.pack(anchor="w", pady=(6, 6))
        tk.Label(row_st, text="Auto-play from:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        v_start = tk.StringVar(master=top, value=playback_sec_to_choice_label(self._user_playback_start_sec))

        def on_start_change(*_a: object) -> None:
            sec = playback_start_label_to_sec(v_start.get())
            self._apply_user_playback_start_change(sec)

        om_start = tk.OptionMenu(row_st, v_start, *playback_start_choice_labels(), command=on_start_change)
        self._style_tk_option_menu_transport(om_start)
        om_start.pack(side="left", padx=(8, 0))

        tk.Label(
            inner,
            text="Default mode on startup",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w", pady=(10, 0))
        row_mode = tk.Frame(inner, bg=self.panel)
        row_mode.pack(anchor="w", pady=(6, 6))
        tk.Label(row_mode, text="Mode:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        ini = read_user_settings()
        dm_cur = _norm_sorting_mode_value(ini.get("default_mode_on_startup", "swipe"))
        v_mode = tk.StringVar(master=top, value="Swipe" if dm_cur == "swipe" else "Grid")

        def on_mode_change(*_a: object) -> None:
            key: SortingMode = "swipe" if v_mode.get() == "Swipe" else "grid"
            patch_user_settings(default_mode_on_startup=key)

        om_mode = tk.OptionMenu(row_mode, v_mode, "Swipe", "Grid", command=on_mode_change)
        self._style_tk_option_menu_transport(om_mode)
        om_mode.pack(side="left", padx=(8, 0))

        tk.Label(
            inner,
            text="Startup preset (optional)",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w", pady=(10, 0))
        row_sp = tk.Frame(inner, bg=self.panel)
        row_sp.pack(anchor="w", pady=(6, 6))
        tk.Label(row_sp, text="Preset:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        all_presets_list = [p for p in (self._mode_preset_store.get("presets") or []) if isinstance(p, dict)]
        startup_labels = ["(none)"] + [format_preset_menu_label(p) for p in all_presets_list]
        cur_sid = read_user_settings().get("startup_preset_id")
        cur_startup_lbl = "(none)"
        if isinstance(cur_sid, str) and cur_sid.strip():
            cp = preset_by_id(self._mode_preset_store, cur_sid.strip())
            if cp:
                cur_startup_lbl = format_preset_menu_label(cp)
        if cur_startup_lbl not in startup_labels:
            cur_startup_lbl = "(none)"
        v_sp = tk.StringVar(master=top, value=cur_startup_lbl)

        def on_startup_preset(*_a: object) -> None:
            lab = v_sp.get()
            if lab == "(none)":
                patch_user_settings(startup_preset_id=None)
                return
            for p in all_presets_list:
                if format_preset_menu_label(p) == lab:
                    patch_user_settings(startup_preset_id=str(p.get("id")))
                    return

        om_sp = tk.OptionMenu(row_sp, v_sp, *startup_labels, command=on_startup_preset)
        self._style_tk_option_menu_transport(om_sp)
        om_sp.pack(side="left", padx=(8, 0))

        tk.Label(
            inner,
            text="Default track order",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w", pady=(10, 0))
        row_ord = tk.Frame(inner, bg=self.panel)
        row_ord.pack(anchor="w", pady=(6, 6))
        tk.Label(row_ord, text="Order:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        v_ord = tk.StringVar(master=top)

        def _order_label(o: TrackOrder) -> str:
            return "Random" if o == "random" else "In Order"

        v_ord.set(_order_label(self._track_order))

        def on_ord_change(*_a: object) -> None:
            order: TrackOrder = "random" if v_ord.get() == "Random" else "in_order"
            if order == self._track_order:
                return
            if self._queue:
                self._prepare_native_dialog()
                if not messagebox.askyesno(
                    "Reload track order?",
                    "Reload track list with new order?",
                    parent=top,
                ):
                    v_ord.set(_order_label(self._track_order))
                    return
            self._track_order = order
            save_track_order_setting(order)
            self._apply_order_toggle_chrome()
            if self._queue:
                self._reorder_loaded_queue_preserving_current()
            self._refresh_all_widget_chrome()

        om_ord = tk.OptionMenu(row_ord, v_ord, "Random", "In Order", command=on_ord_change)
        self._style_tk_option_menu_transport(om_ord)
        om_ord.pack(side="left", padx=(8, 0))

        tk.Checkbutton(
            inner,
            text="Include subfolders when scanning",
            variable=self._var_include_subfolders,
            command=self._on_include_subfolders_toggle,
            bg=self.panel,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.panel,
            activeforeground=self.fg,
            highlightthickness=0,
            font=("Helvetica", 10),
        ).pack(anchor="w", pady=(8, 4))

        v_help_su = tk.BooleanVar(master=top, value=bool(read_user_settings()["show_help_guide_on_startup"]))

        def on_help_su() -> None:
            patch_user_settings(show_help_guide_on_startup=bool(v_help_su.get()))

        tk.Checkbutton(
            inner,
            text="Show help guide on startup",
            variable=v_help_su,
            command=on_help_su,
            bg=self.panel,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.panel,
            activeforeground=self.fg,
            highlightthickness=0,
            font=("Helvetica", 10),
        ).pack(anchor="w", pady=(4, 4))

        v_queue_su = tk.BooleanVar(master=top, value=bool(read_user_settings()["show_queue_panel_on_startup"]))

        def on_queue_su() -> None:
            patch_user_settings(show_queue_panel_on_startup=bool(v_queue_su.get()))

        tk.Checkbutton(
            inner,
            text="Show queue panel on startup",
            variable=v_queue_su,
            command=on_queue_su,
            bg=self.panel,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.panel,
            activeforeground=self.fg,
            highlightthickness=0,
            font=("Helvetica", 10),
        ).pack(anchor="w", pady=(4, 8))

        sep_genre_st = tk.Frame(inner, bg=self.panel_border, height=1)
        sep_genre_st.pack_propagate(False)
        sep_genre_st.pack(fill="x", pady=(0, 10))

        tk.Label(
            inner,
            text="Genre settings",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="Edit grid labels and colors, or reset the active grid preset to defaults.",
            bg=self.panel,
            fg=self.muted,
            font=("Helvetica", 9),
            wraplength=480,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(4, 8))

        row_genre_st = tk.Frame(inner, bg=self.panel)
        row_genre_st.pack(anchor="w", pady=(0, 8))

        def on_edit_genres_from_settings() -> None:
            self._open_edit_genres_dialog()

        def on_reset_genres_defaults() -> None:
            self._prepare_native_dialog()
            if not messagebox.askyesno(
                "Reset genres",
                "Reset the active grid preset to default names, colors, and folders? This saves immediately.",
                parent=top,
            ):
                return
            self._reset_genres_to_defaults_and_persist()

        b_edit_genres_st = tk.Button(
            row_genre_st,
            text="Edit genre labels & colors",
            command=on_edit_genres_from_settings,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(b_edit_genres_st)
        b_edit_genres_st.pack(side="left")
        b_reset_genres_st = tk.Button(
            row_genre_st,
            text="Reset genres to default",
            command=on_reset_genres_defaults,
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=5,
        )
        self._style_tk_button_transport_grey(b_reset_genres_st)
        b_reset_genres_st.pack(side="left", padx=(8, 0))

        sep_mp = tk.Frame(inner, bg=self.panel_border, height=1)
        sep_mp.pack_propagate(False)
        sep_mp.pack(fill="x", pady=(0, 10))

        tk.Label(
            inner,
            text="Mode presets",
            bg=self.panel,
            fg=self.fg,
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="Swipe presets set the three hotkey targets; grid presets define nine folders under GENRES/.",
            bg=self.panel,
            fg=self.muted,
            font=("Helvetica", 9),
            wraplength=480,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(4, 8))

        def _settings_refresh_from_store() -> None:
            self._mode_preset_store = load_mode_preset_store()
            self._apply_track_labels_from_preset_store()
            self._sync_swipe_tiles_from_preset()
            self._sync_genre_tile_titles()
            self._apply_genre_tile_colors()
            self._refresh_shortcut_guide_swipe_lines()
            if self._music_root is not None:
                trip = tuple(str(x) for x in active_swipe_preset(self._mode_preset_store)["folders"][:3])
                self._folders = ensure_sort_folders(self._music_root, trip)
                ensure_genre_folders(self._music_root, list(active_grid_preset(self._mode_preset_store)["folders"][:9]))
            self._refresh_stats()
            self.root.after(10, self._refresh_all_widget_chrome)

        def _settings_reopen() -> None:
            close()
            self.root.after(80, self._open_settings_dialog)

        swipe_presets = presets_of_mode(self._mode_preset_store, "swipe")
        grid_presets = presets_of_mode(self._mode_preset_store, "grid")
        swipe_menu_labs = [format_preset_menu_label(p) for p in swipe_presets] or ["Swipe · Default"]
        grid_menu_labs = [format_preset_menu_label(p) for p in grid_presets] or ["Grid · Default"]

        row_ap = tk.Frame(inner, bg=self.panel)
        row_ap.pack(anchor="w", pady=(0, 4))
        tk.Label(row_ap, text="Swipe:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        aid = str(self._mode_preset_store.get("active_swipe_preset_id") or "")

        def _sw_lab(pid: str) -> str:
            x = preset_by_id(self._mode_preset_store, pid)
            return format_preset_menu_label(x) if x else swipe_menu_labs[0]

        v_sw_act = tk.StringVar(master=top, value=_sw_lab(aid))

        def on_sw_act(*_a: object) -> None:
            lab = v_sw_act.get()
            for p in swipe_presets:
                if format_preset_menu_label(p) == lab:
                    self._mode_preset_store["active_swipe_preset_id"] = str(p["id"])
                    save_mode_preset_store(self._mode_preset_store)
                    _settings_refresh_from_store()
                    return

        om_sw_act = tk.OptionMenu(row_ap, v_sw_act, *swipe_menu_labs, command=on_sw_act)
        self._style_tk_option_menu_transport(om_sw_act)
        om_sw_act.pack(side="left", padx=(6, 16))

        tk.Label(row_ap, text="Grid:", bg=self.panel, fg=self.muted, font=("Helvetica", 10)).pack(side="left")
        gid = str(self._mode_preset_store.get("active_grid_preset_id") or "")

        def _gr_lab(pid: str) -> str:
            x = preset_by_id(self._mode_preset_store, pid)
            return format_preset_menu_label(x) if x else grid_menu_labs[0]

        v_gr_act = tk.StringVar(master=top, value=_gr_lab(gid))

        def on_gr_act(*_a: object) -> None:
            lab = v_gr_act.get()
            for p in grid_presets:
                if format_preset_menu_label(p) == lab:
                    self._mode_preset_store["active_grid_preset_id"] = str(p["id"])
                    save_mode_preset_store(self._mode_preset_store)
                    _settings_refresh_from_store()
                    return

        om_gr_act = tk.OptionMenu(row_ap, v_gr_act, *grid_menu_labs, command=on_gr_act)
        self._style_tk_option_menu_transport(om_gr_act)
        om_gr_act.pack(side="left", padx=(6, 0))

        row_edit = tk.Frame(inner, bg=self.panel)
        row_edit.pack(anchor="w", pady=(6, 4))

        def on_edit_sw() -> None:
            self._open_edit_swipe_preset_dialog()

        def on_edit_gr() -> None:
            self._open_edit_genres_dialog()

        b_es = tk.Button(
            row_edit,
            text="Edit swipe preset",
            command=on_edit_sw,
            font=("Helvetica", 10, "bold"),
            padx=8,
            pady=4,
        )
        self._style_tk_button_transport_grey(b_es)
        b_es.pack(side="left", padx=(0, 6))
        b_eg = tk.Button(
            row_edit,
            text="Edit grid preset",
            command=on_edit_gr,
            font=("Helvetica", 10, "bold"),
            padx=8,
            pady=4,
        )
        self._style_tk_button_transport_grey(b_eg)
        b_eg.pack(side="left", padx=(0, 6))

        row_cr = tk.Frame(inner, bg=self.panel)
        row_cr.pack(anchor="w", pady=(6, 4))

        def new_sw() -> None:
            raw = simpledialog.askstring("New swipe preset", "Name:", parent=top)
            if not raw or not str(raw).strip():
                return
            d = _default_swipe_preset_dict()
            d["name"] = str(raw).strip()
            self._upsert_preset_in_store(d)
            self._mode_preset_store["active_swipe_preset_id"] = d["id"]
            save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def new_gr() -> None:
            raw = simpledialog.askstring("New grid preset", "Name:", parent=top)
            if not raw or not str(raw).strip():
                return
            labels, colors = load_genre_config()
            d = _coerce_grid_preset_blob(
                {
                    "id": _new_preset_id(),
                    "name": str(raw).strip(),
                    "mode": "grid",
                    "labels": labels,
                    "colors": colors,
                    "folders": [safe_genre_folder_name(x) for x in labels],
                },
            )
            if d:
                self._upsert_preset_in_store(d)
                self._mode_preset_store["active_grid_preset_id"] = d["id"]
                save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def dup_sw() -> None:
            pid = str(self._mode_preset_store.get("active_swipe_preset_id") or "")
            p = preset_by_id(self._mode_preset_store, pid)
            if not p:
                return
            bb = _coerce_swipe_preset_blob(dict(p))
            if not bb:
                return
            bb["id"] = _new_preset_id()
            bb["name"] = str(p.get("name", "Swipe")) + " copy"
            self._upsert_preset_in_store(bb)
            self._mode_preset_store["active_swipe_preset_id"] = bb["id"]
            save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def dup_gr() -> None:
            pid = str(self._mode_preset_store.get("active_grid_preset_id") or "")
            p = preset_by_id(self._mode_preset_store, pid)
            if not p:
                return
            bb = _coerce_grid_preset_blob(dict(p))
            if not bb:
                return
            bb["id"] = _new_preset_id()
            bb["name"] = str(p.get("name", "Grid")) + " copy"
            self._upsert_preset_in_store(bb)
            self._mode_preset_store["active_grid_preset_id"] = bb["id"]
            save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def del_sw() -> None:
            if len(swipe_presets) <= 1:
                messagebox.showinfo("Presets", "Cannot delete the last swipe preset.", parent=top)
                return
            pid = str(self._mode_preset_store.get("active_swipe_preset_id") or "")
            if not messagebox.askyesno("Delete swipe preset", "Delete the active swipe preset?", parent=top):
                return
            pl = [p for p in (self._mode_preset_store.get("presets") or []) if isinstance(p, dict) and str(p.get("id")) != pid]
            self._mode_preset_store["presets"] = pl
            nsw = presets_of_mode(self._mode_preset_store, "swipe")
            if nsw:
                self._mode_preset_store["active_swipe_preset_id"] = str(nsw[0]["id"])
            save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def del_gr() -> None:
            if len(grid_presets) <= 1:
                messagebox.showinfo("Presets", "Cannot delete the last grid preset.", parent=top)
                return
            pid = str(self._mode_preset_store.get("active_grid_preset_id") or "")
            if not messagebox.askyesno("Delete grid preset", "Delete the active grid preset?", parent=top):
                return
            pl = [p for p in (self._mode_preset_store.get("presets") or []) if isinstance(p, dict) and str(p.get("id")) != pid]
            self._mode_preset_store["presets"] = pl
            ngr = presets_of_mode(self._mode_preset_store, "grid")
            if ngr:
                self._mode_preset_store["active_grid_preset_id"] = str(ngr[0]["id"])
            save_mode_preset_store(self._mode_preset_store)
            _settings_reopen()

        def rst_sw() -> None:
            pid = str(self._mode_preset_store.get("active_swipe_preset_id") or "")
            p = preset_by_id(self._mode_preset_store, pid)
            if not p:
                return
            base = _default_swipe_preset_dict()
            bb = _coerce_swipe_preset_blob(
                {
                    "id": pid,
                    "name": str(p.get("name", "Swipe")),
                    "mode": "swipe",
                    "labels": base["labels"],
                    "colors": base["colors"],
                    "folders": base["folders"],
                },
            )
            if bb:
                self._upsert_preset_in_store(bb)
            _settings_refresh_from_store()

        def rst_gr() -> None:
            self._prepare_native_dialog()
            if not messagebox.askyesno(
                "Reset grid preset",
                "Reset active grid preset to default labels, colors, and folders?",
                parent=top,
            ):
                return
            self._reset_genres_to_defaults_and_persist()
            _settings_refresh_from_store()

        for txt, cmd in (
            ("New swipe", new_sw),
            ("New grid", new_gr),
            ("Duplicate swipe", dup_sw),
            ("Duplicate grid", dup_gr),
        ):
            bx = tk.Button(row_cr, text=txt, command=cmd, font=("Helvetica", 9, "bold"), padx=6, pady=3)
            self._style_tk_button_transport_grey(bx)
            bx.pack(side="left", padx=(0, 4))

        row_del = tk.Frame(inner, bg=self.panel)
        row_del.pack(anchor="w", pady=(4, 4))
        for txt, cmd in (
            ("Delete swipe", del_sw),
            ("Delete grid", del_gr),
            ("Reset swipe", rst_sw),
            ("Reset grid", rst_gr),
        ):
            bx = tk.Button(row_del, text=txt, command=cmd, font=("Helvetica", 9, "bold"), padx=6, pady=3)
            self._style_tk_button_transport_grey(bx)
            bx.pack(side="left", padx=(0, 4))

        btn_close = tk.Button(outer, text="Close", command=close, font=("Helvetica", 11, "bold"), padx=16, pady=6)
        self._style_tk_button_transport_grey(btn_close)
        btn_close.pack(anchor="e", pady=(12, 0))

        self.root.after(10, self._refresh_all_widget_chrome)

    def _default_folder_missing_message(self) -> str:
        return "Default folder not found. Please set a new default folder."

    def _refresh_default_folder_label(self) -> None:
        if self.lbl_default_folder_path is None:
            return
        p = load_default_folder_path()
        if not p:
            self.lbl_default_folder_path.configure(text="No default folder selected", fg=self.muted)
            return
        display = p
        if len(display) > 72:
            display = "…" + display[-69:]
        self.lbl_default_folder_path.configure(text=f"Default: {display}", fg=self.muted)

    def _load_folder_from_path(self, root: Path, *, missing_message: Optional[str] = None) -> None:
        try:
            resolved = root.expanduser().resolve()
        except (OSError, RuntimeError):
            resolved = root
        if not resolved.is_dir():
            self._prepare_native_dialog()
            messagebox.showinfo(
                "Folder not available",
                missing_message or "Could not open the selected folder.",
                parent=self.root,
            )
            return
        self._scan_id += 1
        sid = self._scan_id
        self._stop_playback()
        try:
            self._set_now_playing_main_plain("Scanning…")
            self.lbl_track.configure(text="Scanning folders… 0 tracks")
            self.lbl_path.configure(text=str(resolved))
            if self.lbl_source_folder is not None:
                self.lbl_source_folder.configure(text="")
        except tk.TclError:
            pass
        self._folder_scan_alive = True
        recursive = bool(self._var_include_subfolders.get())
        skip = collect_skip_scan_dir_names(self._mode_preset_store)
        threading.Thread(
            target=self._folder_scan_worker,
            args=(resolved, sid, recursive, skip),
            daemon=True,
        ).start()
        self._poll_scan_progress()

    def _on_set_default_folder(self) -> None:
        try:
            self._prepare_native_dialog()
            folder = filedialog.askdirectory(title="Choose default music folder", parent=self.root)
            if not folder:
                return
            path = Path(folder).resolve()
            save_default_folder_path(path)
            self._refresh_default_folder_label()
            if messagebox.askyesno(
                "Default folder",
                "Load this folder now?",
                parent=self.root,
            ):
                self._load_folder_from_path(path, missing_message=self._default_folder_missing_message())
        finally:
            self.root.after(10, self._refresh_all_widget_chrome)

    def _on_load_default_folder(self) -> None:
        p = load_default_folder_path()
        if not p:
            self._prepare_native_dialog()
            messagebox.showinfo(
                "Default folder",
                "No default folder is set. Use Set Default Folder first.",
                parent=self.root,
            )
            return
        self._load_folder_from_path(Path(p), missing_message=self._default_folder_missing_message())
        self.root.after(10, self._refresh_all_widget_chrome)

    def _maybe_autoload_default_folder(self) -> None:
        if not self._var_auto_load_default.get():
            return
        p = load_default_folder_path()
        if not p:
            return
        root = Path(p)
        try:
            resolved = root.expanduser().resolve()
        except (OSError, RuntimeError):
            resolved = root
        if not resolved.is_dir():
            self._prepare_native_dialog()
            messagebox.showinfo(
                "Default folder",
                self._default_folder_missing_message(),
                parent=self.root,
            )
            return
        self._load_folder_from_path(resolved, missing_message=self._default_folder_missing_message())
        self.root.after(10, self._refresh_all_widget_chrome)

    def pick_folder(self) -> None:
        try:
            self._prepare_native_dialog()
            folder = filedialog.askdirectory(title="Choose music folder", parent=self.root)
            if not folder:
                return
            self._load_folder_from_path(Path(folder))
        finally:
            self.root.after(10, self._refresh_all_widget_chrome)

    def _current_path(self) -> Optional[Path]:
        return self._queue[0] if self._queue else None

    def _current_position_in_source(self) -> float:
        if self._is_playing:
            est = self._mpv_clock_anchor_pos + (time.monotonic() - self._mpv_clock_anchor_mono)
            if self._duration_sec > 0:
                return min(self._duration_sec, max(0.0, est))
            return max(0.0, est)
        return self._audio_offset_in_source

    def _stop_playback(self) -> None:
        if _which_mpv() and self._mpv_ipc_sock is not None:
            self._mpv_stop_media()
        self._cleanup_temp_audio()
        self._is_playing = False
        self._audio_offset_in_source = 0.0
        self._mpv_cached_time_pos = 0.0
        self._mpv_set_playback_clock_anchor(0.0)
        self._set_play_button_text("Play")

    def _skip_track_after_playback_error(self, detail: str) -> None:
        """Warn user, then remove current track from the queue (same as Skip, with undo). Uses ``after`` to avoid deep recursion."""
        if not self._queue:
            return
        try:
            self._prepare_native_dialog()
            messagebox.showwarning(
                "Playback",
                detail,
                parent=self.root,
            )
        except Exception:
            pass
        self.root.after(0, self._skip_current_safe_after_error)

    def _skip_current_safe_after_error(self) -> None:
        if not self._queue:
            return
        try:
            self.skip_current()
        except Exception as e:
            self._perf_log(f"skip_current_safe_after_error: {e}")

    def _mpv_loadfile(self, path: Path, pos_sec: float, *, start_paused: bool) -> bool:
        """Tell mpv to load ``path`` at ``pos_sec`` (playing or paused)."""
        exe = _which_mpv()
        if not exe or self._mpv_ipc_sock is None:
            return False
        pos_sec = max(0.0, pos_sec)
        if self._duration_sec > 0:
            pos_sec = min(pos_sec, max(0.0, self._duration_sec - 0.05))
        # JSON IPC loadfile rejects the legacy 4th-arg "start=...,pause=..." string ("invalid parameter"
        # on mpv 0.41+). Load, poll until time-pos exists, seek, then pause if requested.
        t0 = time.perf_counter()
        file_key = str(path.resolve())
        ok = self._mpv_command_ok(["loadfile", file_key, "replace"], timeout=3.5)
        if ok and pos_sec > 1e-4:
            # After loadfile, seek only works once the demuxer is ready; slow disks / MP3 open can
            # exceed a few seconds — do not fail on poll alone; outcome is seek success or not.
            ready = self._mpv_poll_until_decoder_ready(25.0)
            if not ready:
                self._perf_log("mpv_loadfile_decoder_ready_slow")
            seek_ok = False
            for attempt in range(1, 6):
                seek_ok = self._mpv_seek_absolute(
                    pos_sec,
                    timeout=2.5,
                    perf_log=(attempt == 1),
                )
                if seek_ok:
                    break
                time.sleep(0.08 * attempt)
            ok = seek_ok
        if ok and start_paused:
            pause_ok = False
            for attempt in range(1, 4):
                pause_ok = self._mpv_set_pause(True, timeout=1.25)
                if pause_ok:
                    break
                time.sleep(0.05 * attempt)
            ok = pause_ok
        self._perf_log(
            f"mpv_track_load_ipc_ms={(time.perf_counter() - t0) * 1000.0:.2f} "
            f"file={path.name!r} start={pos_sec:.3f}s paused={start_paused} ok={ok}",
        )
        if ok:
            self._mpv_loaded_audio_path = file_key
            self._audio_offset_in_source = pos_sec
            self._mpv_set_playback_clock_anchor(pos_sec)
            self._playout_handled = False
            if start_paused:
                self._is_playing = False
                self._set_play_button_text("Play")
            else:
                self._mark_playback_started(pos_sec)
        return ok

    def _play_from_position(self, path: Path, pos_sec: float) -> bool:
        """Load and play ``path`` from ``pos_sec`` via mpv. Returns False if playback did not start."""
        if not _which_mpv():
            try:
                messagebox.showinfo(
                    "mpv required",
                    "mpv is required for playback.\nInstall with:\n\nbrew install mpv",
                    parent=self.root,
                )
            except tk.TclError:
                pass
            return False
        if self._mpv_ipc_sock is None:
            if self._mpv_play_retry_count < 40:
                self._mpv_play_retry_count += 1
                if self._mpv_proc is None:
                    self._mpv_start_idle()
                self.root.after(50, lambda p=path, s=pos_sec: self._play_from_position(p, s))
            else:
                self._perf_log("mpv_ipc_unavailable_giving_up")
                self._mpv_play_retry_count = 0
            return False
        self._mpv_play_retry_count = 0
        if not path_has_supported_audio_format(path):
            self._skip_track_after_playback_error(
                f"This file type is not supported:\n\n{path.name}\n\n"
                f"Supported formats: {supported_audio_formats_display()}\n\n"
                "Skipping to the next track.",
            )
            return False
        pos_sec = max(0.0, pos_sec)
        if self._duration_sec > 0:
            pos_sec = min(pos_sec, max(0.0, self._duration_sec - 0.05))
        cur_key = str(path.resolve())
        if self._mpv_loaded_audio_path == cur_key and self._mpv_ipc_sock is not None:
            # Replay / jump on the already-loaded file: seek only. Full loadfile+seek reload is slow
            # and can hit decoder-ready timeouts on long starts (e.g. 2:00 on long tracks).
            self._perf_log(f"mpv_reuse_loaded_seek_only pos={pos_sec:.3f}s")
            seek_ok = False
            for attempt in range(1, 6):
                seek_ok = self._mpv_seek_absolute(
                    pos_sec,
                    timeout=2.5,
                    perf_log=(attempt == 1),
                )
                if seek_ok:
                    break
                time.sleep(0.08 * attempt)
            if seek_ok:
                self._mpv_set_pause(False, timeout=1.25)
                self._mark_playback_started(pos_sec)
                return True
            self._perf_log("mpv_reuse_seek_failed_fallback_loadfile")
        if not self._mpv_loadfile(path, pos_sec, start_paused=False):
            self._perf_log("mpv_loadfile_failed")
            self._skip_track_after_playback_error(
                f"The player could not open this file:\n\n{path.name}\n\n"
                "It may be corrupt or in an unsupported codec.\n\n"
                "Skipping to the next track.",
            )
            return False
        return True

    def load_current_track(self, *, autoplay: bool = False) -> None:
        if self._autoplay_after_id is not None:
            try:
                self.root.after_cancel(self._autoplay_after_id)
            except tk.TclError:
                pass
            self._autoplay_after_id = None

        path = self._current_path()
        self._duration_sec = 0.0
        self._desired_start_sec = 0.0
        self._playback_start_label = "0:00"
        self._stop_playback()
        self._set_elapsed_seconds(0.0)
        self._set_total_seconds(0.0)
        self._set_playback_start_line("0:00")
        self.lbl_seek_hint.configure(text="")

        if path is None:
            self._jump_highlight_key = 0
            self._set_now_playing_main_plain("Done — all tracks reviewed")
            self.lbl_track.configure(text="")
            self.lbl_path.configure(text="")
            if self.lbl_source_folder is not None:
                self.lbl_source_folder.configure(text="")
            self.lbl_seek_hint.configure(text="")
            self._set_buttons_enabled(False)
            self._refresh_stats()
            self._draw_progress_bar(0.0)
            self._refresh_queue_panel()
            return

        if not path_has_supported_audio_format(path):
            self._skip_track_after_playback_error(
                f"This file type is not supported:\n\n{path.name}\n\n"
                f"Supported formats: {supported_audio_formats_display()}\n\n"
                "Skipping to the next track.",
            )
            return

        self._playout_handled = False
        self._mpv_progress_sync_counter = 0
        t_load0 = time.perf_counter()
        self.lbl_track.configure(text=f"Track {self._display_index} / {self._session_total}")
        src_line = ""
        if self._music_root is not None:
            sub = _source_folder_display(path, self._music_root)
            if sub:
                src_line = f"Source folder: {sub}"
        if self.lbl_source_folder is not None:
            self.lbl_source_folder.configure(text=src_line)
        self.lbl_path.configure(text=str(path))

        dur, title_disp, bpm_s, key_s = self._get_track_meta(path)
        self._duration_sec = dur
        self._apply_track_identity_header(title_disp, bpm_s, key_s)
        self._set_total_seconds(self._duration_sec)
        self._perf_log(f"track_load: {(time.perf_counter() - t_load0) * 1000.0:.1f}ms {path.name}")
        self._schedule_track_meta_preload()

        self._recompute_desired_start_and_highlight()
        self._audio_offset_in_source = self._desired_start_sec
        self._set_elapsed_seconds(self._desired_start_sec)
        self._draw_progress_bar(self._desired_start_sec)

        if not _which_mpv():
            self.lbl_seek_hint.configure(
                text="mpv is required for playback. Install with: brew install mpv",
            )
        else:
            self.lbl_seek_hint.configure(text="")

        self._refresh_stats()

        if self._folders:
            self._set_buttons_enabled(True)

        if autoplay:
            self._autoplay_after_id = self.root.after(8, self._run_scheduled_autoplay)

        self._refresh_all_widget_chrome()
        self._refresh_queue_panel()

    def _run_scheduled_autoplay(self) -> None:
        self._autoplay_after_id = None
        self._autoplay_current()

    def _autoplay_current(self) -> None:
        path = self._current_path()
        if path is None:
            return
        start = self._desired_start_sec
        self._play_from_position(path, start)

    def toggle_play(self) -> None:
        path = self._current_path()
        if path is None:
            return
        if not _which_mpv() or self._mpv_ipc_sock is None:
            try:
                messagebox.showinfo(
                    "mpv required",
                    "mpv is required for playback.\nInstall with:\n\nbrew install mpv",
                    parent=self.root,
                )
            except tk.TclError:
                pass
            return

        if self._is_playing:
            tp = self._mpv_get_time_pos()
            if tp is not None:
                pos = tp
                if self._duration_sec > 0:
                    pos = min(pos, self._duration_sec)
                self._audio_offset_in_source = pos
                self._mpv_set_playback_clock_anchor(pos)
            else:
                pos = self._audio_offset_in_source
            self._mpv_set_pause(True)
            self._is_playing = False
            self._set_elapsed_seconds(pos)
            self._set_play_button_text("Play")
            self._draw_progress_bar(pos)
            return

        if not path_has_supported_audio_format(path):
            self._skip_track_after_playback_error(
                f"This file type is not supported:\n\n{path.name}\n\n"
                f"Supported formats: {supported_audio_formats_display()}\n\n"
                "Skipping to the next track.",
            )
            return
        pos = self._audio_offset_in_source
        if self._duration_sec > 0:
            pos = min(max(0.0, pos), self._duration_sec)
        cur = str(path.resolve())
        if self._mpv_loaded_audio_path == cur:
            self._mpv_seek_absolute(pos)
            self._mpv_set_pause(False)
            self._mark_playback_started(pos)
            self._draw_progress_bar(self._current_position_in_source())
            return
        self._play_from_position(path, pos)
        self._draw_progress_bar(pos)

    def jump_to(self, sec: float, *, jump_ui_key: Optional[JumpUiKey] = None) -> None:
        if self._current_path() is None:
            return
        path = self._current_path()
        assert path is not None
        if not path_has_supported_audio_format(path):
            self._skip_track_after_playback_error(
                f"This file type is not supported:\n\n{path.name}\n\n"
                f"Supported formats: {supported_audio_formats_display()}\n\n"
                "Skipping to the next track.",
            )
            return
        sec = max(0.0, sec)
        if self._duration_sec > 0:
            sec = min(sec, self._duration_sec)
        if jump_ui_key is not None:
            self._jump_highlight_key = jump_ui_key
        was_playing = self._is_playing
        was_at_end = (
            self._duration_sec > 0
            and self._audio_offset_in_source >= self._duration_sec - 0.25
        )
        cur_key = str(path.resolve())
        same_file = (
            _which_mpv()
            and self._mpv_ipc_sock is not None
            and self._mpv_loaded_audio_path == cur_key
        )
        should_resume = was_playing or (
            not was_playing
            and was_at_end
            and self._duration_sec > 0
            and sec < self._duration_sec - 0.05
        )

        if same_file:
            self._mpv_seek_absolute(sec)
            self._audio_offset_in_source = sec
            self._set_elapsed_seconds(sec)
            if should_resume:
                self._mpv_set_pause(False)
                self._mark_playback_started(sec)
            else:
                self._mpv_set_pause(True)
                self._is_playing = False
                self._mpv_set_playback_clock_anchor(sec)
                self._set_play_button_text("Play")
        else:
            self._audio_offset_in_source = sec
            self._set_elapsed_seconds(sec)
            if should_resume:
                if not self._play_from_position(path, sec):
                    return
            else:
                if _which_mpv() and self._mpv_ipc_sock is not None:
                    if not self._mpv_loadfile(path, sec, start_paused=True):
                        self._skip_track_after_playback_error(
                            f"The player could not open this file:\n\n{path.name}\n\n"
                            "Skipping to the next track.",
                        )
                        return
                else:
                    self._is_playing = False
                    self._set_play_button_text("Play")

        self._draw_progress_bar(sec)
        self._refresh_all_widget_chrome()

    def jump_delta(self, delta: float) -> None:
        pos = self._audio_offset_in_source
        if self._is_playing:
            pos = self._current_position_in_source()
        self.jump_to(pos + delta)

    def _tick(self) -> None:
        self._refresh_progress()
        if self._music_root:
            self._refresh_session_stats()
        now = time.monotonic()
        if now - self._last_temp_prune_monotonic >= _SESSION_TEMP_PRUNE_INTERVAL_SEC:
            self._last_temp_prune_monotonic = now
            _prune_stale_bakkis_tempfiles()
        self.root.after(200, self._tick)

    def _draw_progress_bar(self, pos_sec: float) -> None:
        c = self.progress_canvas
        try:
            w = max(2, int(c.winfo_width()))
            h = max(2, int(c.winfo_height()))
        except tk.TclError:
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=self.trough, outline=self.trough, width=0)
        dur = self._duration_sec
        if dur > 0:
            pos_sec = max(0.0, min(float(pos_sec), dur))
            fill_w = int((pos_sec / dur) * w)
            if fill_w > 0:
                c.create_rectangle(0, 0, fill_w, h, fill=self.accent, outline=self.accent, width=0)
            r = max(3, min(6, h // 2 - 2))
            cx = max(r + 1, min(fill_w, w - r - 1))
            cy = h // 2
            c.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill=self.accent,
                outline=self.accent,
                width=0,
            )
        self._raise_time_text_above_scrubber()

    def _on_progress_canvas_configure(self, _event: object) -> None:
        if self._current_path() is None:
            self._draw_progress_bar(0.0)
        else:
            self._draw_progress_bar(self._current_position_in_source())
        self._raise_time_text_above_scrubber()

    def _on_progress_click(self, event: object) -> None:
        if self._current_path() is None or self._duration_sec <= 0:
            return
        w = int(self.progress_canvas.winfo_width())
        if w < 2:
            return
        x = int(getattr(event, "x", 0))
        frac = max(0.0, min(1.0, x / float(w)))
        self.jump_to(frac * self._duration_sec)

    def _autoplay_next_after_playout(self) -> None:
        """When the current file plays through to the end, advance like Skip (undo + stats) and autoplay."""
        if len(self._queue) <= 1 or not self._tiles_enabled:
            return
        self._perf_log("playout: autoplay next track")
        self.skip_current()

    def _refresh_progress(self) -> None:
        path = self._current_path()
        if path is None:
            self._draw_progress_bar(0.0)
            return

        if self._is_playing and self._mpv_ipc_sock is not None:
            self._mpv_progress_sync_counter += 1
            est = self._mpv_clock_anchor_pos + (time.monotonic() - self._mpv_clock_anchor_mono)
            near_end = self._duration_sec > 0 and est >= self._duration_sec - 1.25
            if (self._mpv_progress_sync_counter % 5 == 0) or near_end:
                tp = self._mpv_get_time_pos()
                if tp is not None:
                    self._mpv_set_playback_clock_anchor(tp)
                    self._audio_offset_in_source = tp

        pos = self._current_position_in_source()
        if self._duration_sec > 0:
            pos = min(pos, self._duration_sec)

        self._set_elapsed_seconds(pos)
        self._draw_progress_bar(pos)

    def sort_current(self, bucket: str) -> None:
        if not self._folders or not self._queue:
            return
        src = self._queue[0]
        prev_display = self._display_index
        dest_dir = self._folders[bucket]
        dest = _unique_destination(dest_dir, src.name)
        try:
            self._stop_playback()
            shutil.move(str(src), str(dest))
        except OSError as e:
            messagebox.showerror("Could not move file", str(e), parent=self.root)
            return

        self._undo_stack.append(
            {
                "kind": "move",
                "from": dest,
                "to": src.resolve(),
                "prev_display": prev_display,
                "session_bucket": bucket,
            },
        )
        if bucket == "keep":
            self._session_kept += 1
        elif bucket == "archive":
            self._session_archived += 1
        elif bucket == "favorites":
            self._session_favorites += 1
        self._refresh_session_stats()
        self._queue.pop(0)
        if self._queue:
            self._display_index += 1
        self.load_current_track(autoplay=True)

    def sort_current_genre(self, slot_index: int) -> None:
        if not self._music_root or not self._queue:
            return
        if slot_index < 0 or slot_index > 8:
            return
        src = self._queue[0]
        prev_display = self._display_index
        fn = self._genre_folder_names[slot_index] if slot_index < len(self._genre_folder_names) else safe_genre_folder_name(
            self._genre_labels[slot_index],
        )
        dest_dir = self._music_root / GENRES_NAME / safe_genre_folder_name(fn)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_destination(dest_dir, src.name)
        try:
            self._stop_playback()
            shutil.move(str(src), str(dest))
        except OSError as e:
            messagebox.showerror("Could not move file", str(e), parent=self.root)
            return

        self._undo_stack.append(
            {
                "kind": "move",
                "from": dest,
                "to": src.resolve(),
                "prev_display": prev_display,
                "session_bucket": "grid",
            },
        )
        self._session_genre += 1
        self._refresh_session_stats()
        self._queue.pop(0)
        if self._queue:
            self._display_index += 1
        self.load_current_track(autoplay=True)

    def skip_current(self) -> None:
        if not self._queue:
            return
        prev_display = self._display_index
        skipped = self._queue[0]
        self._stop_playback()
        self._queue.pop(0)
        if self._queue:
            self._display_index += 1
        self._undo_stack.append(
            {"kind": "skip", "path": skipped.resolve(), "prev_display": prev_display},
        )
        self._session_skipped += 1
        self._refresh_session_stats()
        self.load_current_track(autoplay=True)

    def undo_all_to_session_start(self) -> None:
        """Pop the entire undo stack (same as pressing Z once per action). Last step may autoplay."""
        if not self._undo_stack:
            return
        while len(self._undo_stack) > 1:
            n = len(self._undo_stack)
            self.undo_last(autoplay=False)
            if len(self._undo_stack) >= n:
                break
        if self._undo_stack:
            self.undo_last(autoplay=True)

    def undo_last(self, *, autoplay: bool = True) -> None:
        if not self._undo_stack:
            return
        item = self._undo_stack.pop()
        kind = item.get("kind")

        if kind == "skip":
            raw = item["path"]
            p = raw if isinstance(raw, Path) else Path(raw)
            p = p.resolve()
            if not p.is_file():
                messagebox.showerror(
                    "Could not undo",
                    f"The skipped file is no longer at:\n{p}",
                    parent=self.root,
                )
                self._undo_stack.append(item)
                return
            self._stop_playback()
            self._queue.insert(0, p)
            self._display_index = int(item["prev_display"])
            self._session_skipped = max(0, self._session_skipped - 1)
            self._refresh_session_stats()
            self.load_current_track(autoplay=autoplay)
            return

        if kind != "move":
            self._undo_stack.append(item)
            return

        src: Path = item["from"]
        dst: Path = item["to"]
        try:
            self._stop_playback()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as e:
            messagebox.showerror("Could not undo", str(e), parent=self.root)
            self._undo_stack.append(item)
            return

        self._queue.insert(0, dst.resolve())
        self._display_index = int(item["prev_display"])
        sb = item.get("session_bucket")
        if sb in ("keep", "archive", "favorites", "genre", "grid"):
            self._session_undo_bucket(sb)
        self.load_current_track(autoplay=autoplay)

    def _on_close(self) -> None:
        self._stop_playback()
        self._mpv_shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        app = App()
    except Exception as e:
        messagebox.showerror("Startup error", str(e))
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
