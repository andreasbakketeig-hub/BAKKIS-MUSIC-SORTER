import time

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TBPM, TIT2, TKEY, TPE1
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

from bakkis_sorter import metadata

from .audio_fixtures import HAS_AFCONVERT, PNG_1PX, convert, make_mp3, make_wav


def tag_id3(path, **frames):
    tags = ID3()
    for f in frames.values():
        tags.add(f)
    tags.save(str(path))


def test_mp3_tags_and_cover(tmp_path):
    p = make_mp3(tmp_path / "x.mp3")
    tag_id3(
        p,
        t=TIT2(encoding=3, text="Track"),
        a=TPE1(encoding=3, text="Artist"),
        b=TBPM(encoding=3, text="124.00"),
        k=TKEY(encoding=3, text="8A"),
        c=APIC(encoding=3, mime="image/png", type=3, desc="", data=PNG_1PX),
    )
    m = metadata.read_meta(p)
    assert (m.title, m.artist, m.display, m.bpm, m.key) == ("Track", "Artist", "Track — Artist", "124", "8A")
    assert m.duration > 0.9 and m.has_cover
    assert metadata.read_cover(p) == PNG_1PX


def test_wav_without_tags_uses_filename(tmp_path):
    p = make_wav(tmp_path / "Some Artist - Some Title.wav", seconds=2)
    m = metadata.read_meta(p)
    assert m.display == "Some Title — Some Artist"
    assert m.title is None and m.bpm is None and not m.has_cover
    assert abs(m.duration - 2.0) < 0.01


def test_wav_with_id3(tmp_path):
    p = make_wav(tmp_path / "a.wav")
    w = WAVE(str(p))
    w.add_tags()
    w.tags.add(TIT2(encoding=3, text="Wave Title"))
    w.save()
    assert metadata.read_meta(p).display == "Wave Title"


def test_garbage_file_does_not_raise(tmp_path):
    p = tmp_path / "broken.mp3"
    p.write_bytes(b"not audio at all")
    m = metadata.read_meta(p)
    assert m.display == "broken.mp3" and m.duration == 0.0
    assert metadata.read_cover(p) is None


@pytest.mark.skipif(not HAS_AFCONVERT, reason="needs macOS afconvert")
def test_m4a_tags_and_cover(tmp_path):
    p = convert(make_wav(tmp_path / "a.wav"), tmp_path / "a.m4a", "m4af", "aac")
    mp4 = MP4(str(p))
    mp4["\xa9nam"] = ["M4A Title"]
    mp4["\xa9ART"] = ["M4A Artist"]
    mp4["tmpo"] = [126]
    mp4["covr"] = [MP4Cover(PNG_1PX, imageformat=MP4Cover.FORMAT_PNG)]
    mp4.save()
    m = metadata.read_meta(p)
    assert m.display == "M4A Title — M4A Artist" and m.bpm == "126" and m.has_cover
    assert metadata.read_cover(p) == PNG_1PX


@pytest.mark.skipif(not HAS_AFCONVERT, reason="needs macOS afconvert")
def test_flac_tags_and_cover(tmp_path):
    p = convert(make_wav(tmp_path / "a.wav"), tmp_path / "a.flac", "flac", "flac")
    f = FLAC(str(p))
    f["title"] = "Flac Title"
    f["initialkey"] = "5A"
    f["bpm"] = "122,5"
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", PNG_1PX
    f.add_picture(pic)
    f.save()
    m = metadata.read_meta(p)
    assert (m.display, m.key, m.bpm, m.has_cover) == ("Flac Title", "5A", "122.5", True)


def test_format_bpm():
    assert metadata.format_bpm("128") == "128"
    assert metadata.format_bpm("127.99") == "128"
    assert metadata.format_bpm("0") is None
    assert metadata.format_bpm("fast") == "fast"
    assert metadata.format_bpm(None) is None


def test_image_mime():
    assert metadata.image_mime(PNG_1PX) == "image/png"
    assert metadata.image_mime(b"\xff\xd8\xff") == "image/jpeg"


def test_cache_invalidates_on_change(tmp_path):
    p = make_wav(tmp_path / "A - B.wav")
    cache = metadata.MetadataCache()
    try:
        assert cache.get(p).display == "B — A"
        w = WAVE(str(p))
        w.add_tags()
        w.tags.add(TIT2(encoding=3, text="Changed"))
        w.save()
        assert cache.get(p).display == "Changed"
        assert cache.get(tmp_path / "missing.wav").display == "missing.wav"
        assert cache.cover(p) is None and cache.cover(p) is None  # negative result cached
    finally:
        cache.shutdown()


def test_prefetch_fills_cache(tmp_path, monkeypatch):
    paths = [make_wav(tmp_path / f"{i}.wav") for i in range(5)]
    cache = metadata.MetadataCache()
    try:
        cache.prefetch(paths)
        deadline = time.time() + 5
        while time.time() < deadline and any(metadata._cache_key(p) not in cache._meta for p in paths):
            time.sleep(0.01)
        calls = []
        monkeypatch.setattr(metadata, "read_meta", lambda p: calls.append(p))
        for p in paths:
            cache.get(p)
        assert calls == []
    finally:
        cache.shutdown()
