/* BAKKIS MUSIC SORTER v2 — frontend. Plain JS, no build step.
 *
 * Talks to the Python backend over POST /api/<method> (see api.py). Audio streams from /audio/<id>.
 * Two <audio> elements: `cur` plays, `next` preloads the upcoming track at its start point so a
 * sort decision swaps elements and is audible immediately.
 */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const JUMPS = [0, 30, 60, 90, 120, 180, 240, 300]; // keys 1–8; key 9 = outro
const OUTRO_SEC = 60;
const SEEK_STEP = 30;
const DRAG_THRESHOLD = 38;
const WAVE_PEAKS = 800;
const WAVE_MAX_BYTES = 200 * 1024 * 1024;
const WAVE_MAX_SEC = 20 * 60;
const POLL_MS = 1000;
const MAX_AUTO_SKIPS = 3; // tracks that may end and auto-skip in a row without any user input
const WAVE_DECODE_RATE = 4000; // Hz; plenty for 800 peaks, ~10x less memory than full-rate decoding

const $ = (id) => document.getElementById(id);
const body = document.body;

// ------------------------------------------------------------------ state

const S = {
  settings: null,
  presets: null,
  scan: null,
  session: null,
  mode: "swipe",
  counts: [],
  meta: new Map(), // track id -> meta
  noticeSeq: 0,
  busy: false, // a sort/skip/undo request is in flight
  pendingDecisionAt: 0, // performance.now() of the last decision (latency measurement)
  autoSkips: 0, // consecutive automatic skips since the last user action
  coachSorts: 0, // user decisions while the first-session hint is showing
  restoredIds: new Set(), // tracks brought back by undo: never auto-skip these again
  latencies: [],
};
window.__bakkis = S; // handy for debugging in Web Inspector

