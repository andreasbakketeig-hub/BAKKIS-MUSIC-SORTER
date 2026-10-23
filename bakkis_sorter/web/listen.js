/* BAKKIS MUSIC SORTER v2 — Listen mode (advanced): discover music without sorting anything.
 *
 * Plays a shuffled list from the unsorted queue and/or sorted folders, as full tracks or as
 * snippets from the middle of each track with a crossfade. No key in this mode can move a file:
 * decide() refuses while Listen.active, and the arrow keys mean previous / next / like.
 * Loaded before app.js; everything it calls from there is looked up at call time.
 */
"use strict";

const LISTEN_SNIPPETS = [30, 45, 60, 90];
const LISTEN_FADE_MS = 2500;

const Listen = {
  active: false,
  tracks: [],
  idx: 0,
  style: "snippet", // "snippet" | "full"
  snippetSec: 45,
  starts: new Map(), // track id -> start second (stable for preload and play)
  liked: new Set(),
  fadingFrom: null, // track id whose fade-out already started
  prevMode: "swipe",
  prefs: null,

  loadPrefs() {
    if (this.prefs) return this.prefs;
    let p = {};
    try { p = JSON.parse(localStorage.getItem("bakkis.listen") || "{}"); } catch (_) { /* private mode */ }
    this.prefs = { sources: p.sources || [], style: p.style === "full" ? "full" : "snippet", snippetSec: LISTEN_SNIPPETS.includes(p.snippetSec) ? p.snippetSec : 45 };
    return this.prefs;
  },

  savePrefs() {
    try { localStorage.setItem("bakkis.listen", JSON.stringify(this.prefs)); } catch (_) { /* optional */ }
  },

  get current() { return this.tracks[this.idx] || null; },

  // ---------------------------------------------------------------- source picker

  async openPicker() {
    if (!S.session) { toast("Load a folder first"); return; }
    let sources = [];
    try { sources = await api("listen_sources"); } catch (e) { toast(e.message, { level: "error" }); return; }
    if (!sources.length) { toast("Nothing to listen to yet"); return; }
    const prefs = this.loadPrefs();
    const box = document.getElementById("listen-sources");
    box.textContent = "";
    const remembered = new Set(prefs.sources);
    const anyRemembered = sources.some((s) => remembered.has(s.key));
    for (const s of sources) {
      const lab = el("label", "set-check listen-source");
      const cb = el("input");
      cb.type = "checkbox";
      cb.value = s.key;
      // First time: everything sorted (not the unsorted queue) is a good discovery default.
      cb.checked = anyRemembered ? remembered.has(s.key) : s.key !== "queue";
      lab.appendChild(cb);
      lab.appendChild(el("span", null, s.label));
      lab.appendChild(el("span", "muted small", String(s.count)));
      box.appendChild(lab);
    }
    document.getElementById(prefs.style === "full" ? "listen-style-full" : "listen-style-snippet").checked = true;
    document.getElementById("listen-snippet").value = String(prefs.snippetSec);
    document.getElementById("listen-picker").hidden = false;
  },

  closePicker() { document.getElementById("listen-picker").hidden = true; },

  get pickerOpen() { return !document.getElementById("listen-picker").hidden; },

  async startFromPicker() {
    const keys = [...document.querySelectorAll("#listen-sources input:checked")].map((c) => c.value);
    if (!keys.length) { toast("Pick at least one folder", { level: "warning" }); return; }
    const style = document.getElementById("listen-style-full").checked ? "full" : "snippet";
    const snippetSec = Number(document.getElementById("listen-snippet").value) || 45;
    this.prefs = { sources: keys, style, snippetSec };
    this.savePrefs();
    try {
      const res = await api("listen_start", { sources: keys });
      this.closePicker();
      this.begin(res.tracks, style, snippetSec);
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  // ---------------------------------------------------------------- session

  begin(tracks, style, snippetSec) {
    if (!this.active) this.prevMode = S.mode === "listen" ? "swipe" : S.mode;
    this.active = true;
    this.tracks = tracks.map((t) => ({ ...t, folder: t.source }));
    this.style = style;
    this.snippetSec = snippetSec;
    this.starts.clear();
    this.liked.clear();
    this.idx = 0;
    S.mode = "listen";
    Drawers.close();
    renderAll();
    this.play(0, false);
  },

  async exit() {
    if (!this.active) return;
    this.active = false;
    this.closePicker();
    api("listen_stop").catch(() => {});
    S.mode = this.prevMode || "swipe";
    Player.curId = null; // makes applySession load the sorting track again
    Player.userPaused = false;
    applySession(S.session);
    refreshCountsSoon(0);
  },

  /** Where a track starts: snippets come from the middle, full tracks from the top. */
  startFor(id, duration) {
    if (this.starts.has(id)) return this.starts.get(id);
    let start = 0;
    if (this.style === "snippet" && isFinite(duration) && duration > this.snippetSec + 30) {
      const lo = duration * 0.25, hi = Math.min(duration * 0.6, duration - this.snippetSec - 10);
      start = Math.max(0, Math.floor((lo + Math.random() * Math.max(0, hi - lo)) / 5) * 5);
    }
    this.starts.set(id, start);
    return start;
  },

  play(i, fade) {
    if (!this.tracks.length) return;
    this.idx = (i + this.tracks.length) % this.tracks.length;
    const t = this.current;
    this.fadingFrom = null;
    Wave.reset(t.id);
    if (fade) Player.crossfadeTo(t.id, LISTEN_FADE_MS);
    else Player.load(t.id);
    this.renderCard();
    this.renderStatus();
    const upcoming = [1, 2, 3].map((k) => this.tracks[(this.idx + k) % this.tracks.length]).filter(Boolean);
    if (upcoming[0] && upcoming[0].id !== t.id) Player.preload(upcoming[0].id);
    api("listen_prefetch", { track_ids: upcoming.map((x) => x.id) }).catch(() => {});
    for (const u of upcoming) getMeta(u.id).catch(() => {});
  },

  next(fade = false) {
    if (this.idx + 1 >= this.tracks.length) toast("That was the whole list. Starting over.");
    this.play(this.idx + 1, fade);
  },

  prev() {
    // Like most players: go back to the start of this track first, then to the previous one.
    if (Player.cur.currentTime - (this.starts.get(this.current.id) || 0) > 5) { Player.seek(this.starts.get(this.current.id) || 0); return; }
    this.play(this.idx - 1, false);
  },

  async like() {
    const t = this.current;
    if (!t) return;
    try {
      const r = await api("listen_like", { track_id: t.id });
      this.liked.add(t.id);
      this.renderStatus();
      const btn = document.getElementById("listen-like");
      btn.classList.remove("pulse");
      void btn.offsetWidth;
      btn.classList.add("pulse");
      toast(r.added ? `Saved to “Liked while listening” (${r.count})` : "Already in “Liked while listening”", {
        action: { label: "Show", fn: () => api("reveal", { target: "playlists" }).catch(() => {}) },
      });
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  /** Called every frame: start the crossfade before a snippet (or a full track) runs out. */
  tick() {
    if (!this.active || !this.current || Player.curId !== this.current.id || Player.cur.paused) return;
    if (this.fadingFrom === this.current.id) return;
    const t = Player.cur.currentTime;
    const d = Player.duration;
    const start = this.starts.get(this.current.id) || 0;
    const fadeSec = LISTEN_FADE_MS / 1000;
    const end = this.style === "snippet" ? Math.min(start + this.snippetSec, d || Infinity) : d;
    if (end && t >= end - fadeSec) {
      this.fadingFrom = this.current.id;
      this.next(true);
    }
    this.renderCountdown(end ? Math.max(0, end - t) : null);
  },

  onEnded() { this.next(false); },

  onError() {
    const t = this.current;
    toast(`Can't play “${t ? t.name : "this track"}” — next`, { level: "warning" });
    this.next(false);
  },

  // ---------------------------------------------------------------- keys

  /** Returns true when the key was handled here. Sorting keys are never passed on. */
  onKey(e, key) {
    const arrows = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
    if (arrows[key]) {
      e.preventDefault();
      if (e.repeat) return true;
      if (key === "ArrowRight") this.next(false);
      else if (key === "ArrowLeft") this.prev();
      else if (key === "ArrowUp") this.like();
      else this.next(false);
      return true;
    }
    if (key === "f") { e.preventDefault(); if (!e.repeat) this.like(); return true; }
    if (key === "l" || key === "s" || key === "t" || key === "g") {
      e.preventDefault();
      const mode = key === "g" ? "grid" : key === "l" ? null : "swipe";
      this.exit().then(() => { if (mode && mode !== S.mode) setMode(mode); });
      return true;
    }
    if (key === "z" || key === "u" || key === "o") return true; // no undo / reorder while listening
    return false;
  },

  // ---------------------------------------------------------------- rendering

  renderCard() {
    const t = this.current;
    if (!t) return;
    const cached = S.meta.get(t.id);
    fillCard(t, cached);
    if (!cached) getMeta(t.id).then((m) => { if (this.current && this.current.id === t.id) fillCard(t, m); }).catch(() => {});
  },

  renderStatus() {
    const t = this.current;
    if (!t) return;
    document.getElementById("listen-from").textContent = `Playing from ${t.source}`;
    document.getElementById("listen-pos").textContent = `${this.idx + 1} / ${this.tracks.length}`;
    const like = document.getElementById("listen-like");
    like.classList.toggle("on", this.liked.has(t.id));
    like.title = this.liked.has(t.id) ? "Saved to “Liked while listening”" : "Save to “Liked while listening” (↑ or F)";
    document.getElementById("listen-mode-label").textContent = this.style === "snippet" ? `${this.snippetSec} s snippets` : "Full tracks";
  },

  renderCountdown(left) {
    const elc = document.getElementById("listen-countdown");
    const txt = left == null || this.style !== "snippet" ? "" : `next in ${fmtTime(left)}`;
    if (elc.textContent !== txt) elc.textContent = txt;
  },
};

function setupListen() {
  document.getElementById("listen-start").addEventListener("click", () => Listen.startFromPicker());
  document.getElementById("listen-cancel").addEventListener("click", () => {
    Listen.closePicker();
    if (!Listen.active && S.mode === "listen") setMode(Listen.prevMode || "swipe");
  });
  document.getElementById("listen-picker").addEventListener("click", (e) => { if (e.target.id === "listen-picker") Listen.closePicker(); });
  document.getElementById("listen-prev").addEventListener("click", () => Listen.prev());
  document.getElementById("listen-play").addEventListener("click", () => Player.toggle());
  document.getElementById("listen-next").addEventListener("click", () => Listen.next(false));
  document.getElementById("listen-like").addEventListener("click", () => Listen.like());
  document.getElementById("listen-change").addEventListener("click", () => Listen.openPicker());
  document.getElementById("listen-exit").addEventListener("click", () => Listen.exit());
}
