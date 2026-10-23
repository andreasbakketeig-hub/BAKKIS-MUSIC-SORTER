/* BAKKIS MUSIC SORTER v2 — Discover mode ("listen" in the code): play without sorting anything.
 *
 * Plays a shuffled list from the unsorted queue and/or sorted folders, as full tracks or as
 * snippets from the middle of each track with a crossfade. No key in this mode can move a file:
 * decide() refuses while Listen.active, and the arrow keys mean previous / next / heart.
 * Hearts toggle on and off and are kept by the backend until they are copied into a folder.
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
    this.liked = new Set(this.tracks.filter((t) => t.hearted).map((t) => t.id));
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
    if (t.id === Player.curId) Player.seek(this.startFor(t.id, Player.duration)); // a one-track list starts over
    else if (fade) Player.crossfadeTo(t.id, LISTEN_FADE_MS);
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

  /** Toggle the heart on the playing track. */
  async like() {
    const t = this.current;
    if (!t || this.heartBusy) return; // a second press before the first answer would send the same toggle
    const on = !this.liked.has(t.id);
    this.heartBusy = true;
    try {
      const r = await api("listen_heart", { track_id: t.id, on });
      if (r.hearted) this.liked.add(t.id); else this.liked.delete(t.id);
      this.hearted = r.count;
      this.renderStatus();
      const btn = document.getElementById("listen-like");
      btn.classList.remove("pulse");
      void btn.offsetWidth;
      if (r.hearted) btn.classList.add("pulse");
      toast(r.hearted ? `Hearted (${r.count} in your list)` : `Heart removed (${r.count} in your list)`, {
        action: { label: "View list", fn: () => Drawers.show("hearts") },
      });
    } catch (e) { toast(e.message, { level: "error" }); }
    finally { this.heartBusy = false; }
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
    toast(`Can't play “${t ? t.name : "this track"}”, playing the next one`, { level: "warning" });
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
    like.title = this.liked.has(t.id) ? "Hearted. Click (or ↑ / F) to remove the heart" : "Heart this track (↑ or F)";
    if (this.hearted != null) document.getElementById("listen-hearted").textContent = `♥ Hearted (${this.hearted})`;
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
  document.getElementById("listen-hearted").addEventListener("click", () => Drawers.show("hearts"));
  document.getElementById("hearts-create").addEventListener("click", () => HeartsDrawer.createFolder());
  document.getElementById("hearts-export").addEventListener("click", () => HeartsDrawer.exportPlaylist());
  document.getElementById("hearts-clear").addEventListener("click", () => HeartsDrawer.clearList());
}

// ------------------------------------------------------------------ hearted list (drawer)

const HeartsDrawer = {
  items: [],

  async render() {
    const list = document.getElementById("hearts-list");
    list.textContent = "";
    try { this.items = await api("hearts"); } catch (e) { list.appendChild(el("p", "warn small", e.message)); return; }
    Listen.hearted = this.items.length;
    if (Listen.active) Listen.renderStatus();
    const name = document.getElementById("hearts-name");
    if (!name.value) name.value = `${new Date().toISOString().slice(0, 10)} Hearted`;
    document.getElementById("hearts-count").textContent = this.items.length ? `${this.items.length}` : "";
    for (const id of ["hearts-create", "hearts-export", "hearts-clear"]) document.getElementById(id).disabled = !this.items.length;
    if (!this.items.length) { list.appendChild(el("div", "q-empty", "Nothing hearted yet. Press ↑ or F in Discover.")); return; }
    for (const it of this.items) {
      const row = el("div", "q-row q-hist");
      row.appendChild(el("span", "q-label", "♥"));
      const nm = el("span", "q-name", it.name.replace(/\.[^.]+$/, ""));
      nm.title = it.current || it.path;
      row.appendChild(nm);
      if (it.missing) row.appendChild(el("span", "q-state err", "missing"));
      else if (it.source) row.appendChild(el("span", "q-state", it.source));
      const x = el("button", "link-btn danger", "✕");
      x.type = "button";
      x.title = "Remove the heart";
      x.addEventListener("click", async () => {
        await api("hearts_remove", { path: it.path }).catch(() => {});
        if (Listen.active) {
          const t = Listen.tracks.find((tr) => tr.name === it.name && Listen.liked.has(tr.id));
          if (t) Listen.liked.delete(t.id);
        }
        this.render();
      });
      row.appendChild(x);
      list.appendChild(row);
    }
  },

  async createFolder() {
    const name = document.getElementById("hearts-name").value.trim();
    try {
      const r = await api("hearts_create_folder", { name: name || null });
      if (!r.copied) { toast("None of the hearted files could be found", { level: "warning" }); return; }
      toast(`Copied ${r.copied} track${r.copied === 1 ? "" : "s"} to ${treePath(r.folder)}`, {
        ms: 8000, action: { label: "Show", fn: () => api("reveal", { target: "discover" }).catch(() => {}) },
      });
      if (r.missing.length) toast(`${r.missing.length} could not be found: ${r.missing.slice(0, 3).join(", ")}`, { level: "warning", ms: 8000 });
      if (Listen.active) Listen.liked.clear();
      document.getElementById("hearts-name").value = "";
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async exportPlaylist() {
    try {
      const r = await api("hearts_export_playlist");
      toast(`Playlist with ${r.tracks} tracks saved to BAKKIS_SORTED/_Playlists`, {
        ms: 8000, action: { label: "Show", fn: () => api("reveal", { target: "playlists" }).catch(() => {}) },
      });
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async clearList() {
    const ok = await modal({
      title: "Clear the hearted list?",
      body: "Only the list is cleared. No music files are touched.",
      buttons: [{ label: "Cancel", value: false }, { label: "Clear list", value: true, danger: true }],
    });
    if (!ok) return;
    await api("hearts_clear").catch(() => {});
    if (Listen.active) Listen.liked.clear();
    this.render();
  },
};