// ------------------------------------------------------------------ api

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(method, params = {}) {
  const res = await fetch(`/api/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Bakkis-Token": TOKEN },
    body: JSON.stringify(params),
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) throw new ApiError((data && data.error) || res.statusText, res.status);
  return data.result;
}

const audioUrl = (id) => `/audio/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;
const coverUrl = (id) => `/cover/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;

// ------------------------------------------------------------------ helpers

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/** A colour token from :root in app.css, e.g. token("--accent"). Cached: the waveform asks every frame. */
const tokenCache = {};
const token = (name) => (tokenCache[name] ??= getComputedStyle(document.documentElement).getPropertyValue(name).trim());

function activePreset(mode = S.mode) {
  if (!S.presets) return null;
  const id = S.presets[`active_${mode}_preset_id`];
  return S.presets.presets.find((p) => p.id === id) || null;
}

function trackDisplay(track, meta) {
  if (meta && meta.title) return { title: meta.title, artist: meta.artist || "" };
  if (meta && meta.display && meta.display !== track.name) {
    const [t, a] = meta.display.split(" — ");
    return { title: t, artist: a || meta.artist || "" };
  }
  return { title: track.name.replace(/\.[^.]+$/, ""), artist: (meta && meta.artist) || "" };
}

async function getMeta(id) {
  if (S.meta.has(id)) return S.meta.get(id);
  const m = await api("track_meta", { track_id: id });
  S.meta.set(id, m);
  if (S.meta.size > 300) S.meta.delete(S.meta.keys().next().value);
  return m;
}

// ------------------------------------------------------------------ sound (WebAudio, no files)

let audioCtx = null;
function ctx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

/** Create the WebAudio context ahead of the first sort: setting up the audio device can take ~200 ms. */
function warmUpAudio() {
  if (audioCtx || !S.settings || !S.settings.sort_confirmation_sound) return;
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 300));
  idle(() => { try { ctx(); } catch (_) { /* optional */ } });
}

function clickSound() {
  if (!S.settings || !S.settings.sort_confirmation_sound) return;
  try {
    const c = ctx();
    const t = c.currentTime;
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(1400, t);
    osc.frequency.exponentialRampToValueAtTime(700, t + 0.04);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.08, t + 0.004);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    osc.connect(gain).connect(c.destination);
    osc.start(t);
    osc.stop(t + 0.07);
  } catch (_) { /* sound is optional */ }
}

// ------------------------------------------------------------------ player

const Player = {
  cur: new Audio(),
  next: new Audio(),
  curId: null,
  nextId: null,
  activeStart: 0, // the active jump point (R replays from here)
  userPaused: false,

  init() {
    for (const a of [this.cur, this.next]) {
      a.preload = "auto";
      a.addEventListener("ended", () => a === this.cur && onTrackEnded());
      // timeupdate keeps firing while the window is hidden (requestAnimationFrame does not).
      a.addEventListener("timeupdate", () => { if (a === this.cur) { EndWatch.tick(); Listen.tick(); } });
      a.addEventListener("error", () => a === this.cur && a.src && onPlaybackError());
      a.addEventListener("playing", () => a === this.cur && onPlaying());
      a.addEventListener("pause", () => a === this.cur && body.classList.remove("playing"));
      a.addEventListener("durationchange", () => a === this.cur && Wave.markersDirty());
    }
  },

  vol: 0.8,

  setVolume(v) {
    this.vol = Math.max(0, Math.min(1, v / 100));
    this.cur.volume = this.next.volume = this.vol;
  },

  /** Listen mode: fade the preloaded track in while the current one fades out. */
  crossfadeTo(id, ms) {
    const to = this.next, from = this.cur;
    if (this.nextId !== id || to.readyState < 2) { this.load(id); return; }
    to.muted = false;
    to.volume = 0;
    this.cur = to;
    this.next = from;
    this.curId = id;
    this.nextId = null;
    EndWatch.reset(id);
    this.activeStart = Number(to.dataset.start || 0);
    if (!this.userPaused) this.play();
    Wave.markersDirty();
    const t0 = performance.now();
    this.fadingUntil = t0 + ms;
    const timer = setInterval(() => {
      const k = Math.min(1, (performance.now() - t0) / ms);
      to.volume = this.vol * k;
      from.volume = this.vol * (1 - k);
      if (k >= 1) {
        clearInterval(timer);
        this.fadingUntil = 0;
        from.pause();
        from.removeAttribute("src");
        from.load();
        from.volume = this.vol;
      }
    }, 30);
  },

  /** The chosen start point, or, when the track is too short for it, about a third of the way in. */
  startPointFor(duration, id) {
    if (Listen.active && id) return Listen.startFor(id, duration);
    const start = (S.settings && S.settings.playback_start_seconds) || 0;
    if (!isFinite(duration) || duration <= 0) return start;
    if (duration >= start + 30) return start;
    if (duration < 60) return 0;
    return Math.floor((duration / 3) / 15) * 15; // e.g. a 3:30 radio edit with "5:00" starts at 1:00
  },

  /** Make `id` the playing track. Uses the preloaded element when it holds this track. */
  load(id, autoplay = true) {
    if (id === this.curId) return;
    this.fadingUntil = 0;
    const old = this.cur;
    if (id === this.nextId) {
      this.cur = this.next;
      this.next = old;
    } else {
      this.cur.src = audioUrl(id);
      this.cur.dataset.start = "";
      this.cur.dataset.ready = "";
    }
    this.curId = id;
    this.nextId = null;
    EndWatch.reset(id);
    old.pause();
    if (old !== this.cur) {
      old.removeAttribute("src");
      old.load();
    }
    this.cur.muted = false;
    const a = this.cur;
    const begin = () => {
      if (a !== this.cur) return;
      if (a.dataset.start === "") a.dataset.start = String(this.startPointFor(a.duration, this.curId));
      this.activeStart = Number(a.dataset.start);
      if (Math.abs(a.currentTime - this.activeStart) > 0.25) a.currentTime = this.activeStart;
      if (autoplay && !this.userPaused) this.play();
      Wave.markersDirty();
    };
    if (a.error) {
      // The preloaded copy already failed (its "error" event fired while it was the hidden element).
      setTimeout(() => { if (a === this.cur && this.curId === id) onPlaybackError(); }, 0);
      return;
    }
    if (a.readyState >= 1) begin();
    else a.addEventListener("loadedmetadata", begin, { once: true });
  },

  fadingUntil: 0,

  /** Load `id` into the hidden element, seeked to its start point. */
  preload(id) {
    if (!id || id === this.nextId || id === this.curId) return;
    const wait = this.fadingUntil - performance.now();
    if (wait > 0) { setTimeout(() => this.preload(id), wait + 50); return; } // the hidden element is still fading out
    const a = this.next;
    this.nextId = id;
    a.muted = true;
    a.dataset.start = "";
    a.src = audioUrl(id);
    a.addEventListener("loadedmetadata", () => {
      if (a !== this.next || this.nextId !== id) return;
      a.dataset.start = String(this.startPointFor(a.duration, id));
      a.currentTime = Number(a.dataset.start);
    }, { once: true });
  },

  clear() {
    for (const a of [this.cur, this.next]) { a.pause(); a.removeAttribute("src"); a.load(); }
    this.curId = this.nextId = null;
    body.classList.remove("playing");
  },

  play() {
    const p = this.cur.play();
    if (p && p.catch) p.catch(() => body.classList.remove("playing"));
  },

  toggle() {
    if (!this.curId) return;
    if (this.cur.paused) { this.userPaused = false; EndWatch.fired = false; this.play(); }
    else { this.userPaused = true; this.cur.pause(); }
  },

  get duration() {
    const d = this.cur.duration;
    if (isFinite(d) && d > 0) return d;
    const m = S.meta.get(this.curId);
    return m ? m.duration : 0;
  },

  seek(sec, { setActive = false } = {}) {
    if (!this.curId) return;
    const d = this.duration;
    const t = Math.max(0, d ? Math.min(sec, d - 0.05) : sec);
    this.cur.currentTime = t;
    if (d && t < d - 1) EndWatch.reset(this.curId); // seeking back re-arms end-of-track handling
    if (setActive) this.activeStart = t;
    if (this.cur.paused && !this.userPaused) this.play();
    Wave.markersDirty();
  },

  jump(sec) {
    const d = this.duration;
    if (d && sec >= d) { Wave.flash(sec); return; }
    this.seek(sec, { setActive: true });
  },

  outro() {
    const d = this.duration;
    if (!d) return;
    this.seek(Math.max(0, d - OUTRO_SEC), { setActive: true });
  },

  nudge(delta) { this.seek(this.cur.currentTime + delta); },
  replay() { this.seek(this.activeStart); },
};

function onPlaying() {
  body.classList.add("playing");
  if (S.pendingDecisionAt) {
    const ms = performance.now() - S.pendingDecisionAt;
    S.latencies.push(Math.round(ms));
    if (S.latencies.length > 50) S.latencies.shift();
    S.pendingDecisionAt = 0;
  }
  Wave.loadPeaks(Player.curId);
}

/** WebKit sometimes stops at the very end of an MP3 without firing "ended" (seen with real files).
 * Treat "playing, at the end, and not moving for a moment" as ended. Fires at most once per track. */
const EndWatch = {
  STALL_MS: 800,
  id: null,
  fired: false,
  since: 0,
  lastT: -1,

  reset(id) { this.id = id; this.fired = false; this.since = 0; this.lastT = -1; },

  tick() {
    const a = Player.cur, d = Player.duration;
    if (this.fired || this.id !== Player.curId || a.paused || !d) return;
    const t = a.currentTime;
    if (t >= d - 0.3 && Math.abs(t - this.lastT) < 0.01) {
      if (!this.since) this.since = performance.now();
      else if (performance.now() - this.since > this.STALL_MS) onTrackEnded();
    } else {
      this.since = 0;
    }
    this.lastT = t;
  },
};

function onTrackEnded() {
  if (EndWatch.fired && EndWatch.id === Player.curId) return;
  EndWatch.fired = true;
  if (Listen.active) { Listen.onEnded(); return; }
  body.classList.remove("playing");
  if (!S.settings || S.settings.end_of_track === "stop") { Player.cur.pause(); return; }
  decide({ kind: "skip", auto: "ended" });
}

function onPlaybackError() {
  if (Listen.active) { Listen.onError(); return; }
  const cur = S.session && S.session.current;
  if (!cur || cur.id !== Player.curId) return;
  decide({ kind: "skip", auto: "error" });
}

// ------------------------------------------------------------------ waveform

const Wave = {
  canvas: $("wave-canvas"),
  peaks: null, // Uint8Array
  peaksFor: null,
  base: null, // offscreen canvases (unplayed / played)
  played: null,
  loadingFor: null,
  dirtyMarkers: true,
  flashTimer: 0,

  reset(id) {
    this.peaks = null;
    this.peaksFor = id;
    this.render();
    this.markersDirty();
  },

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.render();
    this.markersDirty();
  },

  /** Pre-render bars in two colors; each frame only composites them. */
  render() {
    const w = this.canvas.width, h = this.canvas.height;
    const mk = (color) => {
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const g = c.getContext("2d");
      g.fillStyle = color;
      if (this.peaks) {
        const n = this.peaks.length;
        const dpr = window.devicePixelRatio || 1;
        const barW = Math.max(1, Math.floor(2 * dpr));
        const gap = Math.max(1, Math.floor(dpr));
        const step = barW + gap;
        const bars = Math.floor(w / step);
        for (let i = 0; i < bars; i++) {
          const a = Math.floor((i / bars) * n), b = Math.max(a + 1, Math.floor(((i + 1) / bars) * n));
          let v = 0;
          for (let j = a; j < b; j++) v = Math.max(v, this.peaks[j]);
          const bh = Math.max(2 * dpr, (v / 255) * (h - 8 * dpr));
          g.fillRect(i * step, (h - bh) / 2, barW, bh);
        }
      } else {
        const lh = Math.max(2, Math.round(2 * (window.devicePixelRatio || 1)));
        g.fillRect(0, (h - lh) / 2, w, lh);
      }
      return c;
    };
    this.base = mk(token("--line-strong"));
    this.played = mk(token("--accent"));
  },

  draw() {
    const g = this.canvas.getContext("2d");
    const w = this.canvas.width, h = this.canvas.height;
    g.clearRect(0, 0, w, h);
    if (!this.base) return;
    g.drawImage(this.base, 0, 0);
    const d = Player.duration;
    if (d > 0 && Player.curId) {
      const x = Math.round((Player.cur.currentTime / d) * w);
      if (x > 0) g.drawImage(this.played, 0, 0, x, h, 0, 0, x, h);
      // active start marker
      const sx = Math.round((Player.activeStart / d) * w);
      g.globalAlpha = 0.5;
      g.fillStyle = token("--accent");
      g.fillRect(sx, 0, Math.max(1, window.devicePixelRatio || 1), h);
      g.globalAlpha = 1;
      g.fillStyle = token("--ink");
      g.fillRect(x, 0, Math.max(1, window.devicePixelRatio || 1), h);
    }
  },

  markersDirty() { this.dirtyMarkers = true; },

  drawMarkers() {
    if (!this.dirtyMarkers) return;
    this.dirtyMarkers = false;
    const box = $("markers");
    box.textContent = "";
    if (!["swipe", "grid", "listen"].includes(S.mode)) return;
    const d = Player.duration;
    // Only points inside the track are shown; pressing a key beyond the end flashes the waveform.
    const points = JUMPS.map((sec, i) => ({ sec, key: String(i + 1), label: fmtTime(sec) })).filter((p) => !d || p.sec < d);
    if (d > OUTRO_SEC) points.push({ sec: d - OUTRO_SEC, key: "9", label: "outro", outro: true });
    points.sort((a, b) => a.sec - b.sec);
    const showKeys = S.mode !== "grid";
    for (const p of points) {
      const b = el("button", "marker");
      b.type = "button";
      if (showKeys) b.appendChild(el("kbd", null, p.key));
      b.appendChild(el("span", "t", p.label));
      const pos = d ? p.sec / d : JUMPS.indexOf(p.sec) / (JUMPS.length - 1);
      b.style.left = `${(pos * 100).toFixed(3)}%`;
      if (Math.abs(p.sec - Player.activeStart) < 0.5) b.classList.add("active");
      b.dataset.sec = String(p.sec);
      b.dataset.pos = String(pos);
      if (p.outro) b.dataset.outro = "1";
      b.addEventListener("click", () => (p.outro ? Player.outro() : Player.jump(p.sec)));
      box.appendChild(b);
    }
    // keep the edge labels inside the box
    const first = box.firstElementChild;
    if (first) first.style.transform = "translateX(0)";
    const last = box.lastElementChild;
    if (last && last !== first && Number(last.dataset.pos) > 0.92) last.style.transform = "translateX(-100%)";
    // A marker that would overlap the previous one is hidden (its key still works). The outro marker wins.
    const GAP = 8;
    let prev = null;
    for (const m of box.children) {
      if (prev && m.getBoundingClientRect().left < prev.getBoundingClientRect().right + GAP) {
        if (m.dataset.outro && !prev.dataset.outro) { prev.hidden = true; prev = m; } else m.hidden = true;
        continue;
      }
      prev = m;
    }
  },

  flash(sec) {
    const wave = $("wave");
    wave.classList.add("flash");
    const m = [...document.querySelectorAll(".marker")].find((b) => Number(b.dataset.sec) === sec);
    if (m) m.classList.add("flash");
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => {
      wave.classList.remove("flash");
      if (m) m.classList.remove("flash");
    }, 350);
  },

  /** Peaks: from the backend (disk cache; WAV/AIFF computed there), else decode compressed files here. */
  async loadPeaks(id) {
    if (!id || this.peaksFor === id && this.peaks) return;
    if (this.loadingFor === id) return;
    this.loadingFor = id;
    try {
      const cached = await api("peaks_get", { track_id: id });
      if (Player.curId !== id) return;
      if (cached) return this.setPeaks(id, Uint8Array.from(atob(cached), (c) => c.charCodeAt(0)));
      const meta = await getMeta(id).catch(() => null);
      // WAV/AIFF: the backend already tried; decoding them here would cost WebKit hundreds of MB.
      if (!meta || meta.pcm || meta.size > WAVE_MAX_BYTES || (meta.duration && meta.duration > WAVE_MAX_SEC)) return;
      await new Promise((r) => setTimeout(r, 300)); // let playback settle first
      if (Player.curId !== id) return;
      const buf = await (await fetch(audioUrl(id))).arrayBuffer();
      if (Player.curId !== id) return;
      // Decoding through a low-rate offline context resamples on the fly and keeps memory small.
      const audio = await new OfflineAudioContext(1, 1, WAVE_DECODE_RATE).decodeAudioData(buf);
      if (Player.curId !== id) return;
      const peaks = computePeaks(audio, WAVE_PEAKS);
      this.setPeaks(id, peaks);
      let bin = "";
      for (let i = 0; i < peaks.length; i++) bin += String.fromCharCode(peaks[i]);
      api("peaks_put", { track_id: id, peaks: btoa(bin) }).catch(() => {});
    } catch (_) {
      /* no waveform: the flat progress line stays */
    } finally {
      if (this.loadingFor === id) this.loadingFor = null;
    }
  },

  setPeaks(id, peaks) {
    if (Player.curId !== id) return;
    this.peaks = peaks;
    this.peaksFor = id;
    this.render();
  },
};

function computePeaks(audio, n) {
  const chans = [];
  for (let c = 0; c < Math.min(2, audio.numberOfChannels); c++) chans.push(audio.getChannelData(c));
  const len = chans[0].length;
  const per = Math.max(1, Math.floor(len / n));
  const stride = Math.max(1, Math.floor(per / 256)); // sample sparsely; peaks don't need every sample
  const out = new Float32Array(n);
  let max = 0;
  for (let i = 0; i < n; i++) {
    const a = i * per, b = Math.min(len, a + per);
    let v = 0;
    for (const ch of chans) for (let j = a; j < b; j += stride) { const s = Math.abs(ch[j]); if (s > v) v = s; }
    out[i] = v;
    if (v > max) max = v;
  }
  const res = new Uint8Array(n);
  for (let i = 0; i < n; i++) res[i] = max ? Math.round((out[i] / max) * 255) : 0;
  return res;
}

function frame() {
  Pad.tick(); // game controllers: a no-op without one
  if (body.classList.contains("is-player")) {
    Wave.draw();
    Wave.drawMarkers();
    EndWatch.tick();
    Listen.tick();
    const t = Player.cur.currentTime || 0;
    const txt = `${fmtTime(t)} / ${fmtTime(Player.duration)}`;
    const timeEl = $("time");
    if (timeEl.textContent !== txt) timeEl.textContent = txt;
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ rendering

function setScreen(name) {
  for (const s of ["is-empty", "is-scanning", "is-player", "is-done"]) body.classList.toggle(s, s === `is-${name}`);
}

function renderAll() {
  renderTopbar();
  renderTiles();
  renderHints();
  renderFooterStats();
  if (Listen.active) { setScreen("player"); return; }
  const sess = S.session;
  if (S.scan && S.scan.status === "scanning") {
    setScreen("scanning");
    $("scan-text").textContent = S.scan.count ? `Scanning… ${S.scan.count} tracks` : "Scanning…";
    const f = describeFolder(S.scan.root);
    $("scan-root").textContent = S.scan.root ? `${f.name} · ${f.where}` : "";
    $("scan-root").title = S.scan.root || "";
  } else if (!sess) {
    setScreen("empty");
    renderEmpty();
  } else if (!sess.total) {
    setScreen("empty");
    renderEmpty();
    const msg = $("empty-message");
    msg.textContent = `No audio files found in ${sess.root.split("/").filter(Boolean).pop()}. Supported: mp3, wav, aiff, flac, m4a, aac, alac, ogg.`;
    msg.hidden = false;
  } else if (!sess.current) {
    setScreen("done");
    renderDone();
    Player.clear();
  } else {
    setScreen("player");
    renderCoach();
  }
}

const COACH_SORTS = 3;

/** Contextual first-session hint; gone after a few decisions or "Got it". */
function renderCoach() {
  const box = $("coach");
  const show = S.settings && !S.settings.coach_done;
  box.hidden = !show;
  if (!show) return;
  const t = $("coach-text");
  if (S.mode === "grid") {
    t.innerHTML = "Listen, then press <kbd>1</kbd>–<kbd>9</kbd> to file the track under that genre. <kbd>↓</kbd> skips, <kbd>Z</kbd> undoes. No track is ever deleted.";
    return;
  }
  const L = swipeLayout();
  const sw = activePreset("swipe");
  const k = (slot) => `<kbd>${L.find((x) => x.slot === slot).glyph}</kbd> ${escapeHtml(sw.labels[slot])}`;
  const fav = S.settings.swipe_archive_hotkey === "up" ? "" : ` · <kbd>↑</kbd> ${escapeHtml(sw.labels[2])}`;
  t.innerHTML = `Listen, then decide: ${k(0)} · ${k(1)}${fav} · <kbd>↓</kbd> Skip. <kbd>1</kbd>–<kbd>9</kbd> jump through the track, <kbd>Z</kbd> undoes. No track is ever deleted.`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function finishCoach() {
  if (!S.settings || S.settings.coach_done) return;
  S.settings.coach_done = true;
  $("coach").hidden = true;
  api("set_settings", { coach_done: true }).then((x) => (S.settings = x)).catch(() => {});
}

function renderTopbar() {
  const root = (S.session && S.session.root) || (S.scan && S.scan.root) || "";
  const name = root.split("/").filter(Boolean).pop() || "";
  $("folder-name").textContent = name;
  $("folder-chip").title = root ? `${root}\nClick to choose another folder` : "Choose a folder";
  for (const b of document.querySelectorAll(".mode-switch button")) b.classList.toggle("active", b.dataset.mode === S.mode);
  body.classList.toggle("mode-swipe", S.mode === "swipe");
  body.classList.toggle("mode-grid", S.mode === "grid");
  body.classList.toggle("mode-listen", S.mode === "listen");
  body.classList.toggle("plain-tiles", !!S.settings && S.settings.colored_tiles === false);
  const listenBtn = document.querySelector('.mode-switch [data-mode="listen"]');
  if (listenBtn) listenBtn.hidden = !(S.settings && S.settings.discover_in_topbar && S.session);

  const sel = $("preset-select");
  sel.textContent = "";
  if (S.presets) {
    const act = activePreset();
    for (const p of S.presets.presets.filter((q) => q.mode === S.mode)) {
      const o = el("option", null, p.name);
      o.value = p.id;
      if (act && p.id === act.id) o.selected = true;
      sel.appendChild(o);
    }
    const edit = el("option", null, "Edit presets…");
    edit.value = "__edit__";
    sel.appendChild(edit);
  }
  $("op-badge").hidden = currentOperation() !== "copy";
  $("filter-badge").hidden = !(S.session && S.session.filter);
  const sess = S.session;
  if (sess && sess.total) {
    const done = sess.total - sess.remaining;
    $("count").textContent = Listen.active ? "Discover" : `${Math.min(done + 1, sess.total)} / ${sess.total}`;
    $("top-progress-fill").style.width = `${(done / sess.total) * 100}%`;
  } else {
    $("count").textContent = "";
    $("top-progress-fill").style.width = "0";
  }
}

/** Swipe tile layout for the current settings: [{dir, glyph, slot|'skip', keyHint}]. */
function swipeLayout() {
  const keepRight = !S.settings || S.settings.swipe_keep_direction !== "left";
  const upArchive = S.settings && S.settings.swipe_archive_hotkey === "up";
  const keep = { slot: 0, dir: keepRight ? "right" : "left" };
  const archive = { slot: 1, dir: keepRight ? "left" : "right" };
  const glyph = { left: "←", right: "→" };
  const tiles = [
    { slot: archive.slot, glyph: glyph[archive.dir], key: glyph[archive.dir] + (upArchive ? " ↑" : ""), dir: archive.dir },
    { slot: "skip", glyph: "↓", key: "↓", dir: "down" },
    { slot: 2, glyph: "↑", key: upArchive ? "swipe ↑" : "↑", dir: "up" },
    { slot: keep.slot, glyph: glyph[keep.dir], key: glyph[keep.dir], dir: keep.dir },
  ];
  // Left-most tile is always the "←" action, right-most the "→" action.
  return tiles.sort((a, b) => ["left", "down", "up", "right"].indexOf(a.dir) - ["left", "down", "up", "right"].indexOf(b.dir));
}

function slotForDirection(dir) {
  const t = swipeLayout().find((x) => x.dir === dir);
  return t ? t.slot : null;
}

function renderTiles() {
  const sw = activePreset("swipe");
  const swBox = $("tiles-swipe");
  swBox.textContent = "";
  if (sw) {
    for (const t of swipeLayout()) {
      const skip = t.slot === "skip";
      const color = skip ? "#8A8F98" : sw.colors[t.slot];
      const tile = el("button", "tile");
      tile.type = "button";
      tile.style.setProperty("--tile-color", color);
      tile.dataset.slot = String(t.slot);
      tile.appendChild(el("div", "glyph", t.glyph));
      tile.appendChild(el("div", "label", skip ? "Skip" : sw.labels[t.slot]));
      tile.appendChild(el("div", "key", t.key));
      const n = S.mode !== "swipe" ? null : skip ? (S.session && S.session.counts.skip) || 0 : S.counts[t.slot];
      if (n != null) tile.appendChild(el("div", "badge", String(n)));
      tile.addEventListener("click", () => decide(skip ? { kind: "skip" } : { kind: "sort", slot: t.slot }));
      swBox.appendChild(tile);
    }
  }
  const gr = activePreset("grid");
  const grBox = $("tiles-grid");
  grBox.textContent = "";
  if (gr) {
    gr.labels.forEach((label, i) => {
      const color = gr.colors[i];
      const tile = el("button", "tile");
      tile.type = "button";
      tile.style.setProperty("--tile-color", color);
      tile.dataset.slot = String(i);
      tile.appendChild(el("div", "label", label));
      tile.appendChild(el("div", "key", String(i + 1)));
      const n = S.mode === "grid" ? S.counts[i] : null;
      if (n != null) tile.appendChild(el("div", "badge", String(n)));
      tile.addEventListener("click", () => decide({ kind: "sort", slot: i }));
      grBox.appendChild(tile);
    });
  }
}

function pressTile(slot) {
  const box = S.mode === "grid" ? $("tiles-grid") : $("tiles-swipe");
  const t = box.querySelector(`.tile[data-slot="${slot}"]`);
  if (!t) return;
  t.classList.add("pressed");
  setTimeout(() => t.classList.remove("pressed"), 120);
}

/** Live session stats in the footer, like v1: "12 sorted · 5 Keep · 4 Archive · 2 skipped · 42% kept · 8m". */
function renderFooterStats() {
  const box = $("footer-stats");
  const sess = S.session;
  const show = !!(sess && sess.sorted && !Listen.active);
  box.hidden = !show;
  body.classList.toggle("has-stats", show);
  if (!show) return;
  const info = sess.count_info || {};
  const groups = Object.entries(sess.counts)
    .filter(([k, n]) => n && k !== "skip")
    .map(([k, n]) => ({ n, label: (info[k] && info[k].label) || k, slot: info[k] ? info[k].slot : 99 }))
    .sort((a, b) => b.n - a.n);
  const skipped = sess.counts.skip || 0;
  const parts = [`${sess.sorted} sorted`, ...groups.slice(0, 4).map((g) => `${g.n} ${g.label}`)];
  if (skipped) parts.push(`${skipped} skipped`);
  // "Kept" = everything that went to the Keep/Favorite slots (swipe) or any tile (grid), out of all decisions.
  const kept = Object.entries(sess.counts)
    .filter(([k]) => k !== "skip" && info[k] && (info[k].mode === "grid" || info[k].slot !== 1))
    .reduce((sum, [, n]) => sum + n, 0);
  if (sess.sorted) parts.push(`${Math.round((100 * kept) / sess.sorted)}% kept`);
  const mins = Math.floor(sess.elapsed / 60);
  parts.push(mins >= 1 ? `${mins}m` : `${Math.floor(sess.elapsed)}s`);
  box.textContent = parts.join(" · ");
}

function renderHints() {
  const h = $("hints");
  h.textContent = "";
  const add = (k, label) => { const s = el("span"); s.appendChild(el("kbd", null, k)); s.appendChild(document.createTextNode(label)); h.appendChild(s); };
  if (!S.session) { add("H", "shortcuts"); return; }
  if (Listen.active) { add("← →", "previous / next"); add("↑ or F", "heart"); add("Space", "play"); add("1–9", "jump"); add("L", "back to sorting"); return; }
  if (S.mode === "swipe") { add("← → ↑", "sort"); add("↓", "skip"); add("1–9", "jump"); }
  else { add("1–9", "sort"); add("↓", "skip"); }
  add("Space", "play"); add("A D", "±30s"); add("Z", "undo"); add("H", "all shortcuts");
}

async function renderCard() {
  const cur = S.session && S.session.current;
  if (!cur) return;
  const id = cur.id;
  const cached = S.meta.get(id);
  fillCard(cur, cached);
  if (!cached) {
    try { const m = await getMeta(id); if (S.session && S.session.current && S.session.current.id === id) fillCard(cur, m); }
    catch (_) { /* keep the file-name view */ }
  }
}

function fillCard(track, meta) {
  const { title, artist } = trackDisplay(track, meta);
  const t = $("title");
  t.textContent = title;
  t.dataset.text = title;
  t.classList.remove("marquee");
  $("artist").textContent = artist;
  const cover = $("cover");
  const letter = ((artist || title || "").match(/\p{L}/u) || ["♪"])[0];
  $("cover-fallback").textContent = letter.toUpperCase();
  const img = $("cover-img");
  if (meta && meta.has_cover) {
    if (img.dataset.id !== track.id) {
      cover.classList.remove("has-img");
      img.onload = () => img.dataset.id === track.id && cover.classList.add("has-img");
      img.dataset.id = track.id;
      img.src = coverUrl(track.id);
    }
  } else {
    img.dataset.id = "";
    img.removeAttribute("src");
    cover.classList.remove("has-img");
  }
  const chips = $("chips");
  chips.textContent = "";
  const chip = (label, value) => {
    const c = el("span", "chip");
    if (label) c.appendChild(document.createTextNode(label + " "));
    c.appendChild(el("b", null, value));
    chips.appendChild(c);
  };
  if (meta && meta.duplicate) {
    const c = el("span", "chip chip-warn", `Already in ${meta.duplicate.folder}`);
    c.title = meta.duplicate.path;
    chips.appendChild(c);
  }
  if (meta && meta.bpm) chip("BPM", meta.bpm);
  if (meta && meta.key) chip("Key", meta.key);
  if (meta && meta.duration) chip("", fmtTime(meta.duration));
  chip("", track.name.split(".").pop().toUpperCase());
  renderCardPath(track);
  requestAnimationFrame(() => {
    const wrap = t.parentElement;
    if (t.scrollWidth > wrap.clientWidth + 2) {
      const shift = t.scrollWidth + 96; // text + padding-right + ::after padding-left
      t.classList.add("marquee");
      t.style.setProperty("--marquee-shift", `-${shift}px`);
      t.style.setProperty("--marquee-dur", `${Math.max(8, shift / 60)}s`);
    }
  });
}

/** "/Users/me/Music/Promos" -> {name: "Promos", where: "~/Music"}; "/Volumes/USB/Promos" -> where "on USB". */
function describeFolder(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  const name = parts.pop() || path || "";
  let where = "/" + parts.join("/");
  let disk = null;
  if (parts[0] === "Volumes" && parts[1]) {
    disk = parts[1];
    where = parts.length > 2 ? `on ${disk} · ${parts.slice(2).join("/")}` : `on ${disk}`;
  } else if (S.home && where === S.home) {
    where = "~";
  } else if (S.home && where.startsWith(S.home + "/")) {
    where = "~" + where.slice(S.home.length);
  }
  return { name, where, disk };
}

function missingFolderMessage(path) {
  const f = describeFolder(path);
  if (f.disk) return `Your default folder “${f.name}” is on the disk “${f.disk}”. Is it connected? Or choose another folder.`;
  return `Your default folder “${f.name}” (${f.where}) was not found. Choose a folder, or change it in Settings.`;
}

function renderEmpty() {
  const path = S.settings && S.settings.default_folder_path;
  const btn = $("btn-load-default");
  btn.hidden = !(path && S.defaultFolderExists);
  if (path) btn.textContent = `Load ${path.split("/").filter(Boolean).pop()}`;
  const msg = $("empty-message");
  if (path && !S.defaultFolderExists) {
    msg.textContent = missingFolderMessage(path);
    msg.title = path;
    msg.hidden = false;
  }
}

/** Session summary: one row per destination folder, plus skipped and time spent. */
/** Where the file is: "<loaded folder> › sub › file.mp3"; click shows it in Finder, hover shows the full path. */
function renderCardPath(track) {
  const box = $("card-path");
  const root = (S.session && S.session.root) || "";
  const rel = track.rel || track.name;
  const rootName = root.split("/").filter(Boolean).pop() || "";
  box.textContent = "";
  box.appendChild(el("span", "card-path-icon", "📁"));
  [rootName, ...rel.split("/")].filter(Boolean).forEach((part, i, all) => {
    if (i) box.appendChild(el("span", "card-path-sep", "›"));
    box.appendChild(el("span", i === all.length - 1 ? "card-path-file" : null, part));
  });
  box.title = `${root}/${rel}\nClick to show in Finder`;
  box.onclick = (e) => { e.stopPropagation(); api("reveal_track", { track_id: track.id }).catch((err) => toast(err.message, { level: "error" })); };
  box.onpointerdown = (e) => e.stopPropagation(); // clicking the path must not start a card drag
}

function renderDone() {
  const box = $("done-stats");
  box.textContent = "";
  const sess = S.session;
  if (!sess) return;
  const rows = [];
  for (const [k, v] of Object.entries(sess.counts)) {
    if (!v) continue;
    if (k === "skip") { rows.push({ label: "Skipped", sub: "", n: v, color: "#8A8F98", order: 99 }); continue; }
    const [pid, slot] = k.split(":");
    // What the track was sorted as (kept even if the preset was edited or deleted since).
    const info = (sess.count_info || {})[k] || {};
    const p = S.presets.presets.find((q) => q.id === pid);
    rows.push({
      label: info.label || (p ? p.labels[Number(slot)] : k),
      sub: info.preset || (p ? p.name : ""),
      n: v,
      color: info.color || (p ? p.colors[Number(slot)] : "#8A8F98"),
      order: Number(slot),
    });
  }
  rows.sort((a, b) => (a.order === 99) - (b.order === 99) || a.sub.localeCompare(b.sub) || a.order - b.order);
  for (const r of rows) {
    const name = el("div", "done-label");
    const dot = el("i", "dot");
    dot.style.background = r.color;
    name.appendChild(dot);
    name.appendChild(document.createTextNode(r.label));
    if (r.sub) name.appendChild(el("span", "muted small", ` · ${r.sub}`));
    box.appendChild(name);
    box.appendChild(el("div", "n", String(r.n)));
  }
  const total = rows.reduce((a, r) => a + r.n, 0);
  box.appendChild(el("div", "done-sep"));
  box.appendChild(el("div", "done-sep"));
  box.appendChild(el("div", null, "Total"));
  box.appendChild(el("div", "n", String(total)));
  box.appendChild(el("div", "muted", "Time"));
  box.appendChild(el("div", "n muted", fmtTime(sess.elapsed)));
  const verb = currentOperation() === "copy" ? "copied" : "moved";
  renderDoneFollow();
  const held = sess.held || 0;
  $("btn-done-clear-filter").hidden = !held;
  document.querySelector("#screen-done h1").textContent = held ? "No more tracks match the filter" : "All done";
  $("btn-done-export").hidden = !total;
  const folder = sess.root.split("/").filter(Boolean).pop();
  const tree = sess.in_place ? folder : verb === "copied" ? "BAKKIS_SORTED_COPIES" : "BAKKIS_SORTED";
  let note = total ? `Files were ${verb} into ${sess.in_place ? "preset folders inside " + folder : tree + " inside " + folder}.` : "";
  // Sorting in steps: after a grid sort, the genre folders are ready for a Quick Sort of their own.
  const grid = rows.find((r) => r.order !== 99 && S.presets.presets.some((p) => p.mode === "grid" && p.name === r.sub));
  if (grid && !sess.in_place) note += ` Next step: load ${tree}/${grid.sub}/${grid.label} and Quick Sort it.`;
  $("done-note").textContent = note;
}

/** "move" or "copy" for the loaded folder: inside a sorted tree tracks are always moved. */
function currentOperation() {
  if (S.session && S.session.operation) return S.session.operation;
  return S.settings && S.settings.file_operation === "copy" ? "copy" : "move";
}

/** ".../music/BAKKIS_SORTED_COPIES/Discover/x" -> "BAKKIS_SORTED_COPIES/Discover/x" */
function treePath(path) {
  const m = /\/(BAKKIS_SORTED(?:_COPIES)?\/.*)$/.exec(path || "");
  return m ? m[1] : path;
}

/** Copy a plain-text summary of a session's decisions (promo reports, demo replies). */
async function copyFeedback(sessionId) {
  try {
    const r = await api("feedback", sessionId ? { session_id: sessionId } : {});
    if (!r.count) { toast("Nothing to report yet: sort a few tracks first (skips are not included)"); return; }
    toast(`Feedback for ${r.count} track${r.count === 1 ? "" : "s"} copied. Paste it anywhere.`, { ms: 5000 });
  } catch (e) { toast(e.message, { level: "error" }); }
}

/** Advanced: M3U8 per destination folder, then offer to show them in Finder. */
async function exportPlaylists() {
  try {
    const r = await api("export_playlists");
    const n = r.playlists.length;
    if (!n) { toast("Nothing sorted yet to export"); return; }
    toast(`Exported ${n} playlist${n === 1 ? "" : "s"} to ${treePath(r.folder)}`, {
      ms: 8000,
      action: { label: "Show", fn: () => api("reveal", { target: "playlists" }).catch(() => {}) },
    });
  } catch (e) { toast(e.message, { level: "error" }); }
}

/** One request to GitHub Releases per launch; silent unless a newer version exists. */
async function checkForUpdate() {
  try {
    const r = await api("check_update");
    S.update = r;
    if (r.newer && r.url) toast(`BAKKIS MUSIC SORTER ${r.latest} is available`, { ms: 15000, action: { label: "Download", fn: () => openExternal(r.url) } });
  } catch (_) { /* never bother the user about this */ }
}

/** One quiet promo line on the end screen. */
function renderDoneFollow() {
  const p = $("done-follow");
  p.textContent = "";
  p.appendChild(el("span", "muted", "Sorted with BAKKIS MUSIC SORTER · Follow BAKKIS on "));
  FOLLOW_LINKS.slice(0, 3).forEach(([label, url], i) => {
    if (i) p.appendChild(el("span", "muted", " · "));
    const b = el("button", "link-btn", label);
    b.type = "button";
    b.addEventListener("click", () => openExternal(url));
    p.appendChild(b);
  });
}

// ------------------------------------------------------------------ session updates

/** Apply a session snapshot; starts the new current track and preloads the next one. */
function applySession(snap) {
  const prevId = S.session && S.session.current && S.session.current.id;
  const prevRoot = S.session && S.session.root;
  S.session = snap;
  if (snap && snap.in_place && snap.root !== prevRoot) {
    const copying = S.settings && S.settings.file_operation === "copy";
    toast(`Sorting inside ${snap.root.split("/").filter(Boolean).pop()}: results go into preset folders right here.` +
      (copying ? " These tracks are already copies, so they are moved, not copied again. Your originals stay untouched." : ""), { ms: 9000 });
  }
  if (snap && snap.last_notice > S.noticeSeq) pollNotices();
  if (snap && snap.current) warmUpAudio();
  renderAll();
  Drawers.onSession();
  if (Listen.active) return;
  const cur = snap && snap.current;
  if (!cur) return;
  if (cur.id !== prevId || cur.id !== Player.curId) {
    Wave.reset(cur.id);
    Player.load(cur.id);
    renderCard();
  }
  const nxt = snap.upcoming && snap.upcoming[0];
  if (nxt) {
    Player.preload(nxt.id);
    getMeta(nxt.id).catch(() => {});
  }
}

async function refreshState() {
  const st = await api("state");
  S.settings = st.settings;
  S.presets = st.presets;
  S.scan = st.scan;
  S.version = st.version;
  S.defaultFolderExists = st.default_folder_exists;
  S.home = st.home;
  applySession(st.session);
  return st;
}

let countsTimer = 0;
function refreshCountsSoon(delay = 500) {
  clearTimeout(countsTimer);
  countsTimer = setTimeout(async () => {
    if (!S.session) return;
    try { S.counts = await api("folder_counts", { mode: S.mode }); renderTiles(); } catch (_) { /* ignore */ }
  }, delay);
}

// ------------------------------------------------------------------ decisions

async function ensureMoveAcknowledged() {
  const s = S.settings;
  if (!s || s.file_operation !== "move" || s.move_warning_acknowledged) return true;
  const choice = await modal({
    title: "Files will be moved",
    body: "Sorting moves files into BAKKIS_SORTED inside this folder. If these tracks are in a Rekordbox or Serato library, their links will break. Copy mode keeps the originals where they are.",
    buttons: [
      { label: "Cancel", value: null },
      { label: "Use copy", value: "copy" },
      { label: "Move files", value: "move", primary: true },
    ],
  });
  if (!choice) return false;
  S.settings = await api("set_settings", { move_warning_acknowledged: true, file_operation: choice });
  return true;
}

/** kind: 'sort' | 'skip'. `auto` marks automatic skips (track ended / playback error). */
async function decide({ kind, slot, auto }) {
  if (Listen.active || modalOpen()) return; // listening never moves files; nothing sorts behind a dialog
  const sess = S.session;
  if (S.busy || !sess || !sess.current) return;
  const cur = sess.current;
  const s = S.settings;
  if (auto && s && s.file_operation === "move" && !s.move_warning_acknowledged) {
    // Never move files automatically before the user has accepted moving.
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    toast(auto === "error" ? `Can't play “${name}”. Press ↓ to skip it.` : "Track ended. Press ↓ to skip it.", { level: "warning", ms: 6000 });
    return;
  }
  if (auto && S.restoredIds.has(cur.id)) {
    // The user undid this one on purpose; don't move it away again on its own.
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    toast(auto === "error" ? `Can't play “${name}”. Press ↓ to skip it.` : "Track ended.", { level: "warning", ms: 6000 });
    return;
  }
  if (auto === "ended" && S.autoSkips >= MAX_AUTO_SKIPS) {
    Player.userPaused = true;
    toast(`Paused: ${MAX_AUTO_SKIPS} tracks ended without input. Press ↓ to skip this one.`, { level: "warning", ms: 10000 });
    return;
  }
  // Busy from here on, before the first await, so a quick double press can never sort twice.
  S.busy = true;
  try {
    if (!auto && !(await ensureMoveAcknowledged())) return;
  } catch (e) {
    S.busy = false;
    throw e;
  }
  if (!S.session || !S.session.current || S.session.current.id !== cur.id) { S.busy = false; return; }
  S.autoSkips = auto === "ended" ? S.autoSkips + 1 : 0;
  const preset = activePreset();
  const label = kind === "skip" ? "Skipped" : preset.labels[slot];
  S.pendingDecisionAt = performance.now();
  try {
    const snap = kind === "skip"
      ? await api("skip", { track_id: cur.id, mode: S.mode })
      : await api("sort", { track_id: cur.id, mode: S.mode, slot });
    if (!auto && S.settings && !S.settings.coach_done && ++S.coachSorts >= COACH_SORTS) finishCoach();
    if (kind === "sort") { S.counts[slot] = (S.counts[slot] || 0) + 1; pressTile(slot); }
    else pressTile("skip");
    if (!auto) Pad.feedback(kind === "skip" ? "skip" : preset.mode === "swipe" ? ["keep", "archive", "favorite"][slot] : "keep");
    flyOutCard(flyDirection(kind, slot));
    applySession(snap);
    cardEnter();
    if (!auto) clickSound(); // after the next track has started, so sound setup never delays it
    const verb = snap.operation === "copy" ? "Copied to" : "Moved to";
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    if (auto === "error") toast(`Can't play “${name}” — skipped`, { level: "warning", undo: true });
    else if (auto === "ended") toast(`Track ended — skipped “${name}”`, { undo: true });
    else toast(kind === "skip" ? `Skipped “${name}”` : `${verb} ${label}`, { undo: true });
    if (snap.support_nudge && SUPPORT_URL) {
      toast(`${snap.support_nudge} tracks sorted! If it saves you time, buy me a coffee ☕`, {
        ms: 12000, action: { label: "Support", fn: () => openExternal(SUPPORT_URL) },
      });
    }
    refreshCountsSoon();
  } catch (e) {
    S.pendingDecisionAt = 0;
    Pad.feedback("error");
    toast(e.message, { level: "error" });
    await refreshState().catch(() => {});
  } finally {
    S.busy = false;
  }
}

