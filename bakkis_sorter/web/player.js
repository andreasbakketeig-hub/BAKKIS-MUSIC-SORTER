/* BAKKIS MUSIC SORTER — Player engine & Web Audio sound effects. */
"use strict";

import { audioUrl } from "./api.js";
import { bus } from "./bus.js";
import { S } from "./state.js";

const OUTRO_SEC = 60;
const body = document.body;

let audioCtx = null;
export function ctx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

/** Create the WebAudio context ahead of the first sort: setting up the audio device can take ~200 ms. */
export function warmUpAudio() {
  if (audioCtx || !S.settings || !S.settings.sort_confirmation_sound) return;
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 300));
  idle(() => {
    try {
      ctx();
    } catch (_) {
      /* optional */
    }
  });
}

export function clickSound() {
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
  } catch (_) {
    /* sound is optional */
  }
}

export const Player = {
  cur: new Audio(),
  next: new Audio(),
  altNext: new Audio(),
  curId: null,
  nextId: null,
  altNextId: null,
  activeStart: 0, // the active jump point (R replays from here)
  userPaused: false,
  vol: 0.8,
  fadingUntil: 0,

  init() {
    for (const a of [this.cur, this.next, this.altNext]) {
      a.preload = "auto";
      a.addEventListener("ended", () => a === this.cur && onTrackEnded());
      a.addEventListener("timeupdate", () => {
        if (a === this.cur) {
          EndWatch.tick();
          if (window.Listen && window.Listen.tick) window.Listen.tick();
          bus.emit("playback:time", { currentTime: a.currentTime, duration: this.duration });
        }
      });
      a.addEventListener("error", () => a === this.cur && a.src && onPlaybackError());
      a.addEventListener("playing", () => a === this.cur && onPlaying());
      a.addEventListener("pause", () => a === this.cur && body.classList.remove("playing"));
      a.addEventListener("durationchange", () => a === this.cur && window.Wave && window.Wave.markersDirty());
    }
  },

  setVolume(v) {
    this.vol = Math.max(0, Math.min(1, v / 100));
    this.cur.volume = this.next.volume = this.altNext.volume = this.vol;
  },

  /** Listen mode: fade the preloaded track in while the current one fades out. */
  crossfadeTo(id, ms) {
    const to = this.next,
      from = this.cur;
    if (this.nextId !== id || to.readyState < 2) {
      this.load(id);
      return;
    }
    to.muted = false;
    to.volume = 0;
    this.cur = to;
    this.next = from;
    this.curId = id;
    this.nextId = null;
    EndWatch.reset(id);
    this.activeStart = Number(to.dataset.start || 0);
    if (!this.userPaused) this.play();
    if (window.Wave && window.Wave.markersDirty) window.Wave.markersDirty();
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
    if (window.Listen && window.Listen.active && id) return window.Listen.startFor(id, duration);
    const start = (S.settings && S.settings.playback_start_seconds) || 0;
    if (!isFinite(duration) || duration <= 0) return start;
    if (duration >= start + 30) return start;
    if (duration < 60) return 0;
    return Math.floor(duration / 3 / 15) * 15; // e.g. a 3:30 radio edit with "5:00" starts at 1:00
  },

  /** Make `id` the playing track. Uses the preloaded element when it holds this track. */
  load(id, autoplay = true) {
    if (id === this.curId) return;
    this.fadingUntil = 0;
    const old = this.cur;
    if (id === this.nextId) {
      this.cur = this.next;
      this.next = old;
      this.nextId = null;
    } else if (id === this.altNextId) {
      this.cur = this.altNext;
      this.altNext = old;
      this.altNextId = null;
    } else {
      this.cur.src = audioUrl(id);
      this.cur.dataset.start = "";
      this.cur.dataset.ready = "";
    }
    this.curId = id;
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
      if (window.Wave && window.Wave.markersDirty) window.Wave.markersDirty();
    };
    if (a.error) {
      setTimeout(() => {
        if (a === this.cur && this.curId === id) onPlaybackError();
      }, 0);
      return;
    }
    if (a.readyState >= 1) begin();
    else a.addEventListener("loadedmetadata", begin, { once: true });
  },

  preload(id) {
    if (!id || id === this.nextId || id === this.curId) return;
    const wait = this.fadingUntil - performance.now();
    if (wait > 0) {
      setTimeout(() => this.preload(id), wait + 50);
      return;
    }
    const a = this.next;
    this.nextId = id;
    a.muted = true;
    a.dataset.start = "";
    a.src = audioUrl(id);
    a.addEventListener(
      "loadedmetadata",
      () => {
        if (a !== this.next || this.nextId !== id) return;
        a.dataset.start = String(this.startPointFor(a.duration, id));
        a.currentTime = Number(a.dataset.start);
      },
      { once: true }
    );
  },

  preloadAlt(id) {
    if (!id || id === this.altNextId || id === this.nextId || id === this.curId) return;
    const a = this.altNext;
    this.altNextId = id;
    a.muted = true;
    a.dataset.start = "";
    a.src = audioUrl(id);
    a.addEventListener(
      "loadedmetadata",
      () => {
        if (a !== this.altNext || this.altNextId !== id) return;
        a.dataset.start = String(this.startPointFor(a.duration, id));
        a.currentTime = Number(a.dataset.start);
      },
      { once: true }
    );
  },

  clear() {
    for (const a of [this.cur, this.next, this.altNext]) {
      a.pause();
      a.removeAttribute("src");
      a.load();
    }
    this.curId = this.nextId = this.altNextId = null;
    body.classList.remove("playing");
  },

  play() {
    const p = this.cur.play();
    if (p && p.catch) p.catch(() => body.classList.remove("playing"));
  },

  toggle() {
    if (!this.curId) return;
    if (this.cur.paused) {
      this.userPaused = false;
      EndWatch.fired = false;
      this.play();
    } else {
      this.userPaused = true;
      this.cur.pause();
    }
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
    if (d && t < d - 1) EndWatch.reset(this.curId);
    if (setActive) this.activeStart = t;
    if (this.cur.paused && !this.userPaused) this.play();
    if (window.Wave && window.Wave.markersDirty) window.Wave.markersDirty();
  },

  jump(sec) {
    const d = this.duration;
    if (d && sec >= d) {
      if (window.Wave && window.Wave.flash) window.Wave.flash(sec);
      return;
    }
    this.seek(sec, { setActive: true });
  },

  outro() {
    const d = this.duration;
    if (!d) return;
    this.seek(Math.max(0, d - OUTRO_SEC), { setActive: true });
  },

  nudge(delta) {
    this.seek(this.cur.currentTime + delta);
  },

  replay() {
    this.seek(this.activeStart);
  },

  phraseJump(forward = true) {
    if (!this.curId) return;
    const meta = S.meta.get(this.curId);
    let delta = 15.24;
    if (meta && Number(meta.jump_delta) > 0) {
      delta = Number(meta.jump_delta);
    } else if (meta && Number(meta.bpm) > 0) {
      delta = 32.0 * (60.0 / Number(meta.bpm));
    }
    this.seek(this.cur.currentTime + (forward ? delta : -delta));
  },

  dropJump() {
    if (!this.curId) return;
    const meta = S.meta.get(this.curId);
    const drop = meta && Number(meta.drop_seconds);
    if (drop && drop > 0) {
      this.jump(drop);
    } else {
      this.jump(60);
    }
  },
};

