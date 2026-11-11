"""Unified logging subsystem for BAKKIS MUSIC SORTER.

Configures Python's standard logging to write structured events into bakkis.db (app_logs table)
via SQLiteLogHandler, with optional formatted console output in development mode.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Optional


class SQLiteLogHandler(logging.Handler):
    """Custom logging handler that records events into bakkis.db app_logs."""

    def __init__(self, level: int = logging.INFO, *, storage: Optional[Any] = None) -> None:
        super().__init__(level)
        self._storage = storage

    def _get_storage(self) -> Any:
        if self._storage is None:
            try:
                from .storage import get_storage

                self._storage = get_storage()
            except Exception:
                pass
        return self._storage

    def emit(self, record: logging.LogRecord) -> None:
        st = self._get_storage()
        if st is None:
            return

        try:
            exc_type = None
            tb_str = None
            if record.exc_info and record.exc_info[0]:
                exc_type = getattr(record.exc_info[0], "__name__", str(record.exc_info[0]))
                tb_str = "".join(traceback.format_exception(*record.exc_info))

            context = getattr(record, "context", None)
            context_str = json.dumps(context, default=str) if context else None

            st.record_log(
                level=record.levelname,
                module=record.name,
                message=record.getMessage(),
                exception_type=exc_type,
                traceback=tb_str,
                context=context_str,
                timestamp=record.created or time.time(),
            )
        except Exception:
            self.handleError(record)


_LOGGING_INITIALIZED = False


def get_logger(name: str = "bakkis_sorter") -> logging.Logger:
    """Return a logger for the given module name."""
    global _LOGGING_INITIALIZED
    if not _LOGGING_INITIALIZED:
        setup_logging()
    full_name = name if name.startswith("bakkis_sorter") else f"bakkis_sorter.{name}"
    return logging.getLogger(full_name)


def setup_logging(*, debug: Optional[bool] = None, storage: Optional[Any] = None) -> logging.Logger:
    """Initialize root logger with SQLiteLogHandler and optional console stream."""
    global _LOGGING_INITIALIZED
    root_logger = logging.getLogger("bakkis_sorter")

    # Avoid duplicate handlers on re-init
    if _LOGGING_INITIALIZED and root_logger.handlers:
        return root_logger

    is_debug = debug if debug is not None else bool(os.environ.get("BAKKIS_DEBUG"))
    root_logger.setLevel(logging.DEBUG if is_debug else logging.INFO)

    # 1. Attach SQLite Handler
    sql_handler = SQLiteLogHandler(level=logging.DEBUG if is_debug else logging.INFO, storage=storage)
    root_logger.addHandler(sql_handler)

    # 2. Attach Console Stream Handler if debugging or running interactively
    if is_debug or sys.stdout.isatty():
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.DEBUG if is_debug else logging.INFO)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        console.setFormatter(formatter)
        root_logger.addHandler(console)

    _LOGGING_INITIALIZED = True
    return root_logger