async function undo() {
  if (S.busy || !S.session || modalOpen()) return;
  if (!S.session.history || !S.session.history.length) { toast("Nothing to undo"); return; }
  S.busy = true;
  try {
    const snap = await api("undo");
    if (snap.current) S.restoredIds.add(snap.current.id);
    applySession(snap);
    Pad.feedback("undo");
    toast("Undone");
    refreshCountsSoon(400);
  } catch (e) {
    toast(e.message, { level: "error" });
  } finally {
    S.busy = false;
  }
}

async function undoAll() {
  if (S.busy || !S.session) return;
  const n = S.session.sorted; // every action still undoable this session (sorts and skips)
  if (!n) { toast("Nothing to undo"); return; }
  const ok = await modal({
    title: "Undo the whole session?",
    body: `All ${n} sorted tracks go back to where they were.`,
    buttons: [{ label: "Cancel", value: false }, { label: "Undo all", value: true, danger: true }],
  });
  if (!ok) return;
  S.busy = true;
  try {
    const snap = await api("undo_all");
    for (const t of [snap.current, ...(snap.upcoming || [])].filter(Boolean)) S.restoredIds.add(t.id);
    applySession(snap);
    toast(`Undid ${n} actions`);
    refreshCountsSoon(600);
  } catch (e) {
    toast(e.message, { level: "error" });
  } finally {
    S.busy = false;
  }
}

