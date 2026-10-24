import hashlib
import io
import plistlib
import subprocess
import time
import zipfile

import pytest

from bakkis_sorter import updater


def make_app(path, version, bundle_id=updater.BUNDLE_ID, marker=b""):
    (path / "Contents" / "MacOS").mkdir(parents=True)
    with open(path / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, f)
    (path / "Contents" / "MacOS" / "marker").write_bytes(marker or version.encode())
    return path


def zip_app(app):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in app.rglob("*"):
            z.write(p, p.relative_to(app.parent))
    return buf.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def release_for(data, name="BAKKIS-MUSIC-SORTER-9.9.9.zip", digest=None):
    return {"assets": [{"name": name, "browser_download_url": "https://example.invalid/" + name, "size": len(data),
                        "digest": "sha256:" + (digest or hashlib.sha256(data).hexdigest())}]}


def unzip(z, d):
    with zipfile.ZipFile(z) as f:
        f.extractall(d)


@pytest.fixture
def installed(tmp_path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    return make_app(apps / "BAKKIS MUSIC SORTER.app", "2.0.5")


@pytest.fixture
def new_zip(tmp_path):
    return zip_app(make_app(tmp_path / "build" / "BAKKIS MUSIC SORTER.app", "9.9.9"))


def wait_for(inst, statuses=("ready", "error")):
    for _ in range(200):
        if inst.snapshot()["status"] in statuses:
            return inst.snapshot()
        time.sleep(0.02)
    raise AssertionError("installer did not finish")


def test_pick_asset_and_problems(tmp_path, installed):
    assert updater.pick_asset({"assets": [{"name": "notes.txt", "digest": "sha256:x", "browser_download_url": "u"}]}) is None
    assert updater.pick_asset({"assets": [{"name": "BAKKIS-MUSIC-SORTER-2.1.0.zip", "browser_download_url": "u"}]}) is None  # no digest
    assert updater.pick_asset(release_for(b"x"))["sha256"] == hashlib.sha256(b"x").hexdigest()
    assert "source" in updater.install_problem(None)
    assert "Applications" in updater.install_problem(tmp_path / "AppTranslocation" / "X" / "B.app".replace("AppTranslocation", "/AppTranslocation/"))
    assert updater.install_problem(installed) is None


def test_download_verify_unpack_and_swap(installed, new_zip):
    inst = updater.Installer(installed, opener=lambda req, timeout: FakeResponse(new_zip), unzip=unzip)
    inst.start(release_for(new_zip), "9.9.9")
    assert wait_for(inst)["status"] == "ready"
    sleeper = subprocess.Popen(["sleep", "0.4"])
    inst.restart(pid=sleeper.pid, open_cmd="true")
    sleeper.wait()
    for _ in range(100):
        try:
            if updater.bundle_version(installed)[1] == "9.9.9":
                break
        except FileNotFoundError:
            pass  # the instant between moving the old app aside and the new one in
        time.sleep(0.05)
    assert updater.bundle_version(installed) == (updater.BUNDLE_ID, "9.9.9")
    time.sleep(0.2)
    assert [p.name for p in installed.parent.iterdir()] == ["BAKKIS MUSIC SORTER.app"]  # no backup or staging left


def test_bad_checksum_changes_nothing(installed, new_zip):
    inst = updater.Installer(installed, opener=lambda req, timeout: FakeResponse(new_zip), unzip=unzip)
    inst.start(release_for(new_zip, digest="0" * 64), "9.9.9")
    st = wait_for(inst)
    assert st["status"] == "error" and "checksum" in st["error"]
    assert updater.bundle_version(installed)[1] == "2.0.5"
    assert [p.name for p in installed.parent.iterdir()] == ["BAKKIS MUSIC SORTER.app"]
    with pytest.raises(updater.UpdateError):
        inst.restart(open_cmd="true")


def test_wrong_app_in_download_is_refused(installed, tmp_path):
    other = zip_app(make_app(tmp_path / "x" / "Other.app", "9.9.9", bundle_id="com.example.other"))
    inst = updater.Installer(installed, opener=lambda req, timeout: FakeResponse(other), unzip=unzip)
    inst.start(release_for(other), "9.9.9")
    st = wait_for(inst)
    assert st["status"] == "error" and "Unexpected app" in st["error"]
    assert updater.bundle_version(installed)[1] == "2.0.5"


def test_running_from_source_cannot_install():
    inst = updater.Installer(None)
    with pytest.raises(updater.UpdateError):
        inst.start(release_for(b"x"), "9.9.9")
