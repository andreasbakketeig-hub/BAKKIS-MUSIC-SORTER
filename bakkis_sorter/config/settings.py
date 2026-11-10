"""User settings defaults, normalization, and persistence."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .paths import (
    SETTINGS_JSON,
    _read_json,
    config_dir,
    write_json_atomic,
)
from .coercions import (
    DEFAULT_PLAYBACK_START_SEC,
    PLAYBACK_START_OPTIONS_SEC,
    coerce_bool,
    norm_mode,
)

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
        "create_dated_sorting_sessions": False,  # what new presets start with; each preset has its own "dated"
        "cleanup_empty_source_folders_after_sort": True,  # move mode only; undo recreates them
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
        "discover_in_topbar": True,  # Discover (listen) mode in the top bar
        "queue_filter": True,  # BPM / key filter in the queue drawer
        "colored_tiles": True,
        "game_controller": True,  # Xbox / PlayStation / Switch controllers as a remote, with rumble  # off: grid tiles look like swipe tiles (dark, colour stripe on top)
        "welcome_seen": False,
        "move_hint_seen": False,  # "drag the app to Applications", once  # the "why I built this" card, once on first launch
        "sorted_total": 0,  # tracks sorted across sessions (for the one-time support note)
        "support_nudged": False,
        "promo_duplicate_retention_days": 180,  # 0 means keep forever
        "promo_rating_favorite": 5,  # star rating sent for Feeling this (Up arrow)
        "promo_rating_keep": 4,  # star rating sent for Keep / Maybe (Right arrow)
        "promo_download_dir": "~/Downloads",  # destination folder for Trackstack promo downloads
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
        "discover_in_topbar",
        "queue_filter",
        "welcome_seen",
        "move_hint_seen",
        "support_nudged",
        "colored_tiles",
        "game_controller",
    ):
        if k in src:
            out[k] = coerce_bool(src[k])
    try:
        out["sorted_total"] = max(0, int(src.get("sorted_total", 0)))
    except (TypeError, ValueError):
        out["sorted_total"] = 0

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
    try:
        out["promo_duplicate_retention_days"] = max(0, int(src.get("promo_duplicate_retention_days", 180)))
    except (TypeError, ValueError):
        out["promo_duplicate_retention_days"] = 180
    try:
        out["promo_rating_favorite"] = max(1, min(5, int(src.get("promo_rating_favorite", 5))))
    except (TypeError, ValueError):
        out["promo_rating_favorite"] = 5
    try:
        out["promo_rating_keep"] = max(1, min(5, int(src.get("promo_rating_keep", 4))))
    except (TypeError, ValueError):
        out["promo_rating_keep"] = 4
    dl_dir = str(src.get("promo_download_dir") or "").strip()
    out["promo_download_dir"] = dl_dir if dl_dir else "~/Downloads"
    return out


# Kept by "Reset all settings": where the music is, what was already shown once, and the sort counter.
RESET_KEEPS = ("default_folder_path", "auto_load_default_folder", "welcome_seen", "guide_seen", "coach_done", "move_warning_acknowledged",
               "sorted_total", "support_nudged")


def reset_settings(current: dict) -> dict:
    """Default settings, keeping RESET_KEEPS from ``current``."""
    out = default_settings()
    for k in RESET_KEEPS:
        if k in current:
            out[k] = current[k]
    return out


def settings_path() -> Path:
    return config_dir() / SETTINGS_JSON


def load_settings(*, path: Optional[Path] = None, storage: Optional[Any] = None) -> dict:
    # 1. If explicit path given, read from disk
    if path is not None:
        return normalize_settings(_read_json(Path(path)))

    # 2. Try bakkis.db kv_store
    st = storage
    if st is None:
        try:
            from ..storage import get_storage

            st = get_storage()
        except Exception:
            pass

    if st is not None:
        try:
            saved = st.get_kv("settings")
            if isinstance(saved, dict):
                return normalize_settings(saved)
        except Exception:
            pass

    # 3. Fallback to settings.json and migrate to SQLite
    disk_data = normalize_settings(_read_json(settings_path()))
    if st is not None:
        try:
            st.put_kv("settings", disk_data)
        except Exception:
            pass
    return disk_data


def save_settings(data: dict, *, path: Optional[Path] = None, storage: Optional[Any] = None) -> dict:
    clean = normalize_settings(data)
    if path is not None:
        write_json_atomic(Path(path), clean)
        return clean

    st = storage
    if st is None:
        try:
            from ..storage import get_storage

            st = get_storage()
        except Exception:
            pass

    if st is not None:
        try:
            st.put_kv("settings", clean)
        except Exception:
            pass

    # Keep settings.json in sync for backward compatibility
    try:
        write_json_atomic(settings_path(), clean)
    except OSError:
        pass
    return clean


def repair_default_folder(settings: dict, home: Optional[Path] = None) -> bool:
    """Fix a default folder that points into another (old) user account, e.g. after a new Mac or a
    renamed account: try the same path under the current home, else forget it. Returns True if changed.

    A missing folder on an external disk (/Volumes/...) or in the current home is left alone; the
    app tells the user about it, which is useful there.
    """
    raw = settings.get("default_folder_path")
    if not raw:
        return False
    path = Path(str(raw)).expanduser()
    home = Path(home) if home is not None else Path.home()
    parts = path.parts
    if path.exists() or len(parts) < 3 or parts[1] != "Users" or Path(*parts[:3]) == home:
        return False
    moved = home.joinpath(*parts[3:])
    if moved.is_dir():
        settings["default_folder_path"] = str(moved)
    else:
        settings["default_folder_path"] = None
        settings["auto_load_default_folder"] = False
    return True


def patch_settings(**changes: object) -> dict:
    data = load_settings()
    data.update(changes)
    return save_settings(data)


# --------------------------------------------------------------------------- mode presets