async function setMode(mode) {
  if (mode === "listen") { Listen.openPicker(); return; } // the mode changes once listening starts
  if (Listen.active) { await Listen.exit(); if (mode === S.mode) return; }
  if (mode === S.mode) return;
  S.mode = mode;
  S.counts = [];
  Wave.markersDirty();
  renderAll();
  refreshCountsSoon(0);
}

/** Run a queue change after any sort in flight, so their snapshots can't arrive out of order. */
async function queueChange(method, params = {}) {
  for (let i = 0; S.busy && i < 150; i++) await new Promise((r) => setTimeout(r, 20));
  S.busy = true;
  try { return await api(method, params); } finally { S.busy = false; }
}

async function setOrder(order) {
  if (!S.session) return;
  try {
    const snap = await queueChange("set_order", { order });
    S.settings.track_order = order;
    applySession(snap);
    toast(order === "random" ? "Random order" : "In order");
  } catch (e) { toast(e.message, { level: "error" }); }
}

// ------------------------------------------------------------------ folder loading

/** Native folder dialog in the app; a path prompt in browser dev mode. Returns a path or null. */
async function pickFolderPath() {
  try {
    return await api("choose_folder");
  } catch (e) {
    if (e.status !== 404) { toast(e.message, { level: "error" }); return null; }
    return window.prompt("Music folder path (use a TEST folder):", (S.settings && S.settings.default_folder_path) || "") || null;
  }
}

