"""Targeted unit tests for copy-mode index tracking and file filtering."""

from pathlib import Path
import pytest

from bakkis_sorter.session.copy_index import (
    filter_already_copied,
    load_copied_index,
    save_copied_index,
)


def test_copy_index_save_load_filter(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    f1 = music / "a.mp3"
    f2 = music / "b.mp3"
    f1.write_bytes(b"12345")
    f2.write_bytes(b"67890")

    index = {
        "a.mp3": {"dest": "BAKKIS_SORTED_COPIES/a.mp3", "size": 5},
    }
    save_copied_index(music, index)

    loaded = load_copied_index(music)
    assert "a.mp3" in loaded
    assert loaded["a.mp3"]["size"] == 5

    # Filter: a.mp3 is 5 bytes, so it should be dropped (already copied)
    filtered = filter_already_copied([f1, f2], music)
    assert filtered == [f2]

    # If size changes (modified file), it should NOT be dropped
    f1.write_bytes(b"modified_longer_content")
    filtered_mod = filter_already_copied([f1, f2], music)
    assert filtered_mod == [f1, f2]
