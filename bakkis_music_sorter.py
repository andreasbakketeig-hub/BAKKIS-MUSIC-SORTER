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
JumpUiKey = Union[int, Literal["outro"]]
SortingMode = Literal["cleanup", "genre"]
TrackOrder = Literal["random", "in_order"]
from mutagen import File as MutagenFile
from tkinter import filedialog, messagebox

# Default ~1 : 1.1 (W:H), slightly taller than wide — fits full Genre Mode + shortcut panel without clipping.
_DEFAULT_WIN_W = 1180
_DEFAULT_WIN_H = 1300
_WIN_MIN_W = 1000
_WIN_MIN_H = 820

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


LONG_TRACK_SEC = 90.0  # longer than this → default playback starts at 2:00
DEFAULT_START_SEC = 120.0  # 2:00
KEEP_NAME = "KEEP"
ARCHIVE_NAME = "ARCHIVE"
FAVORITES_NAME = "FAVORITES"
GENRES_NAME = "GENRES"
SKIP_SCAN_DIRS = {KEEP_NAME.lower(), ARCHIVE_NAME.lower(), FAVORITES_NAME.lower(), GENRES_NAME.lower()}

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
    ("Yellow", "#FFD400"),
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


def _genre_labels_path() -> Path:
    return _user_config_dir() / "genre_labels.json"


def _app_settings_path() -> Path:
    return _user_config_dir() / "bakkis_settings.json"


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
    path = _app_settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            v = data.get("track_order", "random")
            if v in ("random", "in_order"):
                return v  # type: ignore[return-value]
    except (OSError, json.JSONDecodeError):
        pass
    return "random"


def save_track_order_setting(order: TrackOrder) -> None:
    path = _app_settings_path()
    data: dict = {}
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    data = raw
    except (OSError, json.JSONDecodeError):
        data = {}
    data["track_order"] = order
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_include_subfolders() -> bool:
    path = _app_settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "include_subfolders" in data:
            return bool(data["include_subfolders"])
    except (OSError, json.JSONDecodeError):
        pass
    return True


def save_include_subfolders_setting(on: bool) -> None:
    path = _app_settings_path()
    data: dict = {}
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    data = raw
    except (OSError, json.JSONDecodeError):
        data = {}
    data["include_subfolders"] = bool(on)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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


def ensure_genre_folders(root: Path, labels: list[str]) -> None:
    base = root / GENRES_NAME
    base.mkdir(parents=True, exist_ok=True)
    for lb in labels[:9]:
        (base / safe_genre_folder_name(lb)).mkdir(parents=True, exist_ok=True)


def iter_audio_files_under(folder: Path, *, recursive: bool = True):
    """Yield resolved audio file paths. When ``recursive`` is False, only the folder's top-level files."""
    root_folder = folder.resolve()
    if recursive:
        for root, dirs, files in os.walk(root_folder, topdown=True):
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in SKIP_SCAN_DIRS and not _should_skip_scan_subdirectory(d)
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


def scan_audio_files(folder: Path, *, recursive: bool = True) -> list[Path]:
    return list(iter_audio_files_under(folder, recursive=recursive))


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


def ensure_sort_folders(root: Path) -> dict[str, Path]:
    out = {
        "keep": root / KEEP_NAME,
        "archive": root / ARCHIVE_NAME,
        "favorites": root / FAVORITES_NAME,
    }
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