async function chooseFolder() {
  const path = await pickFolderPath();
  if (path) loadFolder(path);
}

async function loadFolder(path) {
  if (Listen.active) await Listen.exit();
  $("empty-message").hidden = true;
  try {
    Player.clear();
    S.meta.clear();
    S.counts = [];
    S.scan = await api("load_folder", { path });
    S.session = null;
    renderAll();
  } catch (e) {
    const msg = $("empty-message");
    msg.textContent = e.message;
    msg.hidden = false;
    setScreen("empty");
  }
}

// ------------------------------------------------------------------ toasts & modal

function toast(message, { level = "info", undo: withUndo = false, ms, action } = {}) {
  const box = $("toasts");
  const t = el("div", `toast ${level}`);
  t.appendChild(el("span", "msg", message));
  if (withUndo) {
    action = { label: "Undo", fn: undo };
    // Undo always undoes the most recent action, so only the newest toast may offer it.
    for (const old of box.querySelectorAll(".toast .undo-btn")) old.remove();
  }
  if (action) {
    const b = el("button", withUndo ? "undo-btn" : null, action.label);
    b.type = "button";
    b.addEventListener("click", () => { t.remove(); action.fn(); });
    t.appendChild(b);
  }
  box.appendChild(t);
  while (box.children.length > 3) box.firstElementChild.remove();
  const life = ms || (level === "error" ? 6000 : 3000);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 220); }, life);
}

