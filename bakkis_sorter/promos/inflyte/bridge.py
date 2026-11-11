"""Inflyte WebView and API bridge for communicating with inflyteapp.com."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ...bridge import BaseWebViewBridge
from ...config import config_dir
from ...logger import get_logger
from ...ratelimit import RateLimitError, RateLimiter, call_with_backoff
from ...session import SessionError
from ...storage import EncryptedStorage, get_storage
from ...types import InflyteAuthState
from ..models import InflytePromo, InflyteTrack, _INFLYTE_TRACK_IDS

logger = get_logger("inflyte")

INFLYTE_BASE_URL = "https://inflyteapp.com"
INFLYTE_PROMOS_URL = "https://inflyteapp.com/promos"


def cache_dir() -> Path:
    d = config_dir() / "inflyte_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_debug(event: str, **data: Any) -> None:
    try:
        log_path = config_dir() / "inflyte_debug.log"
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class InflyteBridge(BaseWebViewBridge):
    """Communicates with ``https://inflyteapp.com`` via an authenticated pywebview window."""

    def __init__(
        self,
        *,
        storage: Optional[EncryptedStorage] = None,
        limiter: Optional[RateLimiter] = None,
    ) -> None:
        super().__init__(window_title="Inflyte Promo Box — Connect", url=INFLYTE_PROMOS_URL, width=1040, height=740)
        self.storage = storage or get_storage()
        self.limiter = limiter or RateLimiter(rate_per_sec=4.0, burst=10)

        # Restore cached connection state from encrypted SQLite
        cached_conn = self.storage.get_kv("inflyte_connected")
        if cached_conn is None:
            try:
                # If previously used Inflyte (e.g. have reviewed promos), infer connected
                cached_conn = len(self.storage.get_reviewed_keys("inflyte")) > 0
                if cached_conn:
                    self.storage.put_kv("inflyte_connected", True)
            except Exception:
                cached_conn = False
        self._is_connected: bool = bool(cached_conn)

    def check_auth_status(self) -> dict:
        """Non-blocking check returning InflyteAuthState dictionary without popping a window."""
        with self._lock:
            win = self._win
        if win is None:
            return InflyteAuthState(connected=self._is_connected, window_open=False, href="").to_dict()
        check_js = """
        (function() {
            const href = String(location.href || '');
            const title = String(document.title || '');
            const bodyLen = document.body ? document.body.innerHTML.length : 0;
            const isChallenge = title.includes('Just a moment') || !!document.querySelector('#challenge-error-text');
            const isLogin = href.includes('/users/sign_in') || href.includes('/check_login') || !!document.querySelector('form[action*="sign_in"]');
            return {
                href,
                isLogin,
                ready: !isChallenge && !isLogin && href.includes('inflyteapp.com') && bodyLen > 500
            };
        })()
        """
        try:
            st = self._eval_js(check_js, timeout=3.0, visible=False) or {}
            ready = bool(st.get("ready"))
            is_login = bool(st.get("isLogin"))
            if ready:
                self._is_connected = True
                try:
                    self.storage.put_kv("inflyte_connected", True)
                except Exception:
                    pass
                if self._login_window_shown:
                    try:
                        self._login_window_shown = False
                        win.hide()
                    except (AttributeError, RuntimeError):
                        pass
            elif is_login:
                self._is_connected = False
                try:
                    self.storage.put_kv("inflyte_connected", False)
                except Exception:
                    pass
            return InflyteAuthState(
                connected=ready or self._is_connected,
                window_open=True,
                href=str(st.get("href", "")),
            ).to_dict()
        except (SessionError, TimeoutError):
            return InflyteAuthState(connected=self._is_connected, window_open=False, href="").to_dict()

    def open_login_window(self) -> dict:
        """Open or bring to front the Inflyte WKWebView window so the user can log in."""
        win = self._ensure_window(visible=True)
        self._bring_to_front(win)
        st = self.check_auth_status()
        return {"connected": bool(st.get("connected")), "href": st.get("href", INFLYTE_PROMOS_URL)}

    def ensure_authenticated(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        """Verify the Inflyte WKWebView is on /promos and logged in; show login window if needed."""
        win = self._ensure_window()
        check_js = """
        (function() {
            const href = String(location.href || '');
            const title = String(document.title || '');
            const bodyLen = document.body ? document.body.innerHTML.length : 0;
            const isChallenge = title.includes('Just a moment') || !!document.querySelector('#challenge-error-text');
            const isLogin = href.includes('/users/sign_in') || href.includes('/check_login') || !!document.querySelector('form[action*="sign_in"]');
            const hasPromosDom = !!(
                (window.promo_data && window.promo_data.promos) ||
                document.querySelector('[data-release-id], [data-promo-id], [data-id], a[href*="show="], a[href*="/review/releases/"], .promo, .release, #promos, #releases')
            );
            return {
                href,
                title,
                bodyLen,
                isChallenge,
                isLogin,
                hasPromosDom,
                ready: !isChallenge && !isLogin && href.includes('inflyteapp.com') && bodyLen > 500
            };
        })()
        """
        st = self._eval_js(check_js) or {}
        _log_debug("auth_check_initial", state=st)
        if st.get("ready") and ("/promos" in str(st.get("href", "")) or "/review" in str(st.get("href", ""))):
            self._is_connected = True
            try:
                self.storage.put_kv("inflyte_connected", True)
            except Exception:
                pass
            try:
                win.hide()
            except (AttributeError, RuntimeError):
                pass
            return

        self._bring_to_front(win)
        if on_status:
            on_status("Sign into Inflyte in the popup window (promos will load automatically once signed in)…")

        deadline = time.time() + 240.0
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                st = self._eval_js(check_js, timeout=5.0) or {}
            except (SessionError, TimeoutError):
                continue
            href = str(st.get("href", ""))
            if st.get("isLogin") or st.get("isChallenge"):
                if on_status:
                    msg = "Completing Cloudflare check…" if st.get("isChallenge") else "Waiting for Inflyte sign-in in the popup window…"
                    on_status(msg)
                continue
            if st.get("ready"):
                if "/promos" not in href and "/review" not in href:
                    if on_status:
                        on_status("Signed in! Opening Inflyte Promo Box…")
                    try:
                        win.load_url(INFLYTE_PROMOS_URL)
                    except (AttributeError, RuntimeError):
                        pass
                    time.sleep(1.5)
                    continue
                # Wait briefly for Inflyte's JS to render the promo cards on /promos
                for _ in range(6):
                    if st.get("hasPromosDom"):
                        break
                    time.sleep(0.5)
                    try:
                        st = self._eval_js(check_js, timeout=4.0) or {}
                    except (SessionError, TimeoutError):
                        break
                self._is_connected = True
                try:
                    self.storage.put_kv("inflyte_connected", True)
                except Exception:
                    pass
                try:
                    win.hide()
                except (AttributeError, RuntimeError):
                    pass
                return
        raise SessionError("Timed out waiting for Inflyte sign-in.")

    def fetch_unreviewed_release_ids(self, on_status: Optional[Callable[[str], None]] = None) -> list[int]:
        """Extract all unreviewed release IDs from https://inflyteapp.com/promos."""
        self.ensure_authenticated(on_status=on_status)
        if on_status:
            on_status("Reading unreviewed promos from Inflyte Promo Box…")

        extract_js = """
        (function() {
            const ids = [];
            const seen = new Set();
            const add = (v) => {
                const n = parseInt(v, 10);
                if (n && n > 1000 && !seen.has(n)) { seen.add(n); ids.push(n); }
            };

            const collectFromObj = (obj) => {
                if (!obj || typeof obj !== 'object') return;
                const inner = obj.promo_data && typeof obj.promo_data === 'object' ? obj.promo_data : obj;
                for (const arrKey of ['promos', 'releases', 'items', 'unreviewed', 'data']) {
                    const arr = inner[arrKey];
                    if (Array.isArray(arr)) {
                        for (const p of arr) {
                            if (p && typeof p === 'object') {
                                if (!p.reviewed && !p.deleted) add(p.id || p.release_id || p.releaseId);
                            } else {
                                add(p);
                            }
                        }
                    }
                }
            };

            // 1. Check global JS objects (window.initialData, window.PromoManager, window.promo_data, etc.)
            for (const key of ['initialData', 'promo_data', 'initial_data', 'PromoManager', 'InflyteApp', 'ReleasesHelper', 'ReviewReleasesHelper', 'gon']) {
                collectFromObj(window[key]);
            }
            if (Array.isArray(window.promos)) {
                for (const p of window.promos) {
                    if (!p.reviewed && !p.deleted) add(p.id || p.release_id);
                }
            }

            // Only fall back to scraping DOM / HTML regexes if window.initialData didn't provide the unreviewed list
            if (ids.length === 0) {
                // 2. Scan DOM attributes (including data-releaseid used by .promo_card)
                for (const el of document.querySelectorAll('[data-releaseid], [data-release-id], [data-promo-id], [data-id], [id^="release_"], [id^="promo_"], a[href]')) {
                    add(el.getAttribute('data-releaseid') || el.getAttribute('data-release-id') || el.getAttribute('data-promo-id') || el.getAttribute('data-id'));
                    const idAttr = el.getAttribute('id') || '';
                    let mId = /^(?:release|promo)_(\\d+)$/i.exec(idAttr);
                    if (mId) add(mId[1]);
                    const href = el.getAttribute('href') || '';
                    let m = /[?&]show=(\\d+)/.exec(href) || /\\/(?:review\\/)?releases\\/(\\d+)/.exec(href) || /\\/promos\\/(\\d+)/.exec(href);
                    if (m) add(m[1]);
                }

                // 3. Scan inline scripts and full HTML for release patterns
                const html = document.documentElement ? document.documentElement.innerHTML : '';
                const patterns = [
                    /data-releaseid=["'](\\d+)["']/g,
                    /\\/review\\/releases\\/(\\d+)/g,
                    /[?&]show=(\\d+)/g,
                    /"release_id"\\s*:\\s*(\\d+)/g,
                    /data-release-id=["'](\\d+)["']/g,
                    /data-promo-id=["'](\\d+)["']/g
                ];
                for (const re of patterns) {
                    let m;
                    while ((m = re.exec(html)) !== null) add(m[1]);
                }
            }

            const summarizeObj = (o) => {
                if (!o || typeof o !== 'object') return typeof o;
                const out = {};
                for (const k of Object.keys(o).slice(0, 30)) {
                    const v = o[k];
                    if (Array.isArray(v)) {
                        out[k] = { type: 'array', len: v.length, sampleKeys: (v[0] && typeof v[0] === 'object') ? Object.keys(v[0]).slice(0, 20) : typeof v[0] };
                    } else if (v && typeof v === 'object') {
                        out[k] = { type: 'object', keys: Object.keys(v).slice(0, 20) };
                    } else if (typeof v === 'function') {
                        out[k] = 'fn:' + String(v).slice(0, 160);
                    } else {
                        out[k] = typeof v === 'string' ? v.slice(0, 100) : v;
                    }
                }
                return out;
            };

            const promosArr = (window.initialData && Array.isArray(window.initialData.promos)) ? window.initialData.promos : [];
            const tracksArr = (window.initialData && Array.isArray(window.initialData.tracks)) ? window.initialData.tracks : [];
            const breakdown = {
                totalPromos: promosArr.length,
                totalTracks: tracksArr.length,
                reviewedFalse: promosArr.filter(p => !p.reviewed && !p.deleted).length,
                reviewedTrue: promosArr.filter(p => !!p.reviewed).length,
                deletedTrue: promosArr.filter(p => !!p.deleted).length,
                ratingZero: promosArr.filter(p => !p.rating || p.rating === 0).length,
                ratingPos: promosArr.filter(p => p.rating && p.rating > 0).length,
                samplePromos: promosArr.slice(0, 5).map(p => ({
                    id: p.id,
                    reviewed: p.reviewed,
                    deleted: p.deleted,
                    rating: p.rating,
                    viewed: p.viewed,
                    tracks: p.tracks
                })),
                baseDataSafe: window.initialData && window.initialData.base_data ? {
                    total_sent: window.initialData.base_data.total_sent,
                    sync_enabled: window.initialData.base_data.sync_enabled,
                    auto_play: window.initialData.base_data.auto_play
                } : null
            };

            const diag = {
                href: location.href,
                title: document.title,
                promoCardCount: document.querySelectorAll('.promo_card, [data-releaseid]').length,
                breakdown
            };
            return { ids, diag };
        })()
        """
        res = self._eval_js(extract_js) or {}
        raw_ids = res.get("ids") if isinstance(res, dict) else res
        diag = res.get("diag") if isinstance(res, dict) else {}
        _log_debug("extracted_release_ids", count=len(raw_ids or []), ids=(raw_ids or [])[:25], diag=diag)

        if not raw_ids:
            # Wait 2s for SPA/AJAX render on /promos and retry once
            time.sleep(2.0)
            res = self._eval_js(extract_js) or {}
            raw_ids = res.get("ids") if isinstance(res, dict) else res
            diag = res.get("diag") if isinstance(res, dict) else {}
            _log_debug("extracted_release_ids_retry", count=len(raw_ids or []), ids=(raw_ids or [])[:25], diag=diag)

        return [int(x) for x in (raw_ids or []) if str(x).isdigit()]

    def _build_promo(self, p: dict, tracks_list: list[dict]) -> Optional[InflytePromo]:
        release_id = int(p.get("id") or p.get("release_id") or 0)
        if not release_id or not tracks_list:
            return None
        total_tracks = len(tracks_list)
        tracks: list[InflyteTrack] = []
        for idx, t in enumerate(tracks_list):
            tid = int(t.get("id") or 0)
            if not tid:
                continue
            tracks.append(
                InflyteTrack(
                    id=str(next(_INFLYTE_TRACK_IDS)),
                    inflyte_track_id=tid,
                    release_id=release_id,
                    name=str(t.get("name") or f"Track {idx + 1}").strip(),
                    artist_name=str(t.get("artist_name") or "").strip(),
                    duration=float(t.get("duration") or 0.0),
                    track_index=idx,
                    track_total=total_tracks,
                    peak_url=str(t.get("peak_url") or f"/peak/{tid}"),
                )
            )
        if not tracks:
            return None
        for idx, tr in enumerate(tracks):
            tr.track_index = idx
            tr.track_total = len(tracks)
        return InflytePromo(
            release_id=release_id,
            name=str(p.get("name") or f"Promo {release_id}").strip(),
            sender_label=str(p.get("sender_label") or "").replace("] [", " · ").strip(" []"),
            release_date=str(p.get("release_date") or "").strip(),
            thumbnail_url=str(p.get("thumbnail") or "").strip(),
            full_image_url=str(p.get("full_image") or p.get("thumbnail") or "").strip(),
            summary=str(p.get("summary") or "").strip(),
            tracks=tracks,
        )

    def fetch_initial_promos(
        self, on_status: Optional[Callable[[str], None]] = None
    ) -> tuple[list[InflytePromo], list[int]]:
        """Hydrate unreviewed promos directly from ``window.initialData`` (and inline script JSON)
        so all pages load in one shot; return ``(hydrated_promos, remaining_release_ids)``."""
        self.ensure_authenticated(on_status=on_status)
        if on_status:
            on_status("Reading all unreviewed promos from Inflyte Promo Box…")

        js = """
        (function() {
            let promos = (window.initialData && Array.isArray(window.initialData.promos)) ? window.initialData.promos : [];
            let tracks = (window.initialData && Array.isArray(window.initialData.tracks)) ? window.initialData.tracks : [];

            // If PromoManager already consumed window.initialData.tracks, recover it from the inline <script> tag
            if (tracks.length === 0 || promos.length === 0) {
                for (const s of document.querySelectorAll('script:not([src])')) {
                    const txt = s.textContent || '';
                    const idx = txt.indexOf('initialData');
                    if (idx === -1) continue;
                    const braceStart = txt.indexOf('{', idx);
                    if (braceStart === -1) continue;
                    // Find matching closing brace or semicolon before next var/function
                    let depth = 0, inStr = false, esc = false, endIdx = -1;
                    for (let i = braceStart; i < txt.length; i++) {
                        const c = txt[i];
                        if (esc) { esc = false; continue; }
                        if (c === '\\\\') { esc = true; continue; }
                        if (c === '"') { inStr = !inStr; continue; }
                        if (!inStr) {
                            if (c === '{') depth++;
                            else if (c === '}') {
                                depth--;
                                if (depth === 0) { endIdx = i + 1; break; }
                            }
                        }
                    }
                    if (endIdx !== -1) {
                        try {
                            const parsed = JSON.parse(txt.slice(braceStart, endIdx));
                            if (Array.isArray(parsed.promos) && promos.length === 0) promos = parsed.promos;
                            if (Array.isArray(parsed.tracks) && tracks.length === 0) tracks = parsed.tracks;
                            break;
                        } catch (_) {}
                    }
                }
            }

            const unreviewed = promos.filter(p => p && !p.reviewed && !p.deleted && (p.id || p.release_id));
            return {
                totalPromosInBox: promos.length,
                unreviewedCount: unreviewed.length,
                tracksCount: tracks.length,
                promos: unreviewed,
                tracks: tracks
            };
        })()
        """
        res = self._eval_js(js) or {}
        if not isinstance(res, dict) or not res.get("promos"):
            fallback_ids = self.fetch_unreviewed_release_ids(on_status=on_status)
            return [], fallback_ids

        raw_promos = res.get("promos") or []
        raw_tracks = res.get("tracks") or []
        _log_debug(
            "fetch_initial_promos",
            total_in_box=res.get("totalPromosInBox"),
            unreviewed_count=res.get("unreviewedCount"),
            tracks_count=res.get("tracksCount"),
        )

        tracks_by_release: dict[int, list[dict]] = {}
        for t in raw_tracks:
            if not isinstance(t, dict):
                continue
            rid = int(t.get("release_id") or 0)
            if rid:
                tracks_by_release.setdefault(rid, []).append(t)

        reviewed_keys = self.storage.get_reviewed_keys("inflyte")
        hydrated: list[InflytePromo] = []
        remaining_ids: list[int] = []
        for p in raw_promos:
            if not isinstance(p, dict):
                continue
            rid = int(p.get("id") or p.get("release_id") or 0)
            if not rid or str(rid) in reviewed_keys:
                continue
            t_list = tracks_by_release.get(rid) or (
                p.get("tracks") if isinstance(p.get("tracks"), list) and p.get("tracks") and isinstance(p["tracks"][0], dict) else []
            )
            if not t_list:
                cached = self._read_cached_release(rid)
                if cached and isinstance(cached.get("tracks"), list):
                    t_list = cached["tracks"]
            if t_list:
                built = self._build_promo(p, t_list)
                if built is not None:
                    self._write_cached_release(rid, p, t_list)
                    hydrated.append(built)
                    continue
            remaining_ids.append(rid)

        return hydrated, remaining_ids

    def _read_cached_release(self, release_id: int) -> Optional[dict]:
        """Read release metadata & tracklist from the encrypted SQLite database (``bakkis.db``)."""
        data = self.storage.get_promo_cache("inflyte", str(int(release_id)))
        if isinstance(data, dict) and data.get("tracks"):
            return data
        return None

    def _write_cached_release(self, release_id: int, promo_dict: dict, tracks_list: list[dict]) -> None:
        """Persist release metadata & tracklist in the encrypted SQLite database (``bakkis.db``)."""
        if not tracks_list:
            return
        try:
            self.storage.put_promo_cache(
                "inflyte",
                str(int(release_id)),
                {"promo": promo_dict, "tracks": tracks_list},
            )
        except (sqlite3.Error, OSError) as exc:
            logger.debug("Non-fatal: could not write promo cache to bakkis.db: %s", exc)

    def _xhr_json(
        self,
        method: str,
        path: str,
        form_body: Optional[dict[str, str]] = None,
        *,
        priority: bool = False,
    ) -> dict:
        """Run an XMLHttpRequest inside the Inflyte WKWebView with CSRF headers, rate limiting, and exponential backoff."""
        encoded_body = urllib.parse.urlencode(form_body) if form_body else ""

        def do_call() -> dict:
            js = f"""
            (function() {{
                const csrf = (document.querySelector('meta[name="csrf-token"]') || {{}}).content || '';
                const xhr = new XMLHttpRequest();
                xhr.open({json.dumps(method)}, {json.dumps(path)}, false);
                xhr.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
                xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
                if ({json.dumps(bool(encoded_body))}) {{
                    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8');
                    xhr.send({json.dumps(encoded_body)});
                }} else {{
                    xhr.send(null);
                }}
                let data = null;
                try {{ data = JSON.parse(xhr.responseText); }} catch (_) {{ data = {{ raw: xhr.responseText }}; }}
                const retryAfter = xhr.getResponseHeader('Retry-After');
                return {{
                    status: xhr.status,
                    retryAfter: retryAfter ? parseFloat(retryAfter) : null,
                    data: data
                }};
            }})()
            """
            res = self._eval_js(js) or {}
            status = int(res.get("status") or 0)
            if status in (429, 502, 503, 504):
                _log_debug("xhr_rate_limit", method=method, path=path, status=status)
                raise RateLimitError(
                    f"Inflyte HTTP {status} on {method} {path}",
                    status_code=status,
                    retry_after=res.get("retryAfter"),
                )
            if status < 200 or status >= 300:
                _log_debug("xhr_error", method=method, path=path, status=status)
                raise SessionError(f"Inflyte HTTP {status} on {method} {path}")
            data = res.get("data")
            return data if isinstance(data, dict) else {"result": data}

        return call_with_backoff(do_call, limiter=self.limiter, priority=priority)

    def fetch_release(self, release_id: int) -> tuple[Optional[InflytePromo], list[int]]:
        """Call ``GET /review/releases/<id>.json`` (with encrypted SQLite cache) and return ``(InflytePromo, extra_release_ids)``."""
        if self.storage.is_reviewed("inflyte", str(int(release_id))):
            return None, []
        cached = self._read_cached_release(release_id)
        if cached and isinstance(cached.get("promo"), dict) and isinstance(cached.get("tracks"), list):
            built = self._build_promo(cached["promo"], cached["tracks"])
            if built is not None:
                return built, []

        payload = self._xhr_json("GET", f"/review/releases/{int(release_id)}.json", priority=False)
        promo_data = payload.get("promo_data") or payload
        promos_list = promo_data.get("promos") or []
        tracks_list = promo_data.get("tracks") or []

        # Also collect any other unreviewed promo IDs returned in promo_data.promos
        extra_ids: list[int] = []
        for item in promos_list:
            if isinstance(item, dict) and not item.get("reviewed") and not item.get("deleted") and item.get("id"):
                extra_ids.append(int(item["id"]))

        if not promos_list or not tracks_list:
            return None, extra_ids
        p = next((x for x in promos_list if int(x.get("id") or 0) == int(release_id)), promos_list[0])
        if p.get("reviewed") or p.get("deleted"):
            return None, extra_ids

        promo = self._build_promo(p, tracks_list)
        if promo is not None:
            self._write_cached_release(release_id, p, tracks_list)
        return promo, extra_ids

    def fetch_preview_audio_url(self, release_id: int, inflyte_track_id: int, *, priority: bool = False) -> str:
        res = self._xhr_json(
            "GET",
            f"/review/releases/{int(release_id)}/tracks/{int(inflyte_track_id)}/preview_audio_url",
            priority=priority,
        )
        url = str(res.get("url") or "").strip()
        if not url:
            raise SessionError(f"No preview audio URL returned for track {inflyte_track_id}")
        return url

    def mark_track_played(self, release_id: int, inflyte_track_id: int) -> None:
        self._xhr_json(
            "POST",
            f"/review/releases/{int(release_id)}/tracks/{int(inflyte_track_id)}/played",
            {"count": "1"},
            priority=False,
        )

    def submit_feedback(self, release_id: int, favourite_track_id: int, rating: int, comments: str) -> dict:
        """Submit 5/5 or 4/5 rating + feedback comment (triggers Inflyte Dropbox auto-sync)."""
        return self._xhr_json(
            "POST",
            f"/review/releases/{int(release_id)}",
            {
                "sharing[rating]": str(int(rating)),
                "sharing[favourite_track_id]": str(int(favourite_track_id)),
                "sharing[comments]": str(comments),
                "_method": "PUT",
                "presenter": "true",
            },
            priority=True,
        )

    def dismiss_promo(self, release_id: int) -> dict:
        """Dismiss ('Not for me') a promo from the Inflyte inbox without feedback or download."""
        return self._xhr_json(
            "POST",
            f"/review/releases/{int(release_id)}.json",
            {
                "_method": "DELETE",
                "presenter": "true",
            },
            priority=True,
        )
