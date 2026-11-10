"""Paths, filesystem locations, and JSON I/O utilities."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from .. import APP_NAME

SETTINGS_JSON = "settings.json"
LEGACY_SETTINGS_JSON = "bakkis_settings.json"
MODE_PRESETS_JSON = "mode_presets.json"
GENRE_LABELS_JSON = "genre_labels.json"
MIGRATION_MARKER = ".migrated_from_v1"


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
    if v1_dir is not None:
        src = Path(v1_dir)
    else:  # v1 lives in legacy/ (it keeps its JSON next to its script); older checkouts had it at the root
        src = bundle_dir() / "legacy" if (bundle_dir() / "legacy").is_dir() else bundle_dir()
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


