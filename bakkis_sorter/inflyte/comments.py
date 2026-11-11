"""Backwards-compatibility shim for bakkis_sorter.inflyte.comments."""

from ..promos.comments import (
    COMMENTS_FILENAME,
    DEFAULT_COMMENTS_TEXT,
    CommentPool,
    default_comments_path,
    parse_comments_text,
)

__all__ = [
    "COMMENTS_FILENAME",
    "DEFAULT_COMMENTS_TEXT",
    "CommentPool",
    "default_comments_path",
    "parse_comments_text",
]
