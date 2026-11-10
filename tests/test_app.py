from bakkis_sorter.api import AppBackend
from bakkis_sorter.app import FOLDER_DIALOG, NativeBridge, folder_from_drop

from .conftest import touch


class FakeWindow:
    def __init__(self, dialog_result=None):
        self.dialog_result = dialog_result
        self.dialog_calls = []
        self.fullscreen_toggles = 0
        self.js = []

    def create_file_dialog(self, dialog_type, directory=""):
        self.dialog_calls.append((dialog_type, directory))
        return self.dialog_result

    def toggle_fullscreen(self):
        self.fullscreen_toggles += 1

    def evaluate_js(self, code):
        self.js.append(code)


def bridge(window):
    backend = AppBackend()
    b = NativeBridge(window, backend)
    b.register()
    return b, backend


def test_folder_from_drop(music):
    track = touch(music / "Sub" / "a.mp3")
    notes = touch(music / "notes.txt")
    assert folder_from_drop([str(music)]) == str(music)
    assert folder_from_drop([str(track)]) == str(track.parent)
    assert folder_from_drop([str(notes), str(track)]) == str(track.parent)
    assert folder_from_drop([str(notes)]) is None
    assert folder_from_drop([None, ""]) is None


def test_choose_folder_via_api(music):
    win = FakeWindow(dialog_result=(str(music),))
    b, backend = bridge(win)
    try:
        assert backend.dispatch("choose_folder", {}) == str(music)
        assert win.dialog_calls[0][0] == FOLDER_DIALOG
        win.dialog_result = None
        assert backend.dispatch("choose_folder", {}) is None
    finally:
        backend.close()


def test_fullscreen_toggle_and_exit():
    win = FakeWindow()
    b, backend = bridge(win)
    try:
        assert backend.dispatch("exit_fullscreen", {}) is False
        assert win.fullscreen_toggles == 0  # not fullscreen: Esc does nothing
        assert backend.dispatch("toggle_fullscreen", {}) is True
        assert backend.dispatch("exit_fullscreen", {}) is False
        assert win.fullscreen_toggles == 2
    finally:
        backend.close()


def test_drop_loads_folder_in_ui(music):
    touch(music / "a.mp3")
    win = FakeWindow()
    b, backend = bridge(win)
    try:
        b.on_drop({"dataTransfer": {"files": [{"name": "music", "pywebviewFullPath": str(music)}]}})
        assert win.js == [f'loadFolder("{music}")']
        b.on_drop({"dataTransfer": {"files": [{"name": "x"}]}})
        assert "toast(" in win.js[-1]
        b.on_drop({})
        assert len(win.js) == 3
    finally:
        backend.close()


def test_window_layout_persistence(tmp_path, monkeypatch):
    import secrets
    from bakkis_sorter.storage import EncryptedStorage
    import bakkis_sorter.storage as storage_mod
    import bakkis_sorter.app as app_mod

    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=secrets.token_bytes(32))
    monkeypatch.setattr(storage_mod, "get_storage", lambda: store)
    monkeypatch.setattr(app_mod, "get_storage", lambda: store)

    win = FakeWindow()
    win.width = 1440
    win.height = 920
    win.x = 100
    win.y = 80

    b, backend = bridge(win)
    try:
        b.save_window_layout()
        saved = store.get_kv("window_layout")
        assert saved is not None
        assert saved["width"] == 1440
        assert saved["height"] == 920
        assert saved["x"] == 100
        assert saved["y"] == 80
        assert saved["is_fullscreen"] is False
    finally:
        backend.close()
        store.close()
