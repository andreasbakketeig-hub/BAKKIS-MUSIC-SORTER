"""Promo session mixin for pre-fetching audio/covers and offline flight stashing."""

from __future__ import annotations

import os
import shutil
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from ...logger import get_logger
from ...ratelimit import RateLimitError
from ...session import SessionError
from ..inflyte import cache_dir
from ..models import InflyteTrack

logger = get_logger("inflyte")


class SessionStashMixin:
    """Pre-downloading, cache verification, pre-fetching, and offline flight stashing."""

    def _download_file(self, url: str, dest: Path) -> Optional[Path]:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".{uuid.uuid4().hex[:6]}.tmp")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            if tmp.stat().st_size > 0:
                os.replace(tmp, dest)
                return dest
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.debug("Non-fatal: download preview file %s failed: %s", url, exc)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return None

    def _ensure_audio_cached(self, track_id: str, *, silent: bool = False) -> Optional[Path]:
        """Download and cache the preview MP3/AAC for ``track_id`` on disk without marking it played."""
        with self.lock:  # type: ignore[attr-defined]
            t = self.tracks.get(str(track_id))  # type: ignore[attr-defined]
            if t is None:
                return None
            if t.cached_audio_path and t.cached_audio_path.is_file() and t.cached_audio_path.stat().st_size > 0:
                return t.cached_audio_path
            tlock = self._audio_locks.setdefault(t.id, threading.Lock())  # type: ignore[attr-defined]
            is_current = self.current is not None and self.current.id == t.id  # type: ignore[attr-defined]

        with tlock:
            if t.cached_audio_path and t.cached_audio_path.is_file() and t.cached_audio_path.stat().st_size > 0:
                return t.cached_audio_path
            ext = ".aac" if t.source == "trackstack" else ".mp3"
            dest = cache_dir() / f"track_{t.source}_{t.inflyte_track_id}{ext}"
            if dest.is_file() and dest.stat().st_size > 0:
                t.cached_audio_path = dest
                return dest
            try:
                if not t.preview_url:
                    if t.source == "inflyte" and self.bridge is not None:  # type: ignore[attr-defined]
                        try:
                            t.preview_url = self.bridge.fetch_preview_audio_url(  # type: ignore[attr-defined]
                                t.release_id, t.inflyte_track_id, priority=is_current
                            )
                        except TypeError:
                            t.preview_url = self.bridge.fetch_preview_audio_url(t.release_id, t.inflyte_track_id)  # type: ignore[attr-defined]
                if not t.preview_url:
                    raise SessionError(f"No stream URL available for {t.name}")
                downloaded = self._download_file(t.preview_url, dest)
                if downloaded:
                    t.cached_audio_path = downloaded
                    return downloaded
            except Exception as exc:
                if not silent:
                    self.notify("warning", f"Could not stream {t.name}: {exc}")  # type: ignore[attr-defined]
        return None

    def _prefetch_nearby(self) -> None:
        """Pre-download Track 1 of the next 8 albums + Track 2/3 of the current and next 2 albums in parallel."""
        with self.lock:  # type: ignore[attr-defined]
            if not self.promos:  # type: ignore[attr-defined]
                return
            ordered_targets: list[InflyteTrack] = []
            seen: set[str] = set()

            def add_t(tr: InflyteTrack) -> None:
                if tr.id not in seen:
                    seen.add(tr.id)
                    ordered_targets.append(tr)

            cur_promo = self.promos[0]  # type: ignore[attr-defined]
            cur_idx = max(0, min(self.current_track_idx, len(cur_promo.tracks) - 1))  # type: ignore[attr-defined]
            # 1. Current track
            add_t(cur_promo.tracks[cur_idx])
            # 2. Next track in current promo (for ↓ Skip to Next Track) AND Track 1 of next promo (for ↑/→/← Next Album)
            if len(self.promos) > 1 and self.promos[1].tracks:  # type: ignore[attr-defined]
                add_t(self.promos[1].tracks[0])  # type: ignore[attr-defined]
            if cur_idx + 1 < len(cur_promo.tracks):
                add_t(cur_promo.tracks[cur_idx + 1])
            # 3. Track 1 of the next 8 albums so rapid album-to-album swiping never waits on network
            for p in self.promos[2:9]:  # type: ignore[attr-defined]
                if p.tracks:
                    add_t(p.tracks[0])
            # 4. Track 2 & Track 3 of the current and next 2 albums
            for p in self.promos[:3]:  # type: ignore[attr-defined]
                for t in p.tracks[1:3]:
                    add_t(t)

            to_submit: list[InflyteTrack] = []
            for tr in ordered_targets:
                if tr.id not in self._prefetched_ids:  # type: ignore[attr-defined]
                    self._prefetched_ids.add(tr.id)  # type: ignore[attr-defined]
                    to_submit.append(tr)

        def fetch_one(tr: InflyteTrack) -> None:
            try:
                cached_audio = self._ensure_audio_cached(tr.id, silent=True)
                self.cover(tr.id)  # type: ignore[attr-defined]
                # Post-download warmup: immediately calculate DSP analysis and waveform peaks
                if cached_audio and cached_audio.is_file():
                    from ...metadata import PeakStore, read_meta

                    read_meta(cached_audio)
                    if self.storage is not None:  # type: ignore[attr-defined]
                        digest = PeakStore(cache_dir() / "peaks", storage=self.storage)._digest(cached_audio)  # type: ignore[attr-defined]
                        if digest and not self.storage.get_peaks_bytes(digest):  # type: ignore[attr-defined]
                            from ... import waveform

                            raw = waveform.compute(cached_audio)
                            if raw is not None:
                                self.storage.put_peaks_bytes(digest, raw)  # type: ignore[attr-defined]
            except (SessionError, RateLimitError, urllib.error.URLError, OSError) as exc:
                logger.debug("Prefetch failed for track %s: %s", tr.id, exc)
                with self.lock:  # type: ignore[attr-defined]
                    self._prefetched_ids.discard(tr.id)  # type: ignore[attr-defined]

        for tr in to_submit:
            try:
                self._prefetch_pool.submit(fetch_one, tr)  # type: ignore[attr-defined]
            except RuntimeError:
                # Pool already closed during shutdown
                pass

    def stash_promos(self, target_count: int = 50) -> dict:
        """Pre-download audio and artwork for upcoming promos and store encrypted metadata in SQLite for offline flight use."""
        with self.lock:  # type: ignore[attr-defined]
            if self.stash_state.get("status") == "stashing":  # type: ignore[attr-defined]
                return dict(self.stash_state)  # type: ignore[attr-defined]
            promos_to_stash = list(self.promos[:target_count])  # type: ignore[attr-defined]
            self.stash_state.update(  # type: ignore[attr-defined]
                status="stashing",
                target_count=len(promos_to_stash),
                completed_promos=0,
                completed_tracks=0,
                total_bytes=0,
                message=f"Starting stash of {len(promos_to_stash)} promos…",
                error=None,
            )

        def run_stash() -> None:
            completed_promos = 0
            completed_tracks = 0
            total_bytes = 0
            try:
                for p in promos_to_stash:
                    # 1. Cache cover image
                    cover_data = None
                    try:
                        if p.cached_cover_path and p.cached_cover_path.is_file():
                            cover_data = p.cached_cover_path.read_bytes()
                        else:
                            cover_url = p.full_image_url or p.thumbnail_url
                            if cover_url:
                                dest_cov = cache_dir() / f"cover_{p.source}_{p.release_id}.jpg"
                                downloaded_cov = self._download_file(cover_url, dest_cov)
                                if downloaded_cov:
                                    p.cached_cover_path = downloaded_cov
                                    cover_data = downloaded_cov.read_bytes()
                    except Exception:
                        pass
                    if cover_data:
                        total_bytes += len(cover_data)

                    # 2. Cache audio for tracks
                    for tr in p.tracks:
                        try:
                            audio_path = self._ensure_audio_cached(tr.id, silent=True)
                            if audio_path and audio_path.is_file():
                                total_bytes += audio_path.stat().st_size
                                completed_tracks += 1
                                # Pre-analyze with libsonare
                                try:
                                    from ...metadata import read_meta

                                    read_meta(audio_path)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # 3. Cache serialized promo in SQLite promo_cache
                    if self.storage is not None:  # type: ignore[attr-defined]
                        try:
                            self.storage.put_promo_cache(p.source, p.promo_key, p.to_dict())  # type: ignore[attr-defined]
                        except Exception:
                            pass

                    completed_promos += 1
                    mb = total_bytes / (1024 * 1024)
                    with self.lock:  # type: ignore[attr-defined]
                        self.stash_state.update(  # type: ignore[attr-defined]
                            completed_promos=completed_promos,
                            completed_tracks=completed_tracks,
                            total_bytes=total_bytes,
                            message=f"Stashed {completed_promos}/{len(promos_to_stash)} promos ({mb:.1f} MB)",
                        )
                    self._changed()  # type: ignore[attr-defined]

                mb = total_bytes / (1024 * 1024)
                with self.lock:  # type: ignore[attr-defined]
                    self.stash_state.update(  # type: ignore[attr-defined]
                        status="ready",
                        message=f"Stash complete: {completed_promos} promos ready for offline use ({mb:.1f} MB)",
                    )
                self.notify("info", f"✈️ Stashed {completed_promos} promos for flight ({mb:.1f} MB)")  # type: ignore[attr-defined]
            except Exception as exc:
                with self.lock:  # type: ignore[attr-defined]
                    self.stash_state.update(  # type: ignore[attr-defined]
                        status="error",
                        error=str(exc),
                        message=f"Stash error: {exc}",
                    )
                self.notify("error", f"Stash failed: {exc}")  # type: ignore[attr-defined]
            finally:
                self._changed()  # type: ignore[attr-defined]

        t = threading.Thread(target=run_stash, daemon=True, name="promo-stash-worker")
        self._stash_thread = t  # type: ignore[attr-defined]
        t.start()
        return dict(self.stash_state)  # type: ignore[attr-defined]

    def get_stash_state(self) -> dict:
        with self.lock:  # type: ignore[attr-defined]
            state = dict(self.stash_state)  # type: ignore[attr-defined]
            queued_offline = self.storage.get_offline_review_count() if self.storage else 0  # type: ignore[attr-defined]
            state["queued_offline"] = queued_offline
            return state