function onPlaying() {
  body.classList.add("playing");
  if (S.pendingDecisionAt) {
    const ms = performance.now() - S.pendingDecisionAt;
    S.latencies.push(Math.round(ms));
    if (S.latencies.length > 50) S.latencies.shift();
    S.pendingDecisionAt = 0;
  }
  if (window.Wave && window.Wave.loadPeaks) window.Wave.loadPeaks(Player.curId);
  bus.emit("playback:playing", { curId: Player.curId });
}

export const EndWatch = {
  STALL_MS: 800,
  id: null,
  fired: false,
  since: 0,
  lastT: -1,

  reset(id) {
    this.id = id;
    this.fired = false;
    this.since = 0;
    this.lastT = -1;
  },

  tick() {
    const a = Player.cur,
      d = Player.duration;
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
  if (window.Listen && window.Listen.active) {
    window.Listen.onEnded();
    return;
  }
  body.classList.remove("playing");
  if (!S.settings || S.settings.end_of_track === "stop") {
    Player.cur.pause();
    return;
  }
  bus.emit("playback:ended", { auto: "ended" });
}

function onPlaybackError() {
  if (window.Listen && window.Listen.active) {
    window.Listen.onError();
    return;
  }
  const cur = S.session && S.session.current;
  if (!cur || cur.id !== Player.curId) return;
  bus.emit("playback:error", { auto: "error" });
}

// Global attachments
window.Player = Player;
window.warmUpAudio = warmUpAudio;
window.clickSound = clickSound;
