"""Diagnostics and logging API mixin for AppBackend."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .. import __version__

if TYPE_CHECKING:
    from ..storage import EncryptedStorage


class DiagnosticsApiMixin:
    """API methods for system logs, SQLite stats, and diagnostic reporting."""

    if TYPE_CHECKING:
        storage: EncryptedStorage

    def api_get_logs(
        self,
        min_level: str = "INFO",
        limit: int = 100,
        offset: int = 0,
        module: Optional[str] = None,
        level: Optional[str] = None,
    ) -> list[dict]:
        """Fetch system and error diagnostics logs from bakkis.db."""
        return self.storage.get_logs(min_level=min_level, limit=limit, offset=offset, module=module, level=level)

    def api_clear_logs(self) -> dict:
        """Purge system logs from bakkis.db."""
        self.storage.clear_logs()
        return {"status": "ok"}

    def api_get_db_stats(self) -> dict:
        """Get database size, freelist statistics, and table row counts from bakkis.db."""
        return self.storage.get_database_stats()

    def api_vacuum_db(self) -> dict:
        """Defragment bakkis.db and return reclaimed bytes and refreshed stats."""
        res = self.storage.vacuum()
        res["stats"] = self.storage.get_database_stats()
        return res

    def api_get_diagnostic_bundle(self) -> dict:
        """Build an anonymized diagnostic report of system status and recent errors, copying to clipboard."""
        stats = self.storage.get_database_stats()
        err_logs = self.storage.get_logs(min_level="WARNING", limit=25)

        user_home = str(Path.home())

        def mask_paths(text: str) -> str:
            if not text:
                return ""
            return text.replace(user_home, "~")

        size_mb = stats["total_size_bytes"] / (1024 * 1024)
        reclaim_kb = stats["reclaimable_bytes"] / 1024

        lines = [
            "# BAKKIS MUSIC SORTER — Diagnostic Report",
            f"Generated: {platform.node()} ({platform.system()})",
            f"App Version: {__version__}",
            f"macOS: {platform.mac_ver()[0]} ({platform.machine()})",
            f"Python: {platform.python_version()}",
            f"Database: {size_mb:.2f} MB ({reclaim_kb:.1f} KB reclaimable)",
            "Table Counts:",
        ]
        for tbl, count in sorted(stats.get("counts", {}).items()):
            lines.append(f"  - {tbl}: {count:,}")

        lines.append("")
        lines.append(f"## Recent Warnings & Exceptions ({len(err_logs)} total)")
        if not err_logs:
            lines.append("No warnings or errors logged in recent activity.")
        else:
            for log in err_logs:
                lines.append(f"### [{log['level']}] {log['module']} @ {log['created_at']}")
                lines.append(f"Message: {mask_paths(log['message'])}")
                if log.get("exception_type"):
                    lines.append(f"Exception Type: {log['exception_type']}")
                if log.get("traceback"):
                    tb = mask_paths(log["traceback"].strip())
                    lines.append("```text")
                    lines.append(tb)
                    lines.append("```")
                lines.append("")

        report_text = "\n".join(lines)
        self._copy_to_clipboard(report_text)
        return {"report": report_text, "copied": True}

    def _copy_to_clipboard(self, text: str) -> None:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