let modalQueue = Promise.resolve();
/** One dialog at a time: a second one waits until the first is answered. */
function modal(opts) {
  const run = modalQueue.then(() => showModal(opts));
  modalQueue = run.catch(() => {});
  return run;
}
function showModal({ title, body: text, buttons }) {
  $("modal-title").textContent = title;
  $("modal-body").textContent = text;
  const row = $("modal-buttons");
  row.textContent = "";
  return new Promise((resolve) => {
    const close = (v) => { $("modal").hidden = true; resolve(v); };
    buttons.forEach((b) => {
      const btn = el("button", b.danger ? "danger-btn" : b.primary ? "primary-btn" : "ghost-btn", b.label);
      btn.type = "button";
      btn.addEventListener("click", () => close(b.value));
      row.appendChild(btn);
    });
    $("modal").hidden = false;
    const def = row.querySelector(".primary-btn, .danger-btn");
    if (def) def.focus();
    modal.cancel = () => close(buttons[0].value);
    modal.confirm = () => {
      // Enter presses the focused button (Tab can move to Cancel); otherwise the default one.
      const focused = row.contains(document.activeElement) ? [...row.children].indexOf(document.activeElement) : -1;
      const d = focused >= 0 ? buttons[focused] : buttons.find((b) => b.primary || b.danger) || buttons[buttons.length - 1];
      close(d.value);
    };
  });
}
const modalOpen = () => !$("modal").hidden;

