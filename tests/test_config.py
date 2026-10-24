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
    # v1 presets untouched apart from the v2-only "dated" key (v1 ignores it)
    assert [{k: v for k, v in p.items() if k != "dated"} for p in store.data["presets"][:2]] == V1_STORE["presets"]
    assert [p["dated"] for p in store.data["presets"][:2]] == [False, False]
    assert config.PresetStore(dated_default=True).data["presets"][0]["dated"] is False  # saved now


def test_preset_dates(isolated_config):
    path = isolated_config / "mode_presets.json"
    path.write_text(json.dumps(V1_STORE))
    store = config.PresetStore(dated_default=True)  # an old global "dated" setting carries over
    assert [p["dated"] for p in store.data["presets"][:2]] == [True, True]
    by_name = {p["name"]: p for p in store.presets}
    assert by_name["Promos"]["dated"] and by_name["Demos"]["dated"] and not by_name["Set Builder"]["dated"]
    p = store.update(by_name["Promos"]["id"], dated=False)
    assert p["dated"] is False and config.PresetStore().get(p["id"])["dated"] is False
    assert store.create("swipe", "X", [], [], dated=True)["dated"] is True
    assert config.coerce_preset({"mode": "swipe", "name": "Demos"})["dated"] is True
    assert store.data["active_swipe_preset_id"] == V1_STORE["active_swipe_preset_id"]
    assert store.active("swipe")["name"] == "DJ Sorting"
    assert store.active("grid")["id"] == "bc5f5cebff104560ad7569d323056f47"


def test_empty_store_gets_defaults_and_is_saved(isolated_config):
    store = config.PresetStore()
    assert [p["name"] for p in store.of_mode("swipe")] == ["Quick Sort", "Promos", "Demos"]
    assert [p["name"] for p in store.of_mode("grid")] == ["Genre Sort", "Set Builder"]
    assert store.active("swipe")["name"] == "Quick Sort"
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
    while len(reloaded.of_mode("swipe")) > 1:
        reloaded.delete(reloaded.of_mode("swipe")[-1]["id"])
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


def test_builtin_presets_added_once(isolated_config):
    path = isolated_config / "mode_presets.json"
    path.write_text(json.dumps(V1_STORE))
    store = config.PresetStore()
    promos = next(p for p in store.presets if p["name"] == "Promos")
    assert promos["labels"] == ["Maybe", "Not for me", "Feeling it"] and promos["folders"] == promos["labels"]
    assert next(p for p in store.presets if p["name"] == "Demos")["labels"] == ["Hold", "Reject", "Sign"]
    assert next(p for p in store.presets if p["name"] == "Set Builder")["labels"][4] == "Peak time"
    store.delete(promos["id"])
    again = config.PresetStore()  # a deleted built-in does not come back
    assert "Promos" not in [p["name"] for p in again.presets]
    assert len(again.presets) == 4


def test_builtin_skips_existing_name(isolated_config):
    raw = json.loads(json.dumps(V1_STORE))
    raw["presets"][0]["name"] = "Promos"  # the user already has a preset called Promos
    (isolated_config / "mode_presets.json").write_text(json.dumps(raw))
    store = config.PresetStore()
    assert [p["name"] for p in store.presets].count("Promos") == 1


def test_repair_default_folder_from_an_old_account(tmp_path):
    home = tmp_path / "Users" / "bakkis"
    (home / "Music" / "Promos").mkdir(parents=True)
    old_user = "/Users/andreasbakketeig"
    # same folder exists under the new account -> remapped
    s = {"default_folder_path": f"{old_user}/Music/Promos", "auto_load_default_folder": True}
    assert config.repair_default_folder(s, home) and s["default_folder_path"] == str(home / "Music" / "Promos")
    # nowhere to be found -> forgotten, no auto-load, no warning on start
    s = {"default_folder_path": f"{old_user}/Desktop/MUSIC SORTER TEST", "auto_load_default_folder": True}
    assert config.repair_default_folder(s, home)
    assert s["default_folder_path"] is None and s["auto_load_default_folder"] is False


def test_repair_leaves_external_disks_and_own_home_alone(tmp_path):
    home = tmp_path / "Users" / "bakkis"
    for path in ("/Volumes/USB-STICK/Promos", f"{home}/Gone"):
        s = {"default_folder_path": path, "auto_load_default_folder": True}
        assert not config.repair_default_folder(s, home)
        assert s["default_folder_path"] == path
    assert not config.repair_default_folder({"default_folder_path": None}, home)


def test_colored_tiles_setting():
    assert config.default_settings()["colored_tiles"] is True
    assert config.normalize_settings({"colored_tiles": "false"})["colored_tiles"] is False
    assert config.default_settings()["game_controller"] is True


def test_feature_switches_and_reset():
    d = config.default_settings()
    assert d["discover_in_topbar"] is True and d["queue_filter"] is True and "advanced_features" not in d
    assert "advanced_features" not in config.normalize_settings({"advanced_features": False})
    cur = dict(d, volume=12, colored_tiles=False, default_folder_path="/Music", welcome_seen=True, sorted_total=99)
    out = config.reset_settings(cur)
    assert out["volume"] == d["volume"] and out["colored_tiles"] is True
    assert (out["default_folder_path"], out["welcome_seen"], out["sorted_total"]) == ("/Music", True, 99)
