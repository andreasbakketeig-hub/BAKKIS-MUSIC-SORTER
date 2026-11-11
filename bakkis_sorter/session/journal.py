"""Append-only audit journaling for sorting session actions."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from ..config import config_dir


class Journal:
    """Append-only audit log: records into bakkis.db and keeps <config>/journal/YYYY-MM-DD.jsonl for compatibility."""

    def __init__(self, directory: Optional[Path] = None, *, storage: Optional[Any] = None) -> None:
        self.directory = Path(directory) if directory is not None else config_dir() / "journal"
        self._storage = storage

    def _get_storage(self) -> Any:
        if self._storage is None:
            try:
                from ..storage import get_storage

                self._storage = get_storage()
            except Exception:
                pass
        return self._storage

    def path_for(self, day: date) -> Path:
        return self.directory / f"{day.isoformat()}.jsonl"

    def append(self, entry: dict) -> None:
        now = datetime.now()
        rec = {"ts": now.isoformat(timespec="seconds"), **entry}
        st = self._get_storage()
        if st is not None:
            try:
                st.log_journal_entry(rec)
            except Exception:
                pass

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(self.path_for(now.date()), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