// ------------------------------------------------------------------ keyboard

function toggleFullscreen() {
  api("toggle_fullscreen").catch(() => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen && document.documentElement.requestFullscreen();
  });
}

function onKey(e) {
  S.autoSkips = 0;
  if (modalOpen()) {
    if (e.key === "Escape") { e.preventDefault(); modal.cancel(); }
    else if (e.key === "Enter") { e.preventDefault(); modal.confirm(); }
    return;
  }
  if (Welcome.isOpen) {
    if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); Welcome.close(); }
    return;
  }
  if (Guide.isOpen) {
    if (["Escape", "h", "H", "?"].includes(e.key)) { e.preventDefault(); Guide.close(); }
    return;
  }
  if (Drawers.open === "settings" || Drawers.open === "history" || Drawers.open === "hearts") {
    if (e.key === "Escape") { e.preventDefault(); Drawers.close(); }
    return; // the settings form owns the keyboard
  }
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" && e.target.type !== "range" || tag === "TEXTAREA") return;
  if (tag === "SELECT") return;

  const meta = e.metaKey, ctrl = e.ctrlKey, shift = e.shiftKey;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  const digit = /^(Digit|Numpad)([1-9])$/.exec(e.code);

  if (Listen.pickerOpen) {
    if (key === "Escape") { e.preventDefault(); Listen.closePicker(); }
    else if (key === "Enter") { e.preventDefault(); Listen.startFromPicker(); }
    return;
  }
  if (Listen.active && !meta && !ctrl && Listen.onKey(e, key)) return;
  if (key === "l" && !meta && !ctrl && S.session && S.settings.discover_in_topbar) { e.preventDefault(); Listen.openPicker(); return; }

  // Undo
  if (key === "z" && (meta && shift || ctrl && !meta)) { e.preventDefault(); if (!e.repeat) undoAll(); return; }
  if (key === "z" && !ctrl) { e.preventDefault(); if (!e.repeat) undo(); return; }
  if (meta && key === "f") { e.preventDefault(); toggleFullscreen(); return; }
  if (meta || ctrl || e.altKey) return; // leave other system shortcuts alone

  if (key === "Escape") {
    // Mark Esc as handled, or WebKit passes it on to NSWindow, which leaves fullscreen a second time.
    e.preventDefault();
    if (Drawers.open) Drawers.close();
    else if (document.fullscreenElement) document.exitFullscreen();
    else api("exit_fullscreen").catch(() => {});
    return;
  }
  if (key === "Tab") { e.preventDefault(); body.classList.toggle("focus"); return; }
  if (key === "h" || key === "?") { e.preventDefault(); Guide.toggle(); return; }
  if (key === "q") { e.preventDefault(); Drawers.toggle("queue"); return; }

  const hasTrack = !!(S.session && S.session.current) || Listen.active;
  if (!hasTrack) return;

  if (key === "s" || key === "t") { setMode("swipe"); return; }
  if (key === "g") { setMode("grid"); return; }
  if (key === "u") { setOrder("random"); return; }
  if (key === "o") { setOrder("in_order"); return; }
  if (key === " ") { e.preventDefault(); if (!e.repeat) Player.toggle(); return; }
  if (key === "a") { Player.nudge(-SEEK_STEP); return; }
  if (key === "d") { Player.nudge(SEEK_STEP); return; }
  if (key === "r") { if (!e.repeat) Player.replay(); return; }

  if (digit) {
    e.preventDefault();
    const n = Number(digit[2]);
    if (S.mode === "grid") { if (!e.repeat) decide({ kind: "sort", slot: n - 1 }); }
    else if (n === 9) Player.outro();
    else Player.jump(JUMPS[n - 1]);
    return;
  }

  const arrows = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  if (arrows[key]) {
    e.preventDefault();
    if (e.repeat) return; // never sort on key repeat
    const dir = arrows[key];
    if (dir === "down") { decide({ kind: "skip" }); return; }
    if (S.mode !== "swipe") return;
    if (dir === "up") { decide({ kind: "sort", slot: S.settings.swipe_archive_hotkey === "up" ? 1 : 2 }); return; }
    decide({ kind: "sort", slot: slotForDirection(dir) });
  }
}

// ------------------------------------------------------------------ card fly-out (feedback for every sort method)

const FLY_MS = 180;
const FLY_GRID_MS = 160;
const reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

/** Where the card goes: the tile's direction in swipe mode, a short drop toward the tiles in grid mode. */
function flyDirection(kind, slot) {
  if (S.mode === "grid") return "grid";
  if (kind === "skip") return "down";
  const t = swipeLayout().find((x) => x.slot === slot);
  return t ? t.dir : "right";
}

