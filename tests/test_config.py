import json

import pytest

from bakkis_sorter import config


def test_config_dir_uses_env(isolated_config):
    assert config.config_dir() == isolated_config
    assert isolated_config.is_dir()


def test_defaults_when_no_file():
    s = config.load_settings()
    assert s == config.default_settings()
    assert s["file_operation"] == "move"
    assert s["swipe_keep_direction"] == "right"


def test_normalize_v1_settings_migrates_aliases_and_drops_dead_keys():
    v1 = {
        "default_folder_path": "  /Users/x/Music  ",
        "auto_load_default_folder": "true",
        "playback_start_seconds": 100,
        "default_mode_on_startup": "genre",
        "track_order_mode": "in_order",
        "track_order": "random",
        "add_date_to_created_sorting_folders": True,
        "create_dated_sorting_sessions": "false",
        "volume": 250,
        "swipe_archive_hotkey": "UP",
        "unknown_key": 1,
    }
    s = config.normalize_settings(v1)
    assert s["default_folder_path"] == "/Users/x/Music"
    assert s["auto_load_default_folder"] is True
    assert s["playback_start_seconds"] == 90.0
    assert config.normalize_settings({"playback_start_seconds": 300})["playback_start_seconds"] == 300.0
    assert config.normalize_settings({"playback_start_seconds": 1000})["playback_start_seconds"] == 360.0
    assert s["default_mode_on_startup"] == "grid"
    assert s["track_order"] == "in_order"
    assert s["create_dated_sorting_sessions"] is False
    assert s["volume"] == 100
    assert s["swipe_archive_hotkey"] == "up"
    assert "add_date_to_created_sorting_folders" not in s
    assert "track_order_mode" not in s
    assert "unknown_key" not in s


def test_invalid_values_fall_back():
    s = config.normalize_settings(
        {"volume": "loud", "playback_start_seconds": "x", "file_operation": "delete", "end_of_track": "?", "default_folder_path": ""}
    )
    assert s["volume"] == 80
    assert s["playback_start_seconds"] == config.DEFAULT_PLAYBACK_START_SEC
    assert s["file_operation"] == "move"
    assert s["end_of_track"] == "skip"
    assert s["default_folder_path"] is None


def test_patch_and_reload(isolated_config):
    config.patch_settings(file_operation="copy", volume=42)
    s = config.load_settings()
    assert s["file_operation"] == "copy"
    assert s["volume"] == 42
    assert json.loads((isolated_config / "settings.json").read_text())["volume"] == 42
    assert not (isolated_config / "settings.json.tmp").exists()


def test_corrupt_settings_file_gives_defaults(isolated_config):
    (isolated_config / "settings.json").write_text("{not json")
    assert config.load_settings() == config.default_settings()


def test_migrate_from_v1(tmp_path, isolated_config):
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "bakkis_settings.json").write_text(json.dumps({"volume": 33}))
    (v1 / "mode_presets.json").write_text(json.dumps({"presets": []}))
    copied = config.migrate_from_v1(v1)
    assert sorted(copied) == ["mode_presets.json", "settings.json"]
    assert config.load_settings()["volume"] == 33
    # Runs only once, and never overwrites.
    (v1 / "settings.json").write_text(json.dumps({"volume": 99}))
    assert config.migrate_from_v1(v1) == []
    assert config.load_settings()["volume"] == 33


def test_migrate_prefers_settings_json_over_legacy(tmp_path):
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "settings.json").write_text(json.dumps({"volume": 10}))
    (v1 / "bakkis_settings.json").write_text(json.dumps({"volume": 20}))
    config.migrate_from_v1(v1)
    assert config.load_settings()["volume"] == 10


def test_migrate_keeps_existing_v2_file(tmp_path, isolated_config):
    config.patch_settings(volume=5)
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "settings.json").write_text(json.dumps({"volume": 70}))
    assert config.migrate_from_v1(v1) == []
    assert config.load_settings()["volume"] == 5


# ---------------------------------------------------------------- presets


V1_STORE = {
    "presets": [
        {
            "id": "c145950aff794fc58cf8468d63381f38",
            "name": "DJ Sorting",
            "mode": "swipe",
            "labels": ["Keep", "Archive", "Favorite"],
            "colors": ["#00D6B4", "#FF2D55", "#FFF200"],
            "folders": ["KEEP", "ARCHIVE", "FAVORITES"],
        },
        {
            "id": "bc5f5cebff104560ad7569d323056f47",
            "name": "Genre Sorting",
            "mode": "grid",
            "labels": config.DEFAULT_GENRE_LABELS,
            "colors": config.DEFAULT_GENRE_COLORS,
            "folders": config.DEFAULT_GENRE_LABELS,
        },
    ],
    "active_swipe_preset_id": "c145950aff794fc58cf8468d63381f38",
    "active_grid_preset_id": "bc5f5cebff104560ad7569d323056f47",
}


