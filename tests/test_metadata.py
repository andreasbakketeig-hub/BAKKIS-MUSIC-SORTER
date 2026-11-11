import time

import pytest

pytestmark = pytest.mark.audio
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
    assert m.display == "broken" and m.duration == 0.0
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


def test_peak_store(tmp_path):
    import base64

    p = make_wav(tmp_path / "a.wav")
    store = metadata.PeakStore(tmp_path / "peaks")
    assert store.get(p) is None
    peaks = base64.b64encode(bytes(range(256))).decode()
    assert store.put(p, peaks)
    assert store.get(p) == peaks
    assert not store.put(p, "not base64!!")
    assert not store.put(p, "A" * (metadata.MAX_PEAKS_LEN + 4))
    make_wav(p, seconds=2)  # file changed -> new key
    assert store.get(p) is None
    assert store.get(tmp_path / "missing.wav") is None


def test_peak_store_content_digest_move_resilience(tmp_path):
    import shutil

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    p1 = make_wav(inbox / "track.wav")
    store = metadata.PeakStore(tmp_path / "peaks")
    raw_peaks = bytes(range(256)) * 3 + bytes(range(32))

    assert store.put_bytes(p1, raw_peaks)
    assert store.get_bytes(p1) == raw_peaks

    # Move file to a destination folder (simulating sorter action)
    dest_dir = tmp_path / "sorted" / "2026" / "House"
    dest_dir.mkdir(parents=True)
    p2 = dest_dir / "track.wav"
    shutil.move(p1, p2)

    # Path-independent content digest allows the moved track to resolve the cached peaks instantly
    assert store.get_bytes(p2) == raw_peaks


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Becuz my-free-mp3s.com", "Becuz"),
        ("Claire (Alfonso Muchacho Rework) myfreemp3.vip", "Claire (Alfonso Muchacho Rework)"),
        ("Ultravision feat. Jaw (Original Mix)_www.mediadonor.ru myfreemp3.vip ", "Ultravision feat. Jaw (Original Mix)"),
        ("Under The Sheets %28Original Mix%29", "Under The Sheets (Original Mix)"),
        ("08-Maceo Plex - Under The Sheets", "Maceo Plex - Under The Sheets"),
        ("sleeper (0daymusic.org)", "sleeper"),
        ("www.electronicfresh.com", None),
        ("1-2-3", "1-2-3"),
        ("Dancing For My Pleasure (Original Mix) 7A", "Dancing For My Pleasure (Original Mix)"),
        ("The Second Moon (Original Mix) ***********************", "The Second Moon (Original Mix)"),
        ("9A 122", None),
        ("Inhalation_Original Mix", "Inhalation Original Mix"),
        ("Track 12A", "Track"),
        ("Blue Monday 88", "Blue Monday 88"),
        ("20  - 9A 122 - High_feat__Lauren_Faith_Original_Mix (1)", "High feat Lauren Faith Original Mix (1)"),
        ("2A or 3A - 126 - Where_Are_You_Now", "Where Are You Now"),
        ("Mr. Oizo - Flat Beat", "Mr. Oizo - Flat Beat"),
        ("", None),
    ],
)
def test_clean_text(raw, clean):
    assert metadata.clean_text(raw) == clean


def test_site_stamp_artist_falls_back_to_title_split(tmp_path):
    p = make_mp3(tmp_path / "x.mp3")
    tag_id3(p, t=TIT2(encoding=3, text="08-Maceo Plex - Under The Sheets %28Original Mix%29"), a=TPE1(encoding=3, text="www.electronicfresh.com"))
    m = metadata.read_meta(p)
    assert (m.title, m.artist) == ("Under The Sheets (Original Mix)", "Maceo Plex")


def test_filename_fallback_is_cleaned(tmp_path):
    p = make_wav(tmp_path / "Mihai Popoviciu - Flux my-free-mp3s.com .wav")
    assert metadata.read_meta(p).display == "Flux — Mihai Popoviciu"


def test_dash_variants_and_stem_fallback(tmp_path):
    assert metadata.read_meta(make_wav(tmp_path / "Ælg Øyvind – Søt Ünicode.wav")).display == "Søt Ünicode — Ælg Øyvind"
    assert metadata.read_meta(make_wav(tmp_path / "Artist — Title.wav")).display == "Title — Artist"
    assert metadata.read_meta(make_wav(tmp_path / "No Separator Here.WAV")).display == "No Separator Here"


def test_downsample_cover_resizes_large_images():
    import io
    from PIL import Image

    # 1. Create large 1600x1600 image
    im = Image.new("RGB", (1600, 1600), color=(50, 100, 150))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    raw = buf.getvalue()
    assert len(raw) > 25_000

    # 2. Downsample
    thumb = metadata.downsample_cover(raw, max_dim=320)
    assert thumb is not None
    assert len(thumb) < len(raw)

    # 3. Verify dimensions
    with Image.open(io.BytesIO(thumb)) as thumb_im:
        w, h = thumb_im.size
        assert w <= 320 and h <= 320


def test_downsample_cover_passthrough_and_corrupt_safety():
    assert metadata.downsample_cover(None) is None
    assert metadata.downsample_cover(b"") == b""

    # Small dimension image (<= 320x320) passes through untouched
    from PIL import Image
    import io

    im = Image.new("RGB", (100, 100), color=(50, 50, 50))
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    small = buf.getvalue()
    assert metadata.downsample_cover(small) == small

    # Corrupt data returns original bytes without raising
    corrupt = b"not an image at all" * 2000
    assert metadata.downsample_cover(corrupt) == corrupt


def test_byte_bounded_lru_eviction():
    # 1. Count-based eviction
    lru = metadata.ByteBoundedLRU(max_items=3, max_bytes=100_000)
    lru.put("a", b"111")
    lru.put("b", b"222")
    lru.put("c", b"333")
    assert len(lru) == 3
    lru.put("d", b"444")
    assert len(lru) == 3
    assert "a" not in lru
    assert "d" in lru

    # 2. Byte-limit eviction
    lru_bytes = metadata.ByteBoundedLRU(max_items=10, max_bytes=1000)
    lru_bytes.put("x", b"A" * 400)
    lru_bytes.put("y", b"B" * 400)
    assert lru_bytes.current_bytes == 800
    lru_bytes.put("z", b"C" * 400)
    # Total would be 1200 > 1000, so oldest ("x") is evicted
    assert "x" not in lru_bytes
    assert "y" in lru_bytes
    assert "z" in lru_bytes
    assert lru_bytes.current_bytes == 800

    # 3. Sentinel _MISSING has 0 bytes
    lru_bytes.put("miss", metadata._MISSING)
    assert lru_bytes.current_bytes == 800
    assert "miss" in lru_bytes
    assert lru_bytes.get("miss") is metadata._MISSING


def test_metadata_cache_cover_downsamples_and_bounds_memory(tmp_path):
    import io
    from PIL import Image

    # Create a real MP3 with large embedded APIC
    im = Image.new("RGB", (1000, 1000), color=(120, 60, 30))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    large_cover = buf.getvalue()

    p = make_mp3(tmp_path / "big_cover.mp3")
    tag_id3(p, apic=APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=large_cover))

    cache = metadata.MetadataCache()
    try:
        thumb = cache.cover(p)
        assert thumb is not None
        assert len(thumb) < len(large_cover)

        with Image.open(io.BytesIO(thumb)) as thumb_im:
            w, h = thumb_im.size
            assert w <= 320 and h <= 320

        # Cache memory is tracked and bounded
        assert cache._covers.current_bytes == len(thumb)
    finally:
        cache.shutdown()