/** A copy of the card slides out while the real card already shows the next track (no added latency). */
function flyOutCard(dir) {
  const card = $("card");
  const from = card.style.transform || ""; // set when the decision came from a mouse drag
  card.classList.remove("springing");
  card.style.transform = "";
  $("drag-hint").classList.remove("show");
  if (reduceMotion.matches || !body.classList.contains("is-player") || !card.animate) return;
  if (S.ghost) S.ghost.remove(); // fast sorting: never stack animations
  const r = card.getBoundingClientRect();
  const g = card.cloneNode(true);
  g.removeAttribute("id");
  for (const n of g.querySelectorAll("[id]")) n.removeAttribute("id");
  g.classList.add("card-ghost");
  Object.assign(g.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`, transform: from });
  document.body.appendChild(g);
  const D = 150;
  const [dx, dy, rot] = { right: [D, 0, 5], left: [-D, 0, -5], up: [0, -D * 0.7, 0], down: [0, D * 0.6, 0], grid: [0, 48, 0] }[dir];
  const anim = g.animate(
    [
      { transform: `${from} translate(0, 0) rotate(0deg)`, opacity: 0.95 },
      { transform: `${from} translate(${dx}px, ${dy}px) rotate(${rot}deg)`, opacity: 0 },
    ],
    { duration: dir === "grid" ? FLY_GRID_MS : FLY_MS, easing: "cubic-bezier(.25,.7,.35,1)" },
  );
  S.ghost = g;
  S.ghostCount = (S.ghostCount || 0) + 1;
  const done = () => { g.remove(); if (S.ghost === g) S.ghost = null; };
  anim.onfinish = done;
  anim.oncancel = done;
}

/** The next track settles in under the departing copy. */
function cardEnter() {
  const card = $("card");
  if (reduceMotion.matches || !card.animate) return;
  card.animate([{ opacity: 0.35, transform: "scale(0.985)" }, { opacity: 1, transform: "scale(1)" }], { duration: 140, easing: "ease-out" });
}

// ------------------------------------------------------------------ gestures

function gestureTarget(dir) {
  const sw = activePreset("swipe");
  if (dir === "down") return { label: "Skip", color: "#8A8F98", action: { kind: "skip" } };
  const slot = dir === "up" ? 2 : slotForDirection(dir);
  return { label: sw.labels[slot], color: sw.colors[slot], action: { kind: "sort", slot } };
}

function setupDrag() {
  const card = $("card");
  const hint = $("drag-hint");
  let start = null;

  const dirOf = (dx, dy) => {
    if (Math.max(Math.abs(dx), Math.abs(dy)) < DRAG_THRESHOLD) return null;
    return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
  };

  card.addEventListener("pointerdown", (e) => {
    if (S.mode !== "swipe" || e.button !== 0) return;
    start = { x: e.clientX, y: e.clientY };
    card.setPointerCapture(e.pointerId);
    card.classList.add("dragging");
    card.classList.remove("springing");
  });
  card.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 40}deg)`;
    const dir = dirOf(dx, dy);
    if (dir) {
      const t = gestureTarget(dir);
      hint.textContent = t.label;
      hint.style.background = t.color + "33";
      hint.style.color = t.color;
      hint.classList.add("show");
    } else hint.classList.remove("show");
  });
  const end = (e) => {
    if (!start) return;
    const dir = dirOf(e.clientX - start.x, e.clientY - start.y);
    start = null;
    card.classList.remove("dragging");
    const springBack = () => {
      card.classList.add("springing");
      card.style.transform = "";
      hint.classList.remove("show");
    };
    if (!dir || e.type !== "pointerup") { springBack(); return; }
    // Stay where released; flyOutCard() continues from here if the sort goes through.
    decide(gestureTarget(dir).action).finally(() => { if (card.style.transform) springBack(); });
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function setupTrackpadFlick() {
  let acc = 0, lastT = 0, lockedUntil = 0;
  $("main").addEventListener("wheel", (e) => {
    if (S.mode !== "swipe" || !S.session || !S.session.current || modalOpen()) return;
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
    e.preventDefault();
    const now = performance.now();
    if (now < lockedUntil) return;
    if (now - lastT > 250) acc = 0;
    lastT = now;
    // With macOS "natural scrolling" (the default) fingers moving right give negative deltaX; without it
    // positive. WebKit says which via webkitDirectionInvertedFromDevice, so the card always follows the fingers.
    const natural = e.webkitDirectionInvertedFromDevice !== false;
    acc += natural ? e.deltaX : -e.deltaX;
    if (Math.abs(acc) > 140) {
      const dir = acc < 0 ? "right" : "left";
      acc = 0;
      lockedUntil = now + 700;
      decide(gestureTarget(dir).action);
    }
  }, { passive: false });
}

function setupWaveSeek() {
  const wave = $("wave");
  const hover = $("wave-hover");
  const secAt = (e) => {
    const r = wave.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * Player.duration;
  };
  wave.addEventListener("click", (e) => Player.duration && Player.seek(secAt(e)));
  wave.addEventListener("mousemove", (e) => {
    if (!Player.duration) return;
    const r = wave.getBoundingClientRect();
    hover.hidden = false;
    hover.style.left = `${e.clientX - r.left}px`;
    hover.textContent = fmtTime(secAt(e));
  });
  wave.addEventListener("mouseleave", () => { hover.hidden = true; });
}

function setupDrop() {
  const overlay = $("drop-overlay");
  let depth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; overlay.hidden = false; });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; overlay.hidden = true; } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    // In the app, pywebview delivers the full path to Python (app.py), which calls loadFolder().
    if (window.pywebview) return;
    toast("Drag and drop works in the app window. Use “Choose music folder” here.", { level: "warning" });
  });
}

// ------------------------------------------------------------------ polling

async function pollNotices() {
  if (!S.session) return;
  try {
    const list = await api("notices", { since: S.noticeSeq });
    let needRefresh = false;
    for (const n of list) {
      S.noticeSeq = Math.max(S.noticeSeq, n.seq);
      toast(n.message, { level: n.level === "error" ? "error" : n.level === "warning" ? "warning" : "info" });
      if (n.level === "error") needRefresh = true;
    }
    if (needRefresh) { await refreshState(); refreshCountsSoon(0); }
  } catch (_) { /* backend busy or gone */ }
}

async function poll() {
  try {
    if (S.scan && S.scan.status === "scanning") {
      const st = await refreshState();
      if (st.scan.status === "ready") refreshCountsSoon(0);
      if (st.scan.status === "error") toast(`Scan failed: ${st.scan.error}`, { level: "error" });
    } else {
      await pollNotices();
    }
  } finally {
    setTimeout(poll, S.scan && S.scan.status === "scanning" ? 200 : POLL_MS);
  }
}

// ------------------------------------------------------------------ boot

async function boot() {
  Player.init();
  setupDrag();
  setupTrackpadFlick();
  setupWaveSeek();
  setupDrop();
  window.addEventListener("keydown", onKey);
  // Re-measure whenever the canvas changes size, including when the player screen first appears.
  new ResizeObserver(() => Wave.resize()).observe(Wave.canvas);
  for (const b of document.querySelectorAll(".mode-switch button")) b.addEventListener("click", () => setMode(b.dataset.mode));
  $("btn-play").addEventListener("click", () => Player.toggle());
  $("btn-choose").addEventListener("click", chooseFolder);
  $("btn-done-load").addEventListener("click", chooseFolder);
  $("btn-done-undo").addEventListener("click", undo);
  setupPanels();
  setupListen();
  $("btn-queue").addEventListener("click", () => Drawers.toggle("queue"));
  $("btn-history").addEventListener("click", () => Drawers.toggle("history"));
  $("folder-chip").addEventListener("click", chooseFolder);
  $("btn-settings").addEventListener("click", () => { SettingsDrawer.view = "main"; Drawers.toggle("settings"); });
  $("btn-help").addEventListener("click", () => Guide.toggle());
  $("coach-close").addEventListener("click", finishCoach);
  $("filter-badge").addEventListener("click", () => Drawers.show("queue"));
  $("btn-done-clear-filter").addEventListener("click", () => FilterPanel.clear());
  $("op-badge").addEventListener("click", () => { SettingsDrawer.view = "main"; Drawers.show("settings"); });
  $("btn-done-export").addEventListener("click", exportPlaylists);
  $("btn-done-feedback").addEventListener("click", () => copyFeedback());
  $("btn-feedback-queue").addEventListener("click", () => copyFeedback());
  $("btn-done-reveal").addEventListener("click", () => api("reveal").catch((e) => toast(e.message, { level: "error" })));
  $("btn-load-default").addEventListener("click", () => S.settings.default_folder_path && loadFolder(S.settings.default_folder_path));
  $("preset-select").addEventListener("change", async (e) => {
    if (e.target.value === "__edit__") {
      e.target.blur();
      renderTopbar();
      SettingsDrawer.view = "main";
      SettingsDrawer.scrollTo = "presets";
      Drawers.show("settings");
      return;
    }
    try {
      await api("preset_select", { preset_id: e.target.value });
      await refreshState();
      refreshCountsSoon(0);
    } catch (err) { toast(err.message, { level: "error" }); }
    e.target.blur();
  });
  let volTimer = 0;
  $("volume").addEventListener("input", (e) => {
    const v = Number(e.target.value);
    Player.setVolume(v);
    clearTimeout(volTimer);
    volTimer = setTimeout(() => api("set_settings", { volume: v }).then((s) => (S.settings = s)).catch(() => {}), 400);
  });

  const st = await refreshState();
  S.mode = st.settings.default_mode_on_startup === "grid" ? "grid" : "swipe";
  if (st.settings.startup_preset_id) {
    const p = st.presets.presets.find((q) => q.id === st.settings.startup_preset_id);
    if (p) { S.mode = p.mode; await api("preset_select", { preset_id: p.id }).catch(() => {}); await refreshState(); }
  }
  $("volume").value = String(st.settings.volume);
  Player.setVolume(st.settings.volume);
  renderAll();
  requestAnimationFrame(frame);

  if (!st.session && st.scan.status === "idle" && st.settings.auto_load_default_folder && st.settings.default_folder_path) {
    await loadFolder(st.settings.default_folder_path);
    if (!S.scan || S.scan.status !== "scanning") {
      const msg = $("empty-message");
      msg.textContent = missingFolderMessage(st.settings.default_folder_path);
      msg.title = st.settings.default_folder_path;
      msg.hidden = false;
    }
  } else if (st.session) {
    refreshCountsSoon(0);
  }
  if (S.settings.show_queue_panel_on_startup && S.session) Drawers.show("queue");
  if (S.settings.check_for_updates) setTimeout(checkForUpdate, 4000);
  if (!S.settings.welcome_seen) {
    Welcome.show(); // first launch: the welcome card points to H instead of opening the guide on top
  } else if (S.settings.show_help_guide_on_startup || !S.settings.guide_seen) {
    Guide.show();
    if (!S.settings.guide_seen) api("set_settings", { guide_seen: true }).then((x) => (S.settings = x)).catch(() => {});
  }
  poll();
}

boot().catch((e) => {
  document.body.innerHTML = `<pre style="padding:24px;color:#ff7a7f">Failed to start: ${String(e.message || e)}</pre>`;
});
