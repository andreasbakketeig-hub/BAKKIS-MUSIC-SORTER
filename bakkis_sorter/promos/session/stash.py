"""Promo session mixin for pre-fetching audio/covers and offline flight stashing."""

from __future__ import annotations

import os
import queue
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
    """Pre-downloading, cache verification, priority pre-fetching, and offline flight stashing."""

    def _mark_cur_ready_if_current(self, track_id: str) -> None:
        with self.lock:  # type: ignore[attr-defined]
            cur = self.current  # type: ignore[attr-defined]
            if cur is not None and cur.id == str(track_id):
                self._cur_ready_event.set()  # type: ignore[attr-defined]

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
        finally:
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
                self._mark_cur_ready_if_current(t.id)
                return t.cached_audio_path
            tlock = self._audio_locks.setdefault(t.id, threading.Lock())  # type: ignore[attr-defined]
            is_current = self.current is not None and self.current.id == t.id  # type: ignore[attr-defined]

        with tlock:
            if t.cached_audio_path and t.cached_audio_path.is_file() and t.cached_audio_path.stat().st_size > 0:
                self._mark_cur_ready_if_current(t.id)
                return t.cached_audio_path
            ext = ".aac" if t.source == "trackstack" else ".mp3"
            dest = cache_dir() / f"track_{t.source}_{t.inflyte_track_id}{ext}"
            if dest.is_file() and dest.stat().st_size > 0:
                t.cached_audio_path = dest
                self._mark_cur_ready_if_current(t.id)
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
                    self._mark_cur_ready_if_current(t.id)
                    return downloaded
            except Exception as exc:
                if not silent:
                    self.notify("warning", f"Could not stream {t.name}: {exc}")  # type: ignore[attr-defined]
        return None

    def _prefetch_nearby(self) -> None:
        """Schedule tiered priority pre-downloads for upcoming promos without blocking on DSP."""
        to_push: list[tuple[tuple[int, int, int, int], str]] = []
        with self.lock:  # type: ignore[attr-defined]
            if self._prefetch_closed or not self.promos:  # type: ignore[attr-defined]
                return
            self._prefetch_gen += 1  # type: ignore[attr-defined]
            gen = self._prefetch_gen  # type: ignore[attr-defined]
            ordered_targets: list[tuple[int, InflyteTrack]] = []
            seen: set[str] = set()

            def add_t(tier: int, tr: InflyteTrack) -> None:
                if tr.id not in seen:
                    seen.add(tr.id)
                    ordered_targets.append((tier, tr))

            cur_promo = self.promos[0]  # type: ignore[attr-defined]
            cur_idx = max(0, min(self.current_track_idx, len(cur_promo.tracks) - 1))  # type: ignore[attr-defined]
            cur_tr = cur_promo.tracks[cur_idx]
            if cur_tr.cached_audio_path and cur_tr.cached_audio_path.is_file() and cur_tr.cached_audio_path.stat().st_size > 0:
                self._cur_ready_event.set()  # type: ignore[attr-defined]
            else:
                self._cur_ready_event.clear()  # type: ignore[attr-defined]

            # Tier 0: Current track (uncontended priority)
            add_t(0, cur_tr)
            # Tier 1: Next promo Track 1 (swipe) & next track in current promo (skip/down)
            if len(self.promos) > 1 and self.promos[1].tracks:  # type: ignore[attr-defined]
                add_t(1, self.promos[1].tracks[0])  # type: ignore[attr-defined]
            if cur_idx + 1 < len(cur_promo.tracks):
                add_t(1, cur_promo.tracks[cur_idx + 1])
            # Tier 2: Track 1 of next 10 promos
            for p in self.promos[2:12]:  # type: ignore[attr-defined]
                if p.tracks:
                    add_t(2, p.tracks[0])
            # Tier 3: Tracks 2 & 3 of current and next promo
            for p in self.promos[:2]:  # type: ignore[attr-defined]
                for t in p.tracks[1:3]:
                    add_t(3, t)

            for idx, (tier, tr) in enumerate(ordered_targets):
                promo = self.track_to_promo.get(tr.id)  # type: ignore[attr-defined]
                audio_ok = bool(tr.cached_audio_path and tr.cached_audio_path.is_file() and tr.cached_audio_path.stat().st_size > 0)
                cover_ok = bool(promo and promo.cached_cover_path and promo.cached_cover_path.is_file())
                if audio_ok and (cover_ok or not (promo and (promo.thumbnail_url or promo.full_image_url))):
                    continue
                if tr.id in self._prefetch_running:  # type: ignore[attr-defined]
                    continue
                if self._prefetch_queued_gen.get(tr.id, 0) >= gen:  # type: ignore[attr-defined]
                    continue
                self._prefetch_queued_gen[tr.id] = gen  # type: ignore[attr-defined]
                to_push.append(((tier, -gen, idx, next(self._prefetch_seq)), tr.id))  # type: ignore[attr-defined]

        for item in to_push:
            self._prefetch_queue.put(item)  # type: ignore[attr-defined]

    def _prefetch_worker_loop(self) -> None:
        while True:
            prio, tr_id = self._prefetch_queue.get()  # type: ignore[attr-defined]
            if tr_id is None:
                return
            tier, neg_gen = prio[0], prio[1]
            gen = -neg_gen
            with self.lock:  # type: ignore[attr-defined]
                if self._prefetch_closed:  # type: ignore[attr-defined]
                    return
                if gen < self._prefetch_queued_gen.get(tr_id, 0):  # type: ignore[attr-defined]
                    continue
                tr = self.tracks.get(tr_id)  # type: ignore[attr-defined]
                promo = self.track_to_promo.get(tr_id)  # type: ignore[attr-defined]
                if tr is None or promo is None or promo not in self.promos or tr_id in self._prefetch_running:  # type: ignore[attr-defined]
                    self._prefetch_queued_gen.pop(tr_id, None)  # type: ignore[attr-defined]
                    continue
                need_wait = tier >= 2 and not self._cur_ready_event.is_set()  # type: ignore[attr-defined]

            if need_wait:
                self._cur_ready_event.wait(timeout=1.5)  # type: ignore[attr-defined]
                with self.lock:  # type: ignore[attr-defined]
                    if self._prefetch_closed:  # type: ignore[attr-defined]
                        return
                    if gen < self._prefetch_queued_gen.get(tr_id, 0):  # type: ignore[attr-defined]
                        continue
                    promo = self.track_to_promo.get(tr_id)  # type: ignore[attr-defined]
                    if promo is None or promo not in self.promos or tr_id in self._prefetch_running:  # type: ignore[attr-defined]
                        self._prefetch_queued_gen.pop(tr_id, None)  # type: ignore[attr-defined]
                        continue
                    self._prefetch_running.add(tr_id)  # type: ignore[attr-defined]
            else:
                with self.lock:  # type: ignore[attr-defined]
                    if tr_id in self._prefetch_running or promo not in self.promos:  # type: ignore[attr-defined]
                        continue
                    self._prefetch_running.add(tr_id)  # type: ignore[attr-defined]

            try:
                cached_audio = self._ensure_audio_cached(tr.id, silent=True)
                self.cover(tr.id)  # type: ignore[attr-defined]
                if cached_audio and cached_audio.is_file():
                    with self.lock:  # type: ignore[attr-defined]
                        submit_dsp = tr.id not in self._dsp_submitted and not self._prefetch_closed  # type: ignore[attr-defined]
                        if submit_dsp:
                            self._dsp_submitted.add(tr.id)  # type: ignore[attr-defined]
                    if submit_dsp:
                        try:
                            self._dsp_pool.submit(self._warm_promo_dsp, tr.id, cached_audio)  # type: ignore[attr-defined]
                        except RuntimeError:
                            pass
            except (SessionError, RateLimitError, urllib.error.URLError, OSError) as exc:
                logger.debug("Prefetch failed for track %s: %s", tr.id, exc)
            finally:
                with self.lock:  # type: ignore[attr-defined]
                    self._prefetch_running.discard(tr_id)  # type: ignore[attr-defined]
                    self._prefetch_queued_gen.pop(tr_id, None)  # type: ignore[attr-defined]

    def _warm_promo_dsp(self, track_id: str, audio_path: Path) -> None:
        """Compute waveform peaks + libsonare DSP on low-priority worker only for active upcoming promos."""
        with self.lock:  # type: ignore[attr-defined]
            if self._prefetch_closed:  # type: ignore[attr-defined]
                return
            promo = self.track_to_promo.get(track_id)  # type: ignore[attr-defined]
            if promo is None or promo not in self.promos[:5]:  # type: ignore[attr-defined]
                return
        if not audio_path.is_file():
            return
        try:
            from ... import waveform
            from ...analyzer import analyze_audio_file
            from ...metadata import content_digest, file_digest

            st = self.storage  # type: ignore[attr-defined]
            if st is None:
                return
            digest = content_digest(audio_path)
            if digest and not st.get_peaks_bytes(digest):
                raw = waveform.compute(audio_path)
                if raw is not None:
                    st.put_peaks_bytes(digest, raw)
            f_hash = file_digest(audio_path)
            if f_hash and not st.get_analysis(f_hash):
                analysis = analyze_audio_file(audio_path)
                st.put_analysis(
                    file_hash=f_hash,
                    bpm=analysis.bpm,
                    musical_key=analysis.musical_key,
                    camelot_key=analysis.camelot_key,
                    drop_seconds=analysis.drop_seconds,
                    jump_delta=analysis.jump_delta,
                    confidence=analysis.confidence,
                )
        except Exception as exc:
            logger.debug("Promo DSP warmup error for %s: %s", track_id, exc)

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
                                    with self.lock:  # type: ignore[attr-defined]
                                        p.cached_cover_path = downloaded_cov
                                    cover_data = downloaded_cov.read_bytes()
                    except Exception:
                        pass
                    if cover_data:
                        total_bytes += len(cover_data)

                    # 2. Cache audio for tracks at full network speed (DSP runs lazily on play)
                    for tr in p.tracks:
                        try:
                            audio_path = self._ensure_audio_cached(tr.id, silent=True)
                            if audio_path and audio_path.is_file():
                                total_bytes += audio_path.stat().st_size
                                completed_tracks += 1
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