def _read_track_meta_uncached(path: Path) -> tuple[float, str]:
    """Duration + display title (filename); used for cache misses."""
    dur = 0.0
    try:
        info = MutagenFile(str(path))
        if info is not None and getattr(info, "info", None) is not None:
            dur = float(getattr(info.info, "length", 0.0) or 0.0)
    except Exception:
        pass
    return dur, path.name


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
        self.muted = "#A0A0A0"
        self.accent = "#00D6B4"
        self.teal_on_fg = "#000000"  # text on teal (Play, active jump, Keep tile)
        self.accent_btn_press = "#00B89A"
        self.accent_active = "#00C9A8"  # tile keyboard-flash highlight
        self.archive_bg = "#9E1B1B"
        self.archive_active = "#B32424"
        self.archive_fg = "#FFFFFF"
        self.favorite_bg = "#FFD400"
        self.favorite_active = "#FFDB33"
        self.favorite_on_fg = "#000000"
        self.warning = "#E8A838"
        self.text_on_accent = "#000000"
        self.skip_bg = "#171A1D"
        self.skip_active = "#222831"
        self.btn_dark_active = "#222831"
        self.btn_dark_active_fg = "#FFFFFF"
        self.disabled_fg = "#777777"

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

        self._sorting_mode: SortingMode = "cleanup"
        self._track_order: TrackOrder = load_track_order()
        self._var_include_subfolders = tk.BooleanVar(master=self.root, value=load_include_subfolders())
        self._path_rel_to_music_root: dict[str, str] = {}
        self._genre_labels, self._genre_colors = load_genre_config()
        self._genre_title_labels: list[tk.Label] = []
        self._genre_tile_inners: list[tk.Frame] = []
        self._tile_skip_inner_genre: Optional[tk.Frame] = None
        self._tile_undo_inner_genre: Optional[tk.Frame] = None
        self.cleanup_actions: Optional[tk.Frame] = None
        self.cleanup_outer: Optional[tk.Frame] = None
        self.genre_actions: Optional[tk.Frame] = None
        self._tile_mode_cleanup_inner: Optional[tk.Frame] = None
        self._tile_mode_genre_inner: Optional[tk.Frame] = None
        self._tile_order_random_inner: Optional[tk.Frame] = None
        self._tile_order_in_order_inner: Optional[tk.Frame] = None
        self.lbl_mode_header: Optional[tk.Label] = None
        self.lbl_source_folder: Optional[tk.Label] = None
        self.lbl_session_stats: Optional[tk.Label] = None
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
        self._jump_mouse_error_lbl: Optional[tk.Label] = None
        self._tinder_kb_jump_error_digit: Optional[int] = None
        self._tinder_kb_jump_error_plus30: bool = False
        # Which jump control shows teal (last preset used from UI/keyboard, or default on new track)
        self._jump_highlight_key: JumpUiKey = 0
        self._shortcut_panel_visible = True
        self.shortcut_frame: Optional[tk.Frame] = None
        self.footer_outer: Optional[tk.Frame] = None
        self._footer_logo_photo: Optional[object] = None
        self._load_folder_ring: Optional[tk.Frame] = None
        self._flash_after_ids: dict[int, str] = {}
        self._last_shortcut_keysym: Optional[str] = None
        self._last_shortcut_time: float = 0.0
        self._key_repeat_suppress_sec = 0.085

        self._track_meta_cache: dict[str, tuple[float, str]] = {}
        self._meta_cache_lock = threading.Lock()
        self._scan_id = 0
        self._scan_progress_queue: queue.Queue = queue.Queue()
        self._folder_scan_alive = False
        self._preload_session_id = 0
        self._autoplay_after_id: Optional[str] = None
        self._mpv_play_retry_count = 0

        self._build_ui()
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
        self.root.after(0, self._mpv_start_idle)
        self._tick()

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
        inner = max(420, rw - 80)
        try:
            self.lbl_title.configure(wraplength=inner)
            self.lbl_path.configure(wraplength=inner)
            if self.lbl_source_folder is not None:
                self.lbl_source_folder.configure(wraplength=inner)
        except tk.TclError:
            pass
        try:
            if self._sorting_mode == "genre":
                self.lbl_stats.configure(wraplength=inner)
            else:
                self.lbl_stats.configure(wraplength=0)
        except tk.TclError:
            pass
        try:
            self.lbl_mode_header.configure(wraplength=inner)
        except tk.TclError:
            pass
        try:
            if self.lbl_session_stats is not None:
                sw = max(200, min(420, (rw - 96) // 2))
                self.lbl_session_stats.configure(wraplength=sw)
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

    def _mpv_set_playback_clock_anchor(self, pos_sec: float) -> None:
        """Monotonic anchor so the UI can advance time without polling mpv every tick."""
        self._mpv_clock_anchor_pos = max(0.0, pos_sec)
        self._mpv_clock_anchor_mono = time.monotonic()
        self._mpv_cached_time_pos = self._mpv_clock_anchor_pos

    def _mpv_set_pause(self, paused: bool) -> bool:
        return self._mpv_command_ok(["set_property", "pause", paused], timeout=0.35)

    def _mpv_seek_absolute(self, sec: float) -> bool:
        t0 = time.perf_counter()
        ok = self._mpv_command_ok(["seek", sec, "absolute"], timeout=0.35)
        self._perf_log(f"mpv_jump_seek_ipc_ms={(time.perf_counter() - t0) * 1000.0:.2f} target={sec:.3f}s ok={ok}")
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

    def _get_track_meta(self, path: Path) -> tuple[float, str]:
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

    def _folder_scan_worker(self, root_folder: Path, sid: int, recursive: bool) -> None:
        t0 = time.perf_counter()
        paths: list[Path] = []
        n = 0
        last_prog = 0.0
        try:
            root_resolved = root_folder.resolve()
            for p in iter_audio_files_under(root_resolved, recursive=recursive):
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
                            self.lbl_title.configure(text="Load a folder to begin")
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
                    self._folders = ensure_sort_folders(root)
                    ensure_genre_folders(root, self._genre_labels)
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
                        self.lbl_title.configure(text="Load a folder to begin")
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

    def _style_jump_label_idle(self, lbl: tk.Label) -> None:
        """Inactive jump tile: same contrast idea as sort tiles (dark panel + light text)."""
        lbl.configure(
            bg=self.panel,
            fg=self.fg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.panel_border,
        )

    def _style_jump_label_active(self, lbl: tk.Label) -> None:
        """Selected preset / Outro: teal tile + black text (readable on macOS)."""
        glow = "#00F0DC"
        lbl.configure(
            bg=self.accent,
            fg=self.teal_on_fg,
            highlightthickness=2,
            highlightbackground=glow,
            highlightcolor=glow,
        )

    def _style_jump_label_disabled(self, lbl: tk.Label) -> None:
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
        if self._sorting_mode != "cleanup":
            return False
        return self._absolute_jump_past_track_end(target_sec)

    def _tinder_delta_plus_would_exceed(self) -> bool:
        """Keyboard + Tinder only: +30s would pass track end (genre keeps D key usable)."""
        if self._sorting_mode != "cleanup":
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
        except tk.TclError:
            pass
        try:
            self._apply_order_toggle_chrome()
        except tk.TclError:
            pass
        try:
            self._style_tk_button_transport_grey(self.btn_play)
            self._style_tk_button_transport_grey(self.btn_replay)
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
            self._style_tk_button_transport_grey(self.btn_edit_genres)
        except (tk.TclError, AttributeError):
            pass

    def _reapply_action_tiles(self) -> None:
        """Reassert Keep/Archive/Favorite/Skip/Undo colors (Frames + Labels, not affected by *Button option_db)."""
        genre_inners = list(self._genre_tile_inners)
        extras: list[Optional[tk.Frame]] = [
            self._tile_skip_inner_genre,
            self._tile_undo_inner_genre,
        ]
        for inner in (
            self._tile_keep_inner,
            self._tile_archive_inner,
            self._tile_favorite_inner,
            self._tile_skip_inner,
            self._tile_undo_inner,
            *genre_inners,
            *extras,
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
        self.lbl_playback_start.configure(
            text=f"Playback start: {time_part}",
            **self._time_display_label_kw(),
        )

    def _reapply_time_display_labels(self) -> None:
        """Reassert colors without changing text (dialogs / focus / periodic tick)."""
        kw = self._time_display_label_kw()
        for w in (self.lbl_elapsed, self.lbl_total, self.lbl_playback_start, self.lbl_track):
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

    def _set_play_button_text(self, text: str) -> None:
        """Changing only `text` can reset macOS Tk button colors; always re-chrome."""
        self.btn_play.configure(text=text)
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
        def on_press(_e: object) -> None:
            if not self._tiles_enabled:
                return
            if tinder_target_sec is not None and self._absolute_jump_past_track_end(float(tinder_target_sec)):
                if self._sorting_mode in ("cleanup", "genre"):
                    lbl._tinder_hold_invalid = True  # type: ignore[attr-defined]
                    self._jump_mouse_error_lbl = lbl
                    self._style_jump_label_error_hold(lbl)
                    return
            if tinder_delta_plus and self._delta_plus_past_track_end():
                if self._sorting_mode in ("cleanup", "genre"):
                    lbl._tinder_hold_invalid = True  # type: ignore[attr-defined]
                    self._jump_mouse_error_lbl = lbl
                    self._style_jump_label_error_hold(lbl)
                    return
            command()

        opt: dict = dict(
            text=base_text,
            font=("Helvetica", 13, "bold"),
            padx=12,
            pady=10,
            anchor="center",
            justify="center",
            cursor="",
            bd=0,
        )
        if width > 0:
            opt["width"] = width
        lbl = tk.Label(parent, **opt)
        lbl._jump_base_text = base_text  # type: ignore[attr-defined]
        lbl.bind("<ButtonPress-1>", on_press)
        if hint_suffix:

            def _hint_enter(_e: object) -> None:
                # In genre mode, keys 1–9 select genres — do not show those digits on jump tiles.
                if self._sorting_mode == "genre" and (hint_suffix or "").strip().isdigit():
                    return
                try:
                    lbl.configure(text=base_text + hint_suffix)
                except tk.TclError:
                    pass

            def _hint_leave(_e: object) -> None:
                try:
                    lbl.configure(text=base_text)
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
        compact: bool = False,
    ) -> tuple[tk.Frame, tk.Frame]:
        """Stacked icon / title / shortcut; centered; returns (outer, inner) for packing and flash."""
        outer = tk.Frame(parent, bg=self.bg, cursor="")
        inner = tk.Frame(
            outer,
            bg=bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            cursor="",
        )

        def on_click(_e: object) -> None:
            if not self._tiles_enabled and not clickable_when_tiles_disabled:
                return
            command()

        if compact:
            icon_font = ("Helvetica", 17, "bold")
            title_font = ("Helvetica", 10, "bold")
            shortcut_font = ("Helvetica", 9)
            pad_icon = (5, 2)
            pad_title = (0, 1) if show_icon else (7, 1)
            pad_shortcut = (0, 5)
        else:
            icon_font = ("Helvetica", 28, "bold")
            title_font = ("Helvetica", 13, "bold")
            shortcut_font = ("Helvetica", 11)
            pad_icon = (11, 4)
            pad_title = (0, 2) if show_icon else (12, 2)
            pad_shortcut = (1, 11)

        ic = tk.Label(
            inner,
            text=icon,
            bg=bg,
            fg=fg,
            font=icon_font,
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
            font=title_font,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        sc = tk.Label(
            inner,
            text=shortcut,
            bg=bg,
            fg=shortcut_fg,
            font=shortcut_font,
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        if show_icon:
            ic.pack(fill="x", pady=pad_icon)
        tl.pack(fill="x", pady=pad_title)
        sc.pack(fill="x", pady=pad_shortcut)
        bind_widgets = (outer, inner, tl, sc)
        if show_icon:
            for w in (outer, inner, ic, tl, sc):
                w.bind("<Button-1>", on_click)
        else:
            for w in bind_widgets:
                w.bind("<Button-1>", on_click)

        inner._tile_bg = bg  # type: ignore[attr-defined]
        inner._tile_active_bg = active_bg  # type: ignore[attr-defined]
        inner._tile_fg = fg  # type: ignore[attr-defined]
        inner._tile_shortcut_fg = shortcut_fg  # type: ignore[attr-defined]
        inner._tile_widgets = (ic, tl, sc) if show_icon else (tl, sc)  # type: ignore[attr-defined]
        inner.pack(fill="both", expand=True, padx=3, pady=0)
        return outer, inner

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
        """Genre tile: genre name on top (dominant), shortcut digit centered below."""
        outer = tk.Frame(parent, bg=self.bg, cursor="")
        inner = tk.Frame(
            outer,
            bg=bg,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            cursor="",
        )

        def on_click(_e: object) -> None:
            if not self._tiles_enabled:
                return
            command()

        tl = tk.Label(
            inner,
            text=genre_name.strip().upper(),
            bg=bg,
            fg=fg,
            font=("Helvetica", 12, "bold"),
            anchor="center",
            justify="center",
            wraplength=130,
            highlightthickness=0,
            cursor="",
        )
        tl.pack(fill="x", padx=4, pady=(11, 2))
        sc = tk.Label(
            inner,
            text=digit,
            bg=bg,
            fg=shortcut_fg,
            font=("Helvetica", 13, "bold"),
            anchor="center",
            justify="center",
            highlightthickness=0,
            cursor="",
        )
        sc.pack(fill="x", padx=4, pady=(1, 11))
        for w in (outer, inner, tl, sc):
            w.bind("<Button-1>", on_click)

        inner._tile_bg = bg  # type: ignore[attr-defined]
        inner._tile_active_bg = active_bg  # type: ignore[attr-defined]
        inner._tile_fg = fg  # type: ignore[attr-defined]
        inner._tile_shortcut_fg = shortcut_fg  # type: ignore[attr-defined]
        inner._tile_widgets = (tl, sc)  # type: ignore[attr-defined]
        inner._is_genre_tile = True  # type: ignore[attr-defined]
        inner.pack(fill="both", expand=True, padx=3, pady=0)
        return outer, inner

    def _set_action_tile_enabled(self, inner: tk.Frame, on: bool) -> None:
        widgets: tuple[tk.Label, ...] = getattr(inner, "_tile_widgets", ())
        if not widgets:
            return
        shortcut_w = widgets[-1]
        bg_n = str(getattr(inner, "_tile_bg", self.panel))
        fg_n = str(getattr(inner, "_tile_fg", self.fg))
        sc_fg = str(getattr(inner, "_tile_shortcut_fg", self.muted))
        undo_cleanup = getattr(self, "_tile_undo_inner", None)
        undo_genre = getattr(self, "_tile_undo_inner_genre", None)
        if self._undo_stack and (
            (undo_cleanup is not None and inner is undo_cleanup)
            or (undo_genre is not None and inner is undo_genre)
        ):
            on = True
        if on:
            inner.configure(cursor="", bg=bg_n)
            for w in widgets:
                w.configure(
                    bg=bg_n,
                    fg=fg_n if w is not shortcut_w else sc_fg,
                    highlightthickness=0,
                )
        else:
            inner.configure(cursor="", bg=self.trough)
            for w in widgets:
                w.configure(bg=self.trough, fg=self.disabled_fg, highlightthickness=0)

    def _toggle_shortcut_panel(self) -> None:
        if self.shortcut_frame is None or self.footer_outer is None:
            return
        self._shortcut_panel_visible = not self._shortcut_panel_visible
        if self._shortcut_panel_visible:
            self.shortcut_frame.pack(fill="x", padx=24, pady=(12, 8), before=self.footer_outer)
        else:
            self.shortcut_frame.pack_forget()
        self._refresh_all_widget_chrome()

    def _on_help_key(self, _e: object) -> Optional[str]:
        if not self._shortcuts_active():
            return None
        self._toggle_shortcut_panel()
        return "break"

    def _apply_dual_tile_selection_chrome(
        self,
        inner_left: tk.Frame,
        inner_right: tk.Frame,
        *,
        left_selected: bool,
    ) -> None:
        """Two compact tiles: left active = teal selection ring; same rules as TINDER / GENRE row."""
        for inner, active in (
            (inner_left, left_selected),
            (inner_right, not left_selected),
        ):
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
                inner.configure(bg=bg, highlightthickness=1, highlightbackground=hlb, highlightcolor=hlb)
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
                    w.configure(bg=bg, fg=fg_t if w is not shortcut_w else fg_sc)
                except tk.TclError:
                    pass

    def _apply_mode_switch_chrome(self) -> None:
        """Same skip-style tile chrome as the action row; active mode uses teal like Keep."""
        if self._tile_mode_cleanup_inner is None or self._tile_mode_genre_inner is None:
            return
        self._apply_dual_tile_selection_chrome(
            self._tile_mode_cleanup_inner,
            self._tile_mode_genre_inner,
            left_selected=(self._sorting_mode == "cleanup"),
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
        if self.lbl_mode_header is not None:
            self.lbl_mode_header.configure(
                text="MODE: TINDER" if mode == "cleanup" else "MODE: GENRE",
            )
        self._apply_mode_switch_chrome()
        if self.cleanup_outer is not None and self.genre_actions is not None:
            if mode == "cleanup":
                self.genre_actions.pack_forget()
                self.cleanup_outer.pack(fill="x", padx=24, pady=(0, 10))
            else:
                self.cleanup_outer.pack_forget()
                self.genre_actions.pack(fill="x", padx=24, pady=(0, 10))
        self._refresh_stats()
        self._refresh_session_stats()
        self._refresh_all_widget_chrome()

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

    def _open_edit_genres_dialog(self) -> None:
        self._prepare_native_dialog()
        top = tk.Toplevel(self.root)
        top.title("Edit genres")
        top.configure(bg=self.bg)
        try:
            top.transient(self.root)
        except tk.TclError:
            pass
        entries: list[tk.Entry] = []
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
                width=28,
                bg=self.panel,
                fg=self.fg,
                insertbackground=self.accent,
                highlightthickness=1,
                highlightbackground=self.panel_border,
                highlightcolor=self.accent,
                font=("Helvetica", 11),
            )
            e.insert(0, self._genre_labels[i])
            e.pack(side="left", fill="x", expand=True, padx=(8, 6))
            entries.append(e)

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
                color_vars[idx].set(_palette_name_for_hex(DEFAULT_GENRE_COLORS[idx]))

        def save_and_close() -> None:
            new_labels: list[str] = []
            new_colors: list[str] = []
            for j, ent in enumerate(entries):
                t = ent.get().strip()
                new_labels.append(t if t else DEFAULT_GENRE_LABELS[j])
                name = color_vars[j].get()
                new_colors.append(GENRE_PALETTE_NAME_TO_HEX.get(name, DEFAULT_GENRE_COLORS[j]))
            self._genre_labels = new_labels[:9]
            self._genre_colors = new_colors[:9]
            save_genre_config(self._genre_labels, self._genre_colors)
            if self._music_root is not None:
                ensure_genre_folders(self._music_root, self._genre_labels)
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

        top.minsize(720, 480)
        top.update_idletasks()
        tw = max(720, top.winfo_reqwidth())
        th = max(480, top.winfo_reqheight())
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
        x = max(0, (sw - tw) // 2)
        y = max(0, (sh - th) // 2)
        top.geometry(f"{tw}x{th}+{x}+{y}")

    def _skip_flash_widget(self) -> Optional[tk.Misc]:
        if self._sorting_mode == "genre" and self._tile_skip_inner_genre is not None:
            return self._tile_skip_inner_genre
        return self.btn_skip

    def _undo_flash_widget(self) -> Optional[tk.Misc]:
        if self._sorting_mode == "genre" and self._tile_undo_inner_genre is not None:
            return self._tile_undo_inner_genre
        return self.btn_undo

    def _kbd_skip(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self._flash_control(self._skip_flash_widget())
            self.skip_current()
            return "break"

        return handler

    def _kbd_undo(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self._flash_control(self._undo_flash_widget())
            self.undo_last()
            return "break"

        return handler

    def _kbd_undo_all(self, keysym: str) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._suppress_key_repeat(keysym):
                return "break"
            self._flash_control(self._undo_flash_widget())
            self.undo_all_to_session_start()
            return "break"

        return handler

    def _kbd_cleanup_sort(self, keysym: str, bucket: str, flash: tk.Misc) -> Callable[[object], Optional[str]]:
        def handler(_e: object) -> Optional[str]:
            if not self._shortcuts_active():
                return None
            if self._sorting_mode != "cleanup":
                return "break"
            if self._suppress_key_repeat(keysym):
                return "break"
            self._flash_control(flash)
            self.sort_current(bucket)
            return "break"

        return handler

    def _dispatch_digit_key(self, digit_1_to_9: int, _e: object) -> Optional[str]:
        if not self._shortcuts_active():
            return None
        if not self._tiles_enabled:
            return "break"
        if self._sorting_mode == "genre":
            self._tinder_kb_jump_error_digit = None
            gsym = f"GenreKey-{digit_1_to_9}"
            if self._suppress_key_repeat(gsym):
                return "break"
            idx = digit_1_to_9 - 1
            if 0 <= idx < len(self._genre_tile_inners):
                self._flash_control(self._genre_tile_inners[idx])
            self.sort_current_genre(idx)
            return "break"
        if digit_1_to_9 == 9:
            self._tinder_kb_jump_error_digit = None
            if self._suppress_key_repeat("9"):
                return "break"
            self._flash_control(self._jump_btn_outro)
            self.jump_to_outro()
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
        self._flash_control(btn)
        self.jump_to(float(sec), jump_ui_key=sec)
        return "break"

    def _build_ui(self) -> None:
        top = tk.Frame(self.root, bg=self.bg)
        top.pack(fill="x", padx=24, pady=(18, 10))
        # Equal side columns keep the title in the true horizontal center while resizing.
        top.columnconfigure(0, weight=1, uniform="top_hdr")
        top.columnconfigure(1, weight=0)
        top.columnconfigure(2, weight=1, uniform="top_hdr")

        top_left_col = tk.Frame(top, bg=self.bg)
        top_left_col.grid(row=0, column=0, sticky="nw")
        load_row = tk.Frame(top_left_col, bg=self.bg)
        load_row.pack(anchor="w", fill="x")
        self._load_folder_ring, self.btn_load = self._load_folder_button(load_row, self.pick_folder)
        self._load_folder_ring.pack(side="left")
        self.chk_include_subfolders = tk.Checkbutton(
            load_row,
            text="Include subfolders",
            variable=self._var_include_subfolders,
            command=self._on_include_subfolders_toggle,
            bg=self.bg,
            fg=self.fg,
            selectcolor=self.panel,
            activebackground=self.bg,
            activeforeground=self.fg,
            highlightthickness=0,
            highlightbackground=self.bg,
            font=("Helvetica", 10),
        )
        self.chk_include_subfolders.pack(side="left", padx=(14, 0), anchor="w")

        order_bar = tk.Frame(top_left_col, bg=self.bg)
        order_bar.pack(anchor="w", pady=(10, 0), fill="x")
        tk.Label(order_bar, text="Order:", bg=self.bg, fg=self.muted, font=("Helvetica", 10, "bold")).pack(
            side="left",
            padx=(0, 8),
        )
        order_tiles = tk.Frame(order_bar, bg=self.bg)
        order_tiles.pack(side="left", fill="x", expand=True)
        o_rnd, self._tile_order_random_inner = self._action_tile(
            order_tiles,
            "🔀",
            "RANDOM",
            "Shuffle",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._on_pick_track_order("random"),
            clickable_when_tiles_disabled=True,
            compact=True,
        )
        o_io, self._tile_order_in_order_inner = self._action_tile(
            order_tiles,
            "📋",
            "IN ORDER",
            "A–Z path",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._on_pick_track_order("in_order"),
            clickable_when_tiles_disabled=True,
            compact=True,
        )
        o_rnd.pack(side="left", padx=(0, 6), fill="x", expand=True)
        o_io.pack(side="left", fill="x", expand=True)
        self._apply_order_toggle_chrome()

        title_mid = tk.Frame(top, bg=self.bg)
        title_mid.grid(row=0, column=1, sticky="")
        titles = tk.Frame(title_mid, bg=self.bg)
        titles.pack()
        tk.Label(titles, text="BAKKIS", bg=self.bg, fg=self.fg, font=("Helvetica", 20, "bold")).pack(side="left")
        tk.Label(titles, text=" ", bg=self.bg, font=("Helvetica", 5)).pack(side="left")
        tk.Label(titles, text="MUSIC SORTER", bg=self.bg, fg=self.accent, font=("Helvetica", 20, "bold")).pack(side="left")

        top_right_col = tk.Frame(top, bg=self.bg)
        top_right_col.grid(row=0, column=2, sticky="ne")
        right_stack = tk.Frame(top_right_col, bg=self.bg)
        right_stack.pack(anchor="ne")
        mode_row = tk.Frame(right_stack, bg=self.bg)
        mode_row.pack(anchor="e", fill="x")
        o_mc, self._tile_mode_cleanup_inner = self._action_tile(
            mode_row,
            "💘",
            "TINDER",
            "Swipe row",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._set_sorting_mode("cleanup"),
            clickable_when_tiles_disabled=True,
            compact=True,
        )
        o_mg, self._tile_mode_genre_inner = self._action_tile(
            mode_row,
            "🎵",
            "GENRE",
            "Nine tiles",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            lambda: self._set_sorting_mode("genre"),
            clickable_when_tiles_disabled=True,
            compact=True,
        )
        o_mc.pack(side="left", padx=(8, 6), fill="x", expand=True)
        o_mg.pack(side="left", fill="x", expand=True)

        self._apply_mode_switch_chrome()

        self.lbl_session_stats = tk.Label(
            right_stack,
            text="",
            bg=self.bg,
            fg=self.muted,
            font=("Helvetica", 9),
            justify="right",
            anchor="e",
            wraplength=320,
        )
        self.lbl_session_stats.pack(anchor="e", pady=(8, 0), fill="x")

        status_above_progress = tk.Frame(self.root, bg=self.bg)
        status_above_progress.pack(fill="x", padx=24, pady=(2, 6))
        self.lbl_mode_header = tk.Label(
            status_above_progress,
            text="MODE: TINDER",
            bg=self.bg,
            fg=self.accent,
            font=("Helvetica", 12, "bold"),
            anchor="center",
            justify="center",
        )
        self.lbl_mode_header.pack(fill="x")
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
        self.lbl_stats.pack(fill="x", pady=(4, 0))

        progress_row = tk.Frame(self.root, bg=self.bg)
        progress_row.pack(fill="x", padx=24, pady=(0, 6))

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
            height=32,
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

        self.lbl_playback_start = self._readonly_time_label(
            self.root,
            text="Playback start: 0:00",
            font=("Helvetica", 12, "bold"),
            width=None,
            anchor="center",
        )
        self.lbl_playback_start.pack(fill="x", pady=(0, 4))

        self.root.after_idle(self._raise_time_text_above_scrubber)

        self.lbl_title = tk.Label(
            self.root,
            text="Load a folder to begin",
            bg=self.bg,
            fg=self.fg,
            font=("Helvetica", 22, "bold"),
            wraplength=1100,
            justify="center",
            anchor="center",
        )
        self.lbl_title.pack(fill="x", pady=(0, 4))

        self.lbl_track = self._readonly_time_label(
            self.root,
            text="",
            font=("Helvetica", 12, "bold"),
            width=None,
            anchor="center",
        )
        self.lbl_track.pack(fill="x", pady=(0, 2))

        self.lbl_source_folder = tk.Label(
            self.root,
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
            self.root,
            text="",
            bg=self.bg,
            fg=self.muted,
            wraplength=1100,
            justify="center",
            font=("Helvetica", 11),
        )
        self.lbl_path.pack(fill="x", padx=24, pady=(0, 8))

        jump = tk.Frame(self.root, bg=self.bg)
        jump.pack(fill="x", padx=24, pady=(0, 6))
        jl = tk.Label(jump, text="Jump to", bg=self.bg, fg=self.muted, font=("Helvetica", 10, "bold"))
        jl.pack(anchor="w", pady=(0, 6))

        row1 = tk.Frame(jump, bg=self.bg)
        row1.pack(fill="x")
        for base, hint, sec in [
            ("⏱  0:00", "  1", 0),
            ("⏱  0:30", "  2", 30),
            ("⏱  1:00", "  3", 60),
            ("⏱  1:30", "  4", 90),
            ("⏱  2:00", "  5", 120),
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
            b.pack(side="left", padx=(0, 6), fill="x", expand=True)

        row2_presets = tk.Frame(jump, bg=self.bg)
        row2_presets.pack(fill="x", pady=(8, 0))
        for base, hint, sec in [
            ("⏱  3:00", "  6", 180),
            ("⏱  4:00", "  7", 240),
            ("⏱  5:00", "  8", 300),
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
            b.pack(side="left", padx=(0, 6), fill="x", expand=True)
        b_out = self._jump_time_button(row2_presets, "⏱  Outro", self.jump_to_outro, hint_suffix="  9", width=0)
        self._jump_buttons.append(b_out)
        self._jump_btn_outro = b_out
        b_out.pack(side="left", padx=(0, 0), fill="x", expand=True)

        row2 = tk.Frame(jump, bg=self.bg)
        row2.pack(fill="x", pady=(8, 0))
        b_m = self._jump_time_button(row2, "←  ⏱  −30s", lambda: self.jump_delta(-30), hint_suffix="  A", width=16)
        self._jump_buttons.append(b_m)
        self._jump_btn_minus = b_m
        b_m.pack(side="left", padx=(0, 6), fill="x", expand=True)
        b_p = self._jump_time_button(
            row2,
            "⏱  +30s  →",
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
            font=("Helvetica", 11),
        )
        self.lbl_seek_hint.pack(anchor="w", pady=(8, 0))

        transport = tk.Frame(self.root, bg=self.bg)
        transport.pack(fill="x", padx=24, pady=(8, 10))
        self.btn_play = self._dj_button(transport, "Play", self.toggle_play, kind="transport", width=12, padx=14, pady=10)
        self.btn_play.pack(side="left")
        self.btn_replay = self._dj_button(
            transport,
            "Replay",
            self._replay_active_jump_point,
            kind="transport",
            width=10,
            padx=12,
            pady=10,
        )
        self.btn_replay.pack(side="left", padx=(10, 0))

        self.cleanup_outer = tk.Frame(self.root, bg=self.bg)
        self.cleanup_outer.pack(fill="x", padx=24, pady=(0, 10))
        self.cleanup_actions = tk.Frame(self.cleanup_outer, bg=self.bg)
        self.cleanup_actions.pack(fill="x")

        o_k, self._tile_keep_inner = self._action_tile(
            self.cleanup_actions,
            "😊",
            "KEEP",
            "→",
            self.accent,
            self.accent_active,
            self.teal_on_fg,
            "#1A1A1A",
            lambda: self.sort_current("keep"),
        )
        o_k.pack(side="left", fill="both", expand=True)
        o_a, self._tile_archive_inner = self._action_tile(
            self.cleanup_actions,
            "☹️",
            "ARCHIVE",
            "←",
            self.archive_bg,
            self.archive_active,
            self.archive_fg,
            "#FFCCCC",
            lambda: self.sort_current("archive"),
        )
        o_a.pack(side="left", fill="both", expand=True)
        o_f, self._tile_favorite_inner = self._action_tile(
            self.cleanup_actions,
            "🤩",
            "FAVORITE",
            "↑",
            self.favorite_bg,
            self.favorite_active,
            self.favorite_on_fg,
            "#333333",
            lambda: self.sort_current("favorites"),
        )
        o_f.pack(side="left", fill="both", expand=True)
        o_s, self._tile_skip_inner = self._action_tile(
            self.cleanup_actions, "»", "SKIP", "Enter", self.skip_bg, self.skip_active, self.fg, self.muted, self.skip_current
        )
        o_s.pack(side="left", fill="both", expand=True)
        o_u, self._tile_undo_inner = self._action_tile(
            self.cleanup_actions,
            "↶",
            "UNDO",
            "Z",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self.undo_last,
            clickable_when_tiles_disabled=True,
        )
        o_u.pack(side="left", fill="both", expand=True)

        # Aliases for keyboard flash (tile inner frames)
        self.btn_keep = self._tile_keep_inner  # type: ignore[assignment]
        self.btn_archive = self._tile_archive_inner  # type: ignore[assignment]
        self.btn_favorite = self._tile_favorite_inner  # type: ignore[assignment]
        self.btn_skip = self._tile_skip_inner  # type: ignore[assignment]
        self.btn_undo = self._tile_undo_inner  # type: ignore[assignment]

        self.genre_actions = tk.Frame(self.root, bg=self.bg)
        genre_row_top = tk.Frame(self.genre_actions, bg=self.bg)
        genre_row_top.pack(fill="x")
        genre_row_bot = tk.Frame(self.genre_actions, bg=self.bg)
        genre_row_bot.pack(fill="x", pady=(6, 0))
        self._genre_tile_inners.clear()
        self._genre_title_labels.clear()
        for i in range(9):
            digit = str(i + 1)
            parent_row = genre_row_top if i < 5 else genre_row_bot
            name = self._genre_labels[i]
            hx = self._genre_colors[i] if i < len(self._genre_colors) else DEFAULT_GENRE_COLORS[i]
            act = self._genre_tile_active_bg(hx)
            fg_m = _fg_for_genre_bg(hx)
            sc_m = _genre_shortcut_fg(fg_m)
            o_g, inn_g = self._genre_action_tile(
                parent_row,
                name,
                digit,
                hx,
                act,
                fg_m,
                sc_m,
                lambda idx=i: self.sort_current_genre(idx),
            )
            o_g.pack(side="left", fill="both", expand=True)
            tw = getattr(inn_g, "_tile_widgets", ())
            if tw and isinstance(tw[0], tk.Label):
                self._genre_title_labels.append(tw[0])
            self._genre_tile_inners.append(inn_g)
        self._apply_genre_tile_colors()

        genre_bot = tk.Frame(self.genre_actions, bg=self.bg)
        genre_bot.pack(fill="x", pady=(10, 0))
        o_sg, self._tile_skip_inner_genre = self._action_tile(
            genre_bot,
            "»",
            "SKIP",
            "Enter",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self.skip_current,
        )
        o_sg.pack(side="left", fill="both", expand=True)
        o_ug, self._tile_undo_inner_genre = self._action_tile(
            genre_bot,
            "↶",
            "UNDO",
            "Z",
            self.skip_bg,
            self.skip_active,
            self.fg,
            self.muted,
            self.undo_last,
            clickable_when_tiles_disabled=True,
        )
        o_ug.pack(side="left", fill="both", expand=True, padx=(6, 0))
        tk.Frame(genre_bot, bg=self.bg).pack(side="left", fill="x", expand=True)
        self.btn_edit_genres = tk.Button(
            genre_bot,
            text="Edit Genres",
            command=self._open_edit_genres_dialog,
            font=("Helvetica", 11, "bold"),
            padx=14,
            pady=10,
        )
        self._style_tk_button_transport_grey(self.btn_edit_genres)
        self.btn_edit_genres.pack(side="right")
        self.genre_actions.pack_forget()

        self.shortcut_frame = self._build_shortcut_guide_panel(self.root)
        self.footer_outer = tk.Frame(self.root, bg=self.bg)
        self.footer_outer.pack(fill="x", padx=0, pady=(0, 6))
        self._build_branding_footer(self.footer_outer)
        if self._shortcut_panel_visible:
            self.shortcut_frame.pack(fill="x", padx=24, pady=(12, 8), before=self.footer_outer)

    def _build_shortcut_guide_panel(self, parent: tk.Misc) -> tk.Frame:
        outer = tk.Frame(parent, bg=self.panel, highlightthickness=1, highlightbackground=self.panel_border)
        inner_pad = tk.Frame(outer, bg=self.panel)
        inner_pad.pack(fill="both", expand=True, padx=16, pady=14)
        hdr = ("Helvetica", 13, "bold")
        body = ("Helvetica", 12)
        meta = ("Helvetica", 10)
        grid = tk.Frame(inner_pad, bg=self.panel)
        grid.pack(fill="x")
        specs = [
            (
                "▶ TINDER",
                [
                    "😊  Keep (→)",
                    "☹️  Archive (←)",
                    "🤩  Favorite (↑)",
                    "Enter  Skip",
                    "Z  Undo last step",
                    "⌃Z  Undo all (back to folder load)",
                ],
            ),
                (
                "♪ PLAYBACK",
                [
                    "Space  Play / Pause",
                    "↓  Replay from playback start",
                    "R  Replay active jump (preset / Outro / default start)",
                ],
            ),
            (
                "⏱ JUMP TO",
                [
                    "Tinder mode: 1–9 jump",
                    "1–5  → 0:00 … 2:00",
                    "6–8  → 3:00 … 5:00",
                    "9  → Outro (last 60s)",
                    "5  = 2:00 (long-track default)",
                    "Genre mode: use mouse here",
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
                "◆ GENRE MODE",
                [
                    "Switch: Tinder / Genre tabs",
                    "1–9  Sort to genre folder",
                    "Enter  Skip · Z  Undo",
                    "⌃Z  Undo all",
                    "Edit Genres saves labels + colors",
                ],
            ),
        ]
        for col, (title, lines) in enumerate(specs):
            col_f = tk.Frame(grid, bg=self.panel)
            col_f.grid(row=0, column=col, sticky="nw", padx=(0, 14), pady=0)
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
                ).pack(anchor="w", pady=(4, 0))
        for c in range(5):
            grid.columnconfigure(c, weight=1, uniform="help")
        tk.Label(
            inner_pad,
            text="Press H to hide or show this guide",
            bg=self.panel,
            fg=self.muted,
            font=meta,
        ).pack(anchor="w", pady=(12, 0))
        return outer

    def _build_branding_footer(self, parent: tk.Misc) -> None:
        footer = tk.Frame(parent, bg=self.bg, highlightthickness=0, cursor="")
        footer.pack(fill="x", padx=24, pady=(4, 8))

        rule = tk.Frame(footer, height=1, bg=self.panel_border)
        rule.pack(fill="x", pady=(6, 8))

        logo_path = _bundle_dir() / "bakkislogo.png"
        if logo_path.is_file():
            try:
                from PIL import Image, ImageTk

                img = Image.open(logo_path).convert("RGBA")
                h = max(24, min(56, img.height))
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

        if btn is self.btn_play or btn is self.btn_replay:
            t = self.time_text_fg
            btn.configure(
                bg=self.transport_grey_active,
                fg=t,
                activebackground=self.transport_grey_active,
                activeforeground=t,
            )
        else:
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
        if not self._tiles_enabled:
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
            if flash is not None:
                self._flash_control(flash)
            fn()
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
            self._flash_control(self._jump_btn_plus)
            self.jump_delta(30)
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
            self._flash_control(self.btn_replay)
            self._replay_active_jump_point()
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
        state = "normal" if on else "disabled"
        # Load folder must stay enabled so users can pick a folder when the queue is empty or finished.
        self.btn_load.configure(state="normal")
        self.btn_play.configure(state=state)
        self.btn_replay.configure(state=state)
        try:
            self.btn_edit_genres.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
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
            d = base_gen / safe_genre_folder_name(lab)
            n = _count_files_in_dir(d) if d.is_dir() else 0
            parts.append(f"{lab}: {n}")
        nq = len(self._queue)
        return " · ".join(parts) + f"   ·   Queue: {nq}"

    def _refresh_stats(self) -> None:
        if not self._music_root or not self._folders:
            self.lbl_stats.configure(text="")
            self._update_content_wraplengths()
            return
        if self._sorting_mode == "genre":
            self.lbl_stats.configure(text=self._genre_stats_text())
        else:
            k = _count_files_in_dir(self._folders["keep"])
            a = _count_files_in_dir(self._folders["archive"])
            f = _count_files_in_dir(self._folders["favorites"])
            base = f"Kept: {k}   Archived: {a}   Favorites: {f}"
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
        if self._sorting_mode == "genre":
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
        elif bucket == "genre":
            self._session_genre = max(0, self._session_genre - 1)
        self._refresh_session_stats()

    def _cleanup_temp_audio(self) -> None:
        """Legacy hook (ffmpeg temp WAVs); mpv playback leaves no preview temps."""

    def _on_include_subfolders_toggle(self) -> None:
        save_include_subfolders_setting(bool(self._var_include_subfolders.get()))

    def pick_folder(self) -> None:
        try:
            self._prepare_native_dialog()
            folder = filedialog.askdirectory(title="Choose music folder", parent=self.root)
            if not folder:
                return
            root = Path(folder).resolve()
            self._scan_id += 1
            sid = self._scan_id
            self._stop_playback()
            try:
                self.lbl_title.configure(text="Scanning…")
                self.lbl_track.configure(text="Scanning folders… 0 tracks")
                self.lbl_path.configure(text=str(root))
                if self.lbl_source_folder is not None:
                    self.lbl_source_folder.configure(text="")
            except tk.TclError:
                pass
            self._folder_scan_alive = True
            recursive = bool(self._var_include_subfolders.get())
            threading.Thread(
                target=self._folder_scan_worker,
                args=(root, sid, recursive),
                daemon=True,
            ).start()
            self._poll_scan_progress()
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
        opts = f"start={pos_sec:.6f}"
        if start_paused:
            opts += ",pause=yes"
        t0 = time.perf_counter()
        ok = self._mpv_command_ok(["loadfile", str(path), "replace", opts], timeout=3.5)
        self._perf_log(
            f"mpv_track_load_ipc_ms={(time.perf_counter() - t0) * 1000.0:.2f} "
            f"file={path.name!r} start={pos_sec:.3f}s paused={start_paused} ok={ok}",
        )
        if ok:
            self._mpv_loaded_audio_path = str(path.resolve())
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
            self.lbl_title.configure(text="Done — all tracks reviewed")
            self.lbl_track.configure(text="")
            self.lbl_path.configure(text="")
            if self.lbl_source_folder is not None:
                self.lbl_source_folder.configure(text="")
            self.lbl_seek_hint.configure(text="")
            self._set_buttons_enabled(False)
            self._refresh_stats()
            self._draw_progress_bar(0.0)
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

        dur, title_disp = self._get_track_meta(path)
        self._duration_sec = dur
        self.lbl_title.configure(text=title_disp)
        self._set_total_seconds(self._duration_sec)
        self._perf_log(f"track_load: {(time.perf_counter() - t_load0) * 1000.0:.1f}ms {path.name}")
        self._schedule_track_meta_preload()

        if self._duration_sec > LONG_TRACK_SEC:
            self._desired_start_sec = DEFAULT_START_SEC
            self._playback_start_label = format_sec(DEFAULT_START_SEC)
        else:
            self._desired_start_sec = 0.0
            self._playback_start_label = "0:00"

        self._set_playback_start_line(self._playback_start_label)
        self._audio_offset_in_source = self._desired_start_sec
        self._set_elapsed_seconds(self._desired_start_sec)
        self._draw_progress_bar(self._desired_start_sec)

        outro_sec = self._outro_jump_seconds()
        if self._duration_sec >= 3.0 and abs(self._desired_start_sec - outro_sec) <= 0.75:
            if (
                abs(self._desired_start_sec - DEFAULT_START_SEC) <= 0.75
                and self._duration_sec > LONG_TRACK_SEC
            ):
                self._jump_highlight_key = int(round(self._desired_start_sec))
            else:
                self._jump_highlight_key = "outro"
        else:
            self._jump_highlight_key = int(round(self._desired_start_sec))

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
        name = self._genre_labels[slot_index]
        src = self._queue[0]
        prev_display = self._display_index
        dest_dir = self._music_root / GENRES_NAME / safe_genre_folder_name(name)
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
                "session_bucket": "genre",
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
        if sb in ("keep", "archive", "favorites", "genre"):
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