def test_v1_store_roundtrips_unchanged(isolated_config):
    path = isolated_config / "mode_presets.json"
    path.write_text(json.dumps(V1_STORE))
    store = config.PresetStore()
    assert store.data == V1_STORE
    assert store.active("swipe")["name"] == "DJ Sorting"
    assert store.active("grid")["id"] == "bc5f5cebff104560ad7569d323056f47"


def test_empty_store_gets_defaults_and_is_saved(isolated_config):
    store = config.PresetStore()
    assert len(store.of_mode("swipe")) == 1
    assert len(store.of_mode("grid")) == 1
    assert store.active("grid")["labels"] == config.DEFAULT_GENRE_LABELS
    assert (isolated_config / "mode_presets.json").is_file()


def test_default_grid_reads_v1_genre_labels(isolated_config):
    labels = ["A", "B", "C"]
    (isolated_config / "genre_labels.json").write_text(json.dumps({"labels": labels, "colors": ["#18A0FB", "#bad"]}))
    store = config.PresetStore()
    g = store.active("grid")
    assert g["labels"][:3] == labels
    assert g["labels"][3:] == config.DEFAULT_GENRE_LABELS[3:]
    assert g["colors"][0] == "#18A0FB"
    assert g["colors"][1] == config.DEFAULT_GENRE_COLORS[1]  # not in palette -> fallback


def test_coerce_preset_pads_and_validates():
    p = config.coerce_preset({"mode": "grid", "name": "", "labels": ["Disco"], "colors": ["#123456"], "folders": ["a/b"]})
    assert len(p["labels"]) == 9 and p["labels"][0] == "Disco"
    assert p["colors"][0] == config.DEFAULT_GENRE_COLORS[0]
    assert p["folders"][0] == "a/b"
    assert p["folders"][1] == "Tech House"
    assert p["name"] == "Grid preset"
    assert config.coerce_preset({"mode": "grid"}) is None
    assert config.coerce_preset({"mode": "other"}) is None
    s = config.coerce_preset({"mode": "swipe", "colors": ["0f0"]})
    assert s["labels"] == config.DEFAULT_SWIPE_LABELS
    assert s["colors"][0] == "#00FF00"


def test_duplicate_ids_are_fixed():
    raw = {"presets": [dict(V1_STORE["presets"][0]), dict(V1_STORE["presets"][0])]}
    store = config.normalize_preset_store(raw)
    ids = [p["id"] for p in store["presets"]]
    assert len(set(ids)) == len(ids)


def test_preset_crud(isolated_config):
    store = config.PresetStore()
    p = store.create("swipe", "Promo", ["Play", "Bin", "Gold"], ["#FFFFFF"] * 3)
    assert store.get(p["id"])["labels"] == ["Play", "Bin", "Gold"]
    store.set_active(p["id"])
    assert store.active("swipe")["id"] == p["id"]
    store.update(p["id"], name="Promos 2024", labels=["Yes", "No", "Wow"])
    reloaded = config.PresetStore()
    assert reloaded.active("swipe")["name"] == "Promos 2024"
    assert reloaded.active("swipe")["labels"] == ["Yes", "No", "Wow"]
    reloaded.delete(p["id"])
    assert reloaded.active("swipe")["id"] != p["id"]
    with pytest.raises(config.PresetError):
        reloaded.delete(reloaded.active("swipe")["id"])  # last swipe preset
    with pytest.raises(config.PresetError):
        reloaded.set_active("nope")


def test_reset_grid(isolated_config):
    store = config.PresetStore()
    g = store.active("grid")
    store.update(g["id"], labels=["X"] * 9, folders=["X"] * 9)
    store.reset_grid_to_default_genres(g["id"])
    assert store.active("grid")["labels"] == config.DEFAULT_GENRE_LABELS
    assert store.active("grid")["folders"] == config.DEFAULT_GENRE_LABELS


def test_safe_segment():
    assert config.safe_segment("a/b:c\\d") == "a-b-c-d"
    assert config.safe_segment("   ") == "Preset"
    assert config.safe_segment("..") == "Preset"
    assert len(config.safe_segment("x" * 300)) == 120
