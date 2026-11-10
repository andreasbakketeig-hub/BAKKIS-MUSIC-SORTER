"""Comment pool management for Inflyte promo feedback."""

from __future__ import annotations

import random
import subprocess
import threading
import unicodedata
from pathlib import Path
from typing import Optional, Any

from ..config import bundle_dir, config_dir


def sanitize_comment(text: str, max_len: int = 500) -> str:
    """Normalize unicode, strip null bytes & control chars, preserve emojis/accents, and clamp length."""
    if not isinstance(text, str):
        return ""
    # 1. Replace illegal lone surrogates safely
    safe = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    # 2. Strip null bytes and non-printable control chars (preserve tabs/newlines/emojis)
    cleaned = "".join(c for c in safe if c == " " or (c != "\x00" and not (ord(c) < 32 and c not in "\t\n")))
    # 3. Unicode NFC composition
    normalized = unicodedata.normalize("NFC", cleaned).strip()
    # 4. Length clamping to respect API limits (max 500 chars)
    return normalized[:max_len]

COMMENTS_FILENAME = "promo_comments.txt"

DEFAULT_COMMENTS_TEXT = """# ============================================================================
# BAKKIS x Inflyte Auto-Feedback Comment Pool
#
# Lines under [5/5] are cycled randomly when you press ↑ (Feeling it -> 5/5).
# Lines under [4/5] are cycled randomly when you press → (Maybe / Keep -> 4/5).
# Blank lines and lines starting with # are ignored.
# ============================================================================

[5/5]
Groovy af
Absolute weapon, full support!
Feeling this one heavy, playing it out this weekend
Banger! Straight into the rotation
Huge groove on this, love it
Top tier release, will be rinsing this

[4/5]
Solid groove, will test this out
Nice vibe, downloading for my sets
Cool track, thanks for sending
Good energy, will give this a spin
Solid club tool, keen to try it out
Nice production, grabbing for the weekend
"""


def default_comments_path() -> Path:
    """Prefer promo_comments.txt in the repo/bundle root if writable, else in config_dir()."""
    repo_file = bundle_dir() / COMMENTS_FILENAME
    if repo_file.is_file():
        return repo_file
    cfg_file = config_dir() / COMMENTS_FILENAME
    if not cfg_file.exists():
        try:
            cfg_file.write_text(DEFAULT_COMMENTS_TEXT, encoding="utf-8")
        except OSError:
            pass
    return cfg_file


def parse_comments_text(text: str) -> dict[int, list[str]]:
    """Parse ``[5/5]`` and ``[4/5]`` sections from ``promo_comments.txt``."""
    pools: dict[int, list[str]] = {5: [], 4: []}
    unsectioned: list[str] = []
    current_rating: Optional[int] = None

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip().lower()
            if header in ("5/5", "5", "feeling it", "favorite", "favourite"):
                current_rating = 5
            elif header in ("4/5", "4", "maybe", "keep"):
                current_rating = 4
            else:
                current_rating = None
            continue
        if current_rating in (4, 5):
            pools[current_rating].append(line)
        else:
            unsectioned.append(line)

    if not pools[5] and unsectioned:
        pools[5] = list(unsectioned)
    if not pools[4] and unsectioned:
        pools[4] = list(unsectioned)
    if not pools[5]:
        pools[5] = ["Feeling this! Full support"]
    if not pools[4]:
        pools[4] = ["Solid track, will test out"]
    return pools


class CommentPool:
    """Thread-safe random comment selector backed by SQLite promo_comments table with file fallback."""

    def __init__(
        self,
        path: Optional[Path] = None,
        rng: Optional[random.Random] = None,
        storage: Optional[Any] = None,
    ) -> None:
        self._custom_path = path is not None
        self.path = Path(path) if path is not None else default_comments_path()
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._last: dict[int, Optional[str]] = {5: None, 4: None}
        self.storage = storage

    def load_pools(self) -> dict[int, list[str]]:
        if self.storage is not None:
            try:
                pools = self.storage.get_promo_comments()
                if pools.get(5) or pools.get(4):
                    return pools
            except Exception:
                pass
        elif not self._custom_path:
            try:
                from ..storage import get_storage
                store = get_storage()
                if store is not None:
                    pools = store.get_promo_comments()
                    if pools.get(5) or pools.get(4):
                        return pools
            except Exception:
                pass

        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            text = DEFAULT_COMMENTS_TEXT
        return parse_comments_text(text)

    def pick(self, rating: int) -> str:
        target = 5 if int(rating) >= 5 else 4
        pools = self.load_pools()
        candidates = pools.get(target) or pools[5]
        with self._lock:
            last = self._last.get(target)
            if len(candidates) > 1 and last in candidates:
                choices = [c for c in candidates if c != last]
            else:
                choices = list(candidates)
            chosen = self._rng.choice(choices)
            self._last[target] = chosen
            return chosen

    def open_in_editor(self) -> str:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(DEFAULT_COMMENTS_TEXT, encoding="utf-8")
        subprocess.Popen(["open", "-t", str(self.path)])
        return str(self.path)
