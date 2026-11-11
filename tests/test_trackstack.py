"""Tests for Trackstack integration and unified Inflyte + Trackstack PromoSession."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.promos

from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.inflyte import CommentPool, InflytePromo, InflyteSession, InflyteTrack
from bakkis_sorter.storage import EncryptedStorage
from bakkis_sorter.trackstack import TrackstackBridge, split_display_title, unique_download_dest


class FakeInflyteBridge:
    def __init__(self) -> None:
        self.feedback_calls: list[dict] = []
        self.dismiss_calls: list[int] = []
        self.played_calls: list[tuple[int, int]] = []

    def fetch_preview_audio_url(self, release_id: int, inflyte_track_id: int, *, priority: bool = False) -> str:
        return f"https://example.com/inflyte_{inflyte_track_id}.mp3"

    def mark_track_played(self, release_id: int, inflyte_track_id: int) -> None:
        self.played_calls.append((release_id, inflyte_track_id))

    def submit_feedback(self, release_id: int, favourite_track_id: int, rating: int, comments: str) -> dict:
        self.feedback_calls.append(
            {
                "release_id": release_id,
                "favourite_track_id": favourite_track_id,
                "rating": rating,
                "comments": comments,
            }
        )
        return {"status": "ok"}

    def dismiss_promo(self, release_id: int) -> dict:
        self.dismiss_calls.append(release_id)
        return {"status": "deleted"}


class FakeTrackstackBridge(TrackstackBridge):
    def __init__(self, tmp_path: Path, storage: EncryptedStorage) -> None:
        super().__init__(storage=storage, download_dir=tmp_path / "Inflyte Music")
        self.workspace_id = "ws-test-1"
        self.inbox_id = "inbox-test-1"
        self.user_id = "user-test-1"
        self.downloaded_stack_id = "stack-downloaded-1"
        self.trpc_calls: list[tuple[str, str, Any]] = []

    def ensure_context_ids(self) -> None:
        return

    def _trpc_request(self, method: str, procedure: str, payload: Any = None, *, priority: bool = False) -> Any:
        self.trpc_calls.append((method, procedure, payload))
        if procedure == "tracks.createTrackDownloadUrl":
            sub_tid = payload["submissionTrackId"]
            return {
                "presignedUrl": f"https://r2.example.com/{sub_tid}.wav",
                "filename": f"Artist - Track {sub_tid}.wav",
            }
        return {"ok": True}

    def _download_presigned_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF-WAVE-FULL-QUALITY-AUDIO")
        return dest


def test_split_display_title_and_unique_download_dest(tmp_path: Path) -> None:
    assert split_display_title("Groove Tool (Extended Mix) - DJ Example", "Label") == (
        "Groove Tool (Extended Mix)",
        "DJ Example",
    )
    dl_dir = tmp_path / "Inflyte Music"
    p1 = unique_download_dest(dl_dir, "Artist - Banger.wav")
    p1.write_bytes(b"123")
    p2 = unique_download_dest(dl_dir, "Artist - Banger.wav")
    assert p1.name == "Artist - Banger.wav"
    assert p2.name == "Artist - Banger (2).wav"


def test_trackstack_grouping_and_unified_session_workflow(tmp_path: Path) -> None:
    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=secrets.token_bytes(32))
    ts_bridge = FakeTrackstackBridge(tmp_path, store)
    inf_bridge = FakeInflyteBridge()

    # 1. Build multi-track Trackstack promos from listPendingQueueTracks items
    raw_items = [
        {
            "id": "sub-track-1a",
            "submissionId": "sub-multi-1",
            "trackId": "uuid-track-1a",
            "displayTitle": "Acid Roller - Warehouse DJ",
            "duration": 310.0,
            "genre": {"id": "g1", "label": "Tech House"},
            "streamKey": "audio/uuid-track-1a/stream1.aac",
            "senderDisplayName": "Hot Creations",
            "createdAt": "2026-11-01T12:00:00.000Z",
            "reviewState": "PENDING",
            "bpm": 128,
        },
        {
            "id": "sub-track-1b",
            "submissionId": "sub-multi-1",
            "trackId": "uuid-track-1b",
            "displayTitle": "Acid Roller (Dub Mix) - Warehouse DJ",
            "duration": 295.0,
            "genre": {"id": "g1", "label": "Tech House"},
            "streamKey": "audio/uuid-track-1b/stream2.aac",
            "senderDisplayName": "Hot Creations",
            "createdAt": "2026-11-01T12:00:00.000Z",
            "reviewState": "PENDING",
            "bpm": 128,
        },
        {
            "id": "sub-track-2a",
            "submissionId": "sub-single-2",
            "trackId": "uuid-track-2a",
            "displayTitle": "Skip Me - Other Producer",
            "duration": 240.0,
            "genre": {"id": "g2", "label": "Minimal"},
            "streamKey": "audio/uuid-track-2a/stream3.aac",
            "senderDisplayName": "Solid Grooves",
            "createdAt": "2026-11-02T12:00:00.000Z",
            "reviewState": "PENDING",
            "bpm": 130,
        },
    ]
    ts_promos = ts_bridge.build_promos_from_items(raw_items)
    assert len(ts_promos) == 2
    assert ts_promos[0].source == "trackstack"
    assert len(ts_promos[0].tracks) == 2
    assert ts_promos[0].tracks[0].name == "Acid Roller"
    assert ts_promos[0].tracks[0].artist_name == "Warehouse DJ"
    assert ts_promos[0].tracks[0].bpm == 128.0

    # 2. Also create an Inflyte promo and combine both into one unified InflyteSession
    inf_promo = InflytePromo(
        release_id=888001,
        name="Inflyte Club EP",
        sender_label="Defected",
        release_date="2026-11-08",
        thumbnail_url="",
        full_image_url="",
        summary="",
        source="inflyte",
        tracks=[
            InflyteTrack(
                id="inf-t1",
                inflyte_track_id=99001,
                release_id=888001,
                name="Inflyte Heater",
                artist_name="Club Artist",
                duration=320.0,
                track_index=0,
                track_total=1,
                source="inflyte",
            )
        ],
    )

    presets = PresetStore(path=tmp_path / "presets.json")
    comments_file = tmp_path / "promo_comments.txt"
    comments_file.write_text("[5/5]\nHuge weapon!\n[4/5]\nNice groove!\n", encoding="utf-8")
    pool = CommentPool(path=comments_file)

    custom_settings = {**default_settings(), "promo_download_dir": str(tmp_path / "Inflyte Music")}
    session = InflyteSession(
        [ts_promos[0], ts_promos[1], inf_promo],
        bridge=inf_bridge,
        trackstack_bridge=ts_bridge,
        storage=store,
        presets=presets,
        settings=lambda: custom_settings,
        comment_pool=pool,
    )
    session._download_file = lambda url, dest: (dest.write_bytes(b"AAC-PREVIEW"), dest)[1]  # type: ignore[method-assign]

    # Promo 1 is Trackstack multi-track submission (2 tracks)
    cur = session.current
    assert cur is not None
    assert cur.source == "trackstack"
    assert cur.track_index == 0

    # Press ↓ (skip) -> steps to Track 2 of the same Trackstack submission
    session.skip(cur.id)
    cur2 = session.current
    assert cur2 is not None
    assert cur2.track_index == 1
    assert cur2.name == "Acid Roller (Dub Mix)"

    # Press ↑ (slot 2: Feeling it) on Trackstack promo -> Thumbs Up ("Downloaded" stack) + downloads BOTH files to ~/Music/Inflyte Music
    session.sort(cur2.id, "swipe", 2)
    session.flush()

    dl_folder = tmp_path / "Inflyte Music"
    downloaded_files = sorted(p.name for p in dl_folder.glob("*.wav"))
    assert downloaded_files == [
        "Artist - Track sub-track-1a.wav",
        "Artist - Track sub-track-1b.wav",
    ]
    support_calls = [c for c in ts_bridge.trpc_calls if c[1] == "submissions.supportSubmissionTrack"]
    assert len(support_calls) == 2
    assert support_calls[0][2] == {"id": "sub-track-1a", "stackId": "stack-downloaded-1"}
    assert support_calls[1][2] == {"id": "sub-track-1b", "stackId": "stack-downloaded-1"}
    assert store.is_reviewed("trackstack", "sub-multi-1")

    # Promo 2 is Trackstack single-track submission -> press ← (slot 1: Not for me) -> Declines on Trackstack
    cur_ts2 = session.current
    assert cur_ts2 is not None
    assert cur_ts2.source == "trackstack"
    session.sort(cur_ts2.id, "swipe", 1)
    session.flush()

    decline_calls = [c for c in ts_bridge.trpc_calls if c[1] == "submissions.defaultDeclineSubmissionTrack"]
    assert len(decline_calls) == 1
    assert decline_calls[0][2] == {"id": "sub-track-2a"}
    assert store.is_reviewed("trackstack", "sub-single-2")

    # Promo 3 is Inflyte promo -> press → (slot 0: Maybe 4/5) -> submits 4/5 + comment to Inflyte
    cur_inf = session.current
    assert cur_inf is not None
    assert cur_inf.source == "inflyte"
    session.sort(cur_inf.id, "swipe", 0)
    session.flush()

    assert len(inf_bridge.feedback_calls) == 1
    assert inf_bridge.feedback_calls[0]["rating"] == 4
    assert inf_bridge.feedback_calls[0]["comments"] == "Nice groove!"
    assert store.is_reviewed("inflyte", "888001")

    session.close()
    store.close()


def test_trackstack_context_discovery_and_window_autohide(tmp_path: Path) -> None:
    key = secrets.token_bytes(32)
    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=key)
    bridge = TrackstackBridge(storage=store, download_dir=tmp_path / "Inflyte Music")

    class _DummyWin:
        def __init__(self) -> None:
            self.hidden = False

        def hide(self) -> None:
            self.hidden = True

    dummy_win = _DummyWin()
    bridge._win = dummy_win
    bridge._login_window_shown = True

    def fake_eval_js(script: str, timeout: float = 15.0, *, visible: bool = False):
        return {
            "ready": True,
            "workspaceId": "ws-cuid-100",
            "inboxId": None,
            "userId": "user-cuid-200",
            "apiBaseUrl": "https://example.lambda-url.eu-west-1.on.aws",
        }

    def fake_trpc(method: str, procedure: str, payload=None, *, priority: bool = False):
        if procedure == "workspace.getWorkspace" and payload == "ws-cuid-100":
            return {"id": "ws-cuid-100", "inboxes": [{"id": "inbox-cuid-300"}]}
        if procedure == "inbox.getInboxStacks":
            return [{"id": "stack-dl-400", "name": "Downloaded"}]
        return {}

    bridge._eval_js = fake_eval_js  # type: ignore[method-assign]
    bridge._trpc_request = fake_trpc  # type: ignore[method-assign]

    st = bridge.check_auth_status()
    assert st["connected"] is True
    assert dummy_win.hidden is True

    bridge.ensure_context_ids()
    assert bridge.workspace_id == "ws-cuid-100"
    assert bridge.inbox_id == "inbox-cuid-300"
    assert bridge.downloaded_stack_id == "stack-dl-400"

    store.close()


def test_trackstack_configurable_download_dir(tmp_path: Path) -> None:
    from bakkis_sorter.promos.trackstack.downloader import promo_download_dir

    # 1. Default fallback is ~/Downloads
    default_dir = promo_download_dir()
    assert default_dir == Path("~/Downloads").expanduser()

    # 2. Custom directory passed is respected
    custom_target = tmp_path / "MyCustomDownloads"
    resolved = promo_download_dir(str(custom_target))
    assert resolved == custom_target
    assert resolved.exists()


