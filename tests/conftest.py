import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own config dir; never touch the real Application Support folder."""
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("BAKKIS_CONFIG_DIR", str(d))
    return d


@pytest.fixture
def music(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    return root


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
