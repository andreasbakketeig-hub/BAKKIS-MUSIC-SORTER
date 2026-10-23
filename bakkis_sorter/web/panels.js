/* BAKKIS MUSIC SORTER v2 — drawers and overlays: queue, settings, preset editor, guide.
 *
 * Loaded before app.js. Only defines functions and objects; everything it calls from app.js
 * (S, api, el, toast, modal, …) is looked up at call time.
 */
"use strict";

const QUEUE_ROW_H = 40;
const QUEUE_PAGE = 100;
const PALETTE = [
  ["Teal", "#00D6B4"], ["Blue", "#18A0FB"], ["Purple", "#7C5CFF"], ["Gray", "#8A8F98"], ["Orange", "#FF7A1A"],
  ["Pink", "#D65CFF"], ["Red", "#FF2D55"], ["Yellow", "#FFF200"], ["Dark", "#2A2F36"],
];
const START_CHOICES = [[0, "0:00"], [30, "0:30"], [60, "1:00"], [90, "1:30"], [120, "2:00"], [150, "2:30"], [180, "3:00"], [240, "4:00"], [300, "5:00"], [360, "6:00"]];

// Promo / contact. Links open in the default browser via the backend (never inside the app window).
const CONTACT_EMAIL = "bakkis@bakkis.com";
const SUPPORT_URL = "https://ko-fi.com/bakkis";
const FOLLOW_LINKS = [
  ["Spotify", "https://open.spotify.com/artist/4gKuT9rOlvCpHJUkWp4JFv"],
  ["SoundCloud", "https://soundcloud.com/bakkismusic"],
  ["Instagram", "https://www.instagram.com/bakkismusic/"],
  ["Beatport", "https://www.beatport.com/artist/bakkis/1216154"],
  ["Traxsource", "https://www.traxsource.com/artist/842861/bakkis"],
  ["bakkis.com", "https://bakkis.com"],
];

function openExternal(url) {
  api("open_url", { url }).catch(() => toast("Could not open the link", { level: "error" }));
}

/** mailto: link with app + macOS version and the latest warnings, so a bug report is useful right away. */
async function reportProblem() {
  let about = {};
  try { about = await api("about"); } catch (_) { /* send what we have */ }
  let recent = [];
  try { recent = (await api("notices", { since: 0 })).filter((n) => n.level !== "info").slice(-5); } catch (_) { /* no session */ }
  const lines = [
    "What happened?",
    "",
    "",
    "What did you expect?",
    "",
    "",
    "---",
    `App: BAKKIS MUSIC SORTER ${about.version || S.version || ""}`,
    `macOS: ${about.macos || ""}`,
    `Mode: ${S.mode} · ${S.settings ? S.settings.file_operation : ""}`,
  ];
  if (recent.length) lines.push("Recent messages:", ...recent.map((n) => `- [${n.level}] ${n.message}`));
  const subject = `BAKKIS MUSIC SORTER ${about.version || ""} – problem`;
  openExternal(`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(lines.join("\n"))}`);
}

// ------------------------------------------------------------------ drawers

const Drawers = {
  open: null,

  toggle(name) { this.open === name ? this.close() : this.show(name); },

  show(name) {
    this.close();
    this.open = name;
    document.getElementById(`drawer-${name}`).hidden = false;
    document.body.classList.add("drawer-open");
    document.body.classList.toggle("drawer-wide-open", document.getElementById(`drawer-${name}`).classList.contains("drawer-wide"));
    if (name === "queue") QueueDrawer.refresh();
    if (name === "settings") SettingsDrawer.render();
    if (name === "history") HistoryDrawer.render();
    if (name === "hearts") HeartsDrawer.render();
  },

  close() {
    if (!this.open) return;
    document.getElementById(`drawer-${this.open}`).hidden = true;
    document.body.classList.remove("drawer-open", "drawer-wide-open");
    this.open = null;
  },

  /** Called by app.js after every session snapshot. */
  onSession() {
    if (this.open === "queue") QueueDrawer.refreshSoon();
  },
};

// ------------------------------------------------------------------ queue drawer

const QueueDrawer = {
  query: "",
  pages: new Map(),
  total: 0,
  timer: 0,
  inflight: new Set(),

  refreshSoon() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.refresh(), 80);
  },

  async refresh() {
    this.pages.clear();
    this.renderHistory();
    FilterPanel.render();
    await this.fetchPage(0);
    this.renderList();
  },

  /** Resolves true only when a page was actually loaded (callers re-render on that). */
  async fetchPage(n) {
    if (this.pages.has(n) || this.inflight.has(n)) return false;
    this.inflight.add(n);
    try {
      const q = this.query;
      const res = await api("queue", { offset: n * QUEUE_PAGE, limit: QUEUE_PAGE, q });
      if (q !== this.query) return false; // the search changed while loading
      this.total = res.total;
      this.pages.set(n, res.items);
      return true;
    } catch (_) {
      return false; // the next refresh retries
    } finally {
      this.inflight.delete(n);
    }
  },

  renderHistory() {
    const box = document.getElementById("queue-history");
    box.textContent = "";
    const hist = (S.session && S.session.history) || [];
    if (!hist.length) { box.appendChild(el("div", "q-empty", "Nothing sorted yet")); return; }
    for (const a of hist) {
      const row = el("div", "q-row q-hist");
      row.appendChild(el("span", `q-label q-${a.status}`, a.label));
      row.appendChild(el("span", "q-name", a.name));
      if (a.status === "pending") row.appendChild(el("span", "q-state", "…"));
      if (a.status === "failed") row.appendChild(el("span", "q-state err", "failed"));
      box.appendChild(row);
    }
    const hint = el("div", "q-hint", "Z undoes the most recent action");
    box.appendChild(hint);
  },

  renderList() {
    const scroller = document.getElementById("queue-list");
    const inner = document.getElementById("queue-inner");
    document.getElementById("queue-count").textContent = this.total ? String(this.total) : "";
    inner.style.height = `${this.total * QUEUE_ROW_H}px`;
    inner.textContent = "";
    if (!this.total) { inner.appendChild(el("div", "q-empty", this.query ? "No matches" : "No more tracks")); return; }
    const first = Math.max(0, Math.floor(scroller.scrollTop / QUEUE_ROW_H) - 5);
    const last = Math.min(this.total - 1, Math.ceil((scroller.scrollTop + scroller.clientHeight) / QUEUE_ROW_H) + 5);
    const missing = new Set();
    for (let i = first; i <= last; i++) {
      const page = this.pages.get(Math.floor(i / QUEUE_PAGE));
      if (!page) { missing.add(Math.floor(i / QUEUE_PAGE)); continue; }
      const t = page[i % QUEUE_PAGE];
      if (!t) continue;
      const row = el("button", "q-row q-track");
      row.type = "button";
      row.style.top = `${i * QUEUE_ROW_H}px`;
      row.title = "Play next";
      row.appendChild(el("span", "q-idx", String(t.index + 1)));
      const txt = el("span", "q-text");
      txt.appendChild(el("span", "q-name", t.name.replace(/\.[^.]+$/, "")));
      if (t.folder) txt.appendChild(el("span", "q-folder", t.folder));
      row.appendChild(txt);
      if (t.bpm || t.key) row.appendChild(el("span", "q-meta", [t.bpm, t.key].filter(Boolean).join(" · ")));
      if (t.index === 0) row.appendChild(el("span", "q-state", "next"));
      row.addEventListener("click", () => this.playNext(t));
      inner.appendChild(row);
    }
    for (const n of missing) this.fetchPage(n).then((loaded) => loaded && this.renderList());
  },

  async playNext(t) {
    try {
      applySession(await api("play_next", { track_id: t.id }));
      toast(`“${t.name.replace(/\.[^.]+$/, "")}” plays next`);
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};

// ------------------------------------------------------------------ BPM / key filter (queue drawer)

const KEY_CODES = Array.from({ length: 12 }, (_, i) => [`${i + 1}A`, `${i + 1}B`]).flat();
const KEY_NAMES = {
  "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm", "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "C#m",
  "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb", "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
};

const FilterPanel = {
  built: false,

  build() {
    if (this.built) return;
    this.built = true;
    const sel = document.getElementById("qf-key");
    const any = el("option", null, "Any");
    any.value = "";
    sel.appendChild(any);
    for (const c of KEY_CODES) {
      const o = el("option", null, `${c} · ${KEY_NAMES[c]}`);
      o.value = c;
      sel.appendChild(o);
    }
    document.getElementById("qf-apply").addEventListener("click", () => this.apply());
    document.getElementById("qf-clear").addEventListener("click", () => this.clear());
    for (const id of ["qf-bpm-min", "qf-bpm-max"]) {
      document.getElementById(id).addEventListener("keydown", (e) => { if (e.key === "Enter") this.apply(); });
    }
  },

  render() {
    const box = document.getElementById("queue-filter");
    const on = !!(S.settings && S.settings.queue_filter && S.session);
    box.hidden = !on;
    if (!on) return;
    this.build();
    const f = S.session.filter;
    const status = document.getElementById("qf-status");
    if (f) {
      const parts = [];
      if (f.bpm_min != null || f.bpm_max != null) parts.push(`${f.bpm_min ?? "…"}–${f.bpm_max ?? "…"} BPM`);
      if (f.key) parts.push(`${f.key}${f.compatible ? " + compatible" : ""}`);
      status.textContent = `Filtered${parts.length ? ` (${parts.join(", ")})` : ""}: ${S.session.remaining - S.session.held} to go, ${S.session.held} set aside. Tracks without BPM/key tags are set aside when filtering on them.`;
    } else {
      status.textContent = "Show only tracks in a BPM range and/or key. Nothing is moved; other tracks wait until you clear the filter.";
    }
  },

  async apply() {
    const num = (id) => { const v = document.getElementById(id).value.trim(); return v === "" ? null : Number(v); };
    const params = {
      bpm_min: num("qf-bpm-min"),
      bpm_max: num("qf-bpm-max"),
      key: document.getElementById("qf-key").value || null,
      compatible: document.getElementById("qf-compatible").checked,
      order: document.getElementById("qf-order").value,
    };
    if (params.bpm_min != null && params.bpm_max != null && params.bpm_min > params.bpm_max) {
      [params.bpm_min, params.bpm_max] = [params.bpm_max, params.bpm_min];
    }
    try {
      applySession(await api("set_filter", params));
      QueueDrawer.refresh();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async clear() {
    try {
      applySession(await api("clear_filter"));
      QueueDrawer.refresh();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};

// ------------------------------------------------------------------ history drawer (past sessions)

const HistoryDrawer = {
  SHOW: 40,
  expanded: new Set(),

  async render() {
    const body = document.getElementById("history-body");
    body.textContent = "";
    body.appendChild(el("p", "muted small", "Everything sorted in the last 90 days that is still in place. Put tracks back where they came from; nothing is ever overwritten."));
    let sessions = [];
    try { sessions = await api("history"); } catch (e) { body.appendChild(el("p", "warn small", e.message)); return; }
    if (!sessions.length) { body.appendChild(el("div", "q-empty", "Nothing sorted yet")); return; }
    for (const sess of sessions) body.appendChild(this.renderSession(sess));
  },

  renderSession(sess) {
    const box = el("div", "hist-session");
    const head = el("div", "hist-head");
    const when = sess.started ? new Date(sess.started).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : sess.id;
    const folder = (sess.root || "").split("/").filter(Boolean).pop() || "";
    const title = el("div", "hist-title");
    title.appendChild(el("span", null, when));
    title.appendChild(el("span", "muted small", `${folder ? folder + " · " : ""}${sess.items.length} track${sess.items.length === 1 ? "" : "s"}`));
    head.appendChild(title);
    const actions = el("div", "hist-actions");
    const fb = el("button", "ghost-btn small-btn", "Copy feedback");
    fb.type = "button";
    fb.title = "Copy a summary of this session's decisions";
    fb.addEventListener("click", () => copyFeedback(sess.id));
    actions.appendChild(fb);
    head.appendChild(actions);
    const restorable = sess.items.filter((i) => i.status === "sorted");
    if (restorable.length) {
      const all = el("button", "ghost-btn small-btn", `Put back all (${restorable.length})`);
      all.type = "button";
      all.addEventListener("click", () => this.putBack(restorable, `Put back ${restorable.length} tracks from ${when}?`));
      actions.appendChild(all);
    }
    box.appendChild(head);
    const list = el("div", "hist-items");
    const open = this.expanded.has(sess.id);
    const shown = open ? sess.items : sess.items.slice(0, this.SHOW);
    for (const it of shown) {
      const row = el("div", "q-row q-hist");
      row.appendChild(el("span", `q-label${it.op === "copy" ? " copy" : ""}`, it.label || ""));
      const name = el("span", "q-name", it.name);
      name.title = it.dest;
      row.appendChild(name);
      if (it.status === "sorted") {
        const b = el("button", "link-btn", it.op === "copy" ? "Remove copy" : "Put back");
        b.type = "button";
        b.addEventListener("click", () => this.putBack([it]));
        row.appendChild(b);
      } else {
        row.appendChild(el("span", "q-state", it.status === "current" ? "this session · Z" : "moved since"));
      }
      list.appendChild(row);
    }
    if (sess.items.length > shown.length) {
      const more = el("button", "link-btn", `Show all ${sess.items.length}`);
      more.type = "button";
      more.addEventListener("click", () => { this.expanded.add(sess.id); this.render(); });
      list.appendChild(more);
    }
    box.appendChild(list);
    return box;
  },

  async putBack(items, confirmText) {
    if (confirmText) {
      const ok = await modal({
        title: confirmText,
        body: "Moved tracks go back to their original folders (with a new name if the old one is taken). Copies are deleted; originals are untouched.",
        buttons: [{ label: "Cancel", value: false }, { label: "Put back", value: true, primary: true }],
      });
      if (!ok) return;
    }
    try {
      const res = await api("history_restore", { entry_ids: items.map((i) => i.id) });
      const ok = res.filter((r) => r.ok).length;
      const failed = res.filter((r) => !r.ok);
      if (ok) toast(ok === 1 ? res.find((r) => r.ok).message : `Put back ${ok} tracks`);
      for (const f of failed.slice(0, 3)) toast(f.message, { level: "warning", ms: 6000 });
      if (S.session) await refreshState();
      refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};

// ------------------------------------------------------------------ settings drawer

// The main view holds what most people change; everything else waits in the collapsed Advanced part.
// `show` hides a setting while it has no effect (e.g. copy-only options in move mode).
const isCopy = () => S.settings && S.settings.file_operation === "copy";
const SETTINGS_SCHEMA = [
  { section: "Music folder" },
  { key: "default_folder_path", type: "folder", label: "Default folder" },
  { key: "auto_load_default_folder", type: "bool", label: "Load it on startup", show: () => !!S.settings.default_folder_path },
  { key: "include_subfolders", type: "bool", label: "Include subfolders" },
  { key: "track_order", type: "select", label: "Track order", options: [["random", "Random"], ["in_order", "In order"]] },
  { section: "Sorting" },
  {
    key: "file_operation", type: "select", label: "Files", options: [["move", "Move tracks"], ["copy", "Copy tracks"]],
    note: () => isCopy()
      ? "Copies go to BAKKIS_SORTED_COPIES; your originals stay where they are."
      : "Tracks move into BAKKIS_SORTED. Rekordbox and Serato lose track of moved files; choose Copy if that matters.",
  },
  {
    key: "swipe_keep_direction", type: "select", label: "Swipe direction",
    options: [["right", "Keep → right, Archive ← left"], ["left", "Keep ← left, Archive → right"]],
  },
  { section: "Playback" },
  { key: "playback_start_seconds", type: "select", label: "Start tracks at", options: START_CHOICES, number: true, note: "Short tracks start about a third of the way in." },
  { key: "end_of_track", type: "select", label: "When a track ends", options: [["skip", "Skip it and play the next"], ["stop", "Stop"]] },
  { section: "Presets" },
  { type: "presets" },
  { advanced: true },
  { sub: "Files" },
  { key: "cleanup_empty_source_folders_after_sort", type: "bool", label: "Remove folders that become empty", note: "Undo recreates them.", show: () => !isCopy() },
  { key: "hide_copied_on_rescan", type: "bool", label: "Hide tracks I already copied", note: "When the folder is loaded again.", show: isCopy },
  { key: "create_dated_sorting_sessions", type: "bool", label: "Date folders for new presets", note: "A folder per day: <preset>/YYYY-MM-DD/. Each preset has its own switch." },
  { sub: "Controls" },
  { key: "swipe_archive_hotkey", type: "select", label: "Up arrow key", options: [["down", "Favorite"], ["up", "Archive (drag the card up for Favorite)"]] },
  { sub: "Look and sound" },
  { key: "colored_tiles", type: "bool", label: "Colored tiles", note: "Off: plain dark tiles with a thin colour stripe." },
  { key: "sort_confirmation_sound", type: "bool", label: "Click sound on sort" },
  { sub: "Features" },
  { key: "discover_in_topbar", type: "bool", label: "Discover in the top bar", note: "Listen through your folders and heart tracks. Never moves files. Shortcut: L." },
  { key: "queue_filter", type: "bool", label: "BPM and key filter in the queue" },
  { type: "export" },
  { sub: "Startup" },
  { key: "default_mode_on_startup", type: "select", label: "Mode on startup", options: [["swipe", "Swipe"], ["grid", "Grid"]] },
  { key: "startup_preset_id", type: "startup_preset", label: "Preset on startup" },
  { key: "show_help_guide_on_startup", type: "bool", label: "Show shortcuts on startup" },
  { key: "show_queue_panel_on_startup", type: "bool", label: "Open the queue on startup" },
  { key: "check_for_updates", type: "bool", label: "Check for new versions", note: "One request to GitHub at startup. Nothing about you or your music is sent." },
  { type: "reset" },
];

const SettingsDrawer = {
  view: "main", // or {editing preset}
  scrollTo: null,

  async save(changes) {
    try {
      S.settings = await api("set_settings", changes);
      renderAll();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  render() {
    const body = document.getElementById("settings-body");
    body.textContent = "";
    if (this.view !== "main") { PresetEditor.render(body, this.view); return; }
    document.getElementById("settings-title").textContent = "Settings";
    let box = body;
    for (const item of SETTINGS_SCHEMA) {
      if (item.show && !item.show()) continue;
      if (item.section) { box.appendChild(el("h3", "set-section", item.section)); continue; }
      if (item.sub) { box.appendChild(el("h4", "set-sub", item.sub)); continue; }
      if (item.advanced) { box = this.advancedBox(body); continue; }
      if (item.type === "presets") { this.renderPresets(box); continue; }
      if (item.type === "export") { box.appendChild(this.exportRow()); continue; }
      if (item.type === "reset") { box.appendChild(this.resetRow()); continue; }
      box.appendChild(this.renderItem(item));
    }
    this.renderAbout(body);
    if (this.scrollTo) {
      const target = body.querySelector(`[data-anchor="${this.scrollTo}"]`);
      if (target) target.scrollIntoView();
      this.scrollTo = null;
    }
  },

  /** The collapsed "Advanced" part; remembers whether it was open. */
  advancedBox(body) {
    const d = el("details", "set-advanced");
    try { d.open = localStorage.getItem("bakkis.settings.advanced") === "1"; } catch (_) { /* private mode */ }
    d.addEventListener("toggle", () => { try { localStorage.setItem("bakkis.settings.advanced", d.open ? "1" : "0"); } catch (_) { /* ignore */ } });
    d.appendChild(el("summary", "set-section", "Advanced"));
    body.appendChild(d);
    return d;
  },

  exportRow() {
    const row = el("div", "set-row");
    const b = el("button", "ghost-btn small-btn", "Export playlists (M3U8)");
    b.type = "button";
    b.disabled = !S.session;
    b.addEventListener("click", exportPlaylists);
    row.appendChild(b);
    row.appendChild(el("div", "set-note", "One playlist per sorted folder, for Rekordbox (File → Import → Playlist) or Serato (drag onto the crate list)."));
    return row;
  },

  resetRow() {
    const row = el("div", "set-row");
    const b = el("button", "ghost-btn small-btn", "Reset all settings");
    b.type = "button";
    b.addEventListener("click", async () => {
      const ok = await modal({
        title: "Reset all settings?",
        body: "Settings go back to their defaults. Your presets, history, default folder and sorted files stay as they are.",
        buttons: [{ label: "Cancel", value: false }, { label: "Reset", value: true, danger: true }],
      });
      if (!ok) return;
      try {
        S.settings = await api("reset_settings");
        renderAll();
        this.render();
        toast("Settings reset");
      } catch (e) { toast(e.message, { level: "error" }); }
    });
    row.appendChild(b);
    return row;
  },

  renderItem(item) {
    const s = S.settings;
    const row = el("div", "set-row");
    const id = `set-${item.key}`;
    if (item.type === "bool") {
      const lab = el("label", "set-check");
      const cb = el("input");
      cb.type = "checkbox";
      cb.id = id;
      cb.checked = !!s[item.key];
      cb.addEventListener("change", () => this.onBool(item, cb));
      lab.appendChild(cb);
      lab.appendChild(el("span", null, item.label));
      row.appendChild(lab);
    } else if (item.type === "select" || item.type === "startup_preset") {
      row.appendChild(el("label", "set-label", item.label)).htmlFor = id;
      const sel = el("select", "set-select");
      sel.id = id;
      let opts = item.options;
      if (item.type === "startup_preset") {
        opts = [["", "Last used"]].concat(S.presets.presets.map((p) => [p.id, `${p.mode === "swipe" ? "Swipe" : "Grid"} · ${p.name}`]));
      }
      for (const [v, label] of opts) {
        const o = el("option", null, label);
        o.value = String(v);
        if (String(s[item.key] == null ? "" : s[item.key]) === String(v)) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => {
        let v = sel.value;
        if (item.number) v = Number(v);
        if (item.type === "startup_preset" && !v) v = null;
        const changes = { [item.key]: v };
        if (item.key === "file_operation" && v === "move") changes.move_warning_acknowledged = true;
        this.save(changes).then(() => { if (item.key === "file_operation") this.render(); }); // options that depend on it
      });
      row.appendChild(sel);
    } else if (item.type === "folder") {
      row.appendChild(el("div", "set-label", item.label));
      const path = s.default_folder_path;
      const val = el("div", "set-path");
      if (path) {
        const f = describeFolder(path);
        val.appendChild(el("span", "set-path-name", f.name));
        val.appendChild(el("span", null, ` · ${f.where}`));
      } else {
        val.textContent = "None";
      }
      val.title = path || "";
      if (path && !S.defaultFolderExists) val.classList.add("warn");
      row.appendChild(val);
      const btns = el("div", "set-btns");
      const mk = (label, fn, disabled) => {
        const b = el("button", "ghost-btn small-btn", label);
        b.type = "button";
        b.disabled = !!disabled;
        b.addEventListener("click", fn);
        btns.appendChild(b);
      };
      mk("Use current folder", () => this.save({ default_folder_path: S.session.root }).then(refreshState).then(() => this.render()), !S.session);
      mk("Choose…", async () => { const p = await pickFolderPath(); if (p) { await this.save({ default_folder_path: p }); await refreshState(); this.render(); } });
      mk("Clear", () => this.save({ default_folder_path: null }).then(() => this.render()), !path);
      if (path && S.defaultFolderExists) mk("Load now", () => { Drawers.close(); loadFolder(path); });
      row.appendChild(btns);
    }
    const note = typeof item.note === "function" ? item.note() : item.note;
    if (note) row.appendChild(el("div", "set-note", note));
    return row;
  },

  async onBool(item, cb) {
    if (item.key === "cleanup_empty_source_folders_after_sort" && cb.checked) {
      const ok = await modal({
        title: "Remove empty source folders?",
        body: "After a track is moved, folders it leaves empty are deleted (only junk files like .DS_Store are removed with them). Undo recreates them. Nothing above the music folder is touched.",
        buttons: [{ label: "Cancel", value: false }, { label: "Turn on", value: true, primary: true }],
      });
      if (!ok) { cb.checked = false; return; }
    }
    await this.save({ [item.key]: cb.checked });
  },

  renderAbout(body) {
    body.appendChild(el("h3", "set-section", "About"));
    const box = el("div", "about");
    const logo = el("span", "logo logo-boxed about-logo");
    logo.setAttribute("role", "img");
    logo.setAttribute("aria-label", "BAKKIS");
    box.appendChild(logo);
    box.appendChild(el("div", "muted small", `BAKKIS MUSIC SORTER ${S.version || ""} · Free and open source (MIT)`));
    if (S.update && S.update.newer) {
      const up = el("button", "link-btn", `Version ${S.update.latest} is available: download`);
      up.type = "button";
      up.addEventListener("click", () => openExternal(S.update.url));
      box.appendChild(up);
    }
    const follow = el("div", "about-links");
    follow.appendChild(el("span", "muted small", "Follow BAKKIS"));
    for (const [label, url] of FOLLOW_LINKS) {
      const a = el("button", "link-btn", label);
      a.type = "button";
      a.title = url;
      a.addEventListener("click", () => openExternal(url));
      follow.appendChild(a);
    }
    box.appendChild(follow);
    const btns = el("div", "set-btns");
    const mk = (label, fn, cls = "ghost-btn small-btn") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.addEventListener("click", fn);
      btns.appendChild(b);
    };
    if (SUPPORT_URL) mk("♥ Support", () => openExternal(SUPPORT_URL), "primary-btn small-btn");
    mk("Why I built this", () => { Drawers.close(); Welcome.show(); });
    mk("Report a problem", reportProblem);
    mk(`Contact: ${CONTACT_EMAIL}`, () => openExternal(`mailto:${CONTACT_EMAIL}`));
    box.appendChild(btns);
    body.appendChild(box);
  },

  renderPresets(body) {
    const wrap = el("div", "set-presets");
    wrap.dataset.anchor = "presets";
    for (const mode of ["swipe", "grid"]) {
      wrap.appendChild(el("div", "set-label", mode === "swipe" ? "Swipe presets" : "Grid presets"));
      const activeId = S.presets[`active_${mode}_preset_id`];
      const list = S.presets.presets.filter((p) => p.mode === mode);
      for (const p of list) {
        const row = el("div", "preset-row");
        const sw = el("span", "preset-swatches");
        p.colors.slice(0, mode === "swipe" ? 3 : 9).forEach((c) => { const d = el("i"); d.style.background = c; sw.appendChild(d); });
        row.appendChild(sw);
        row.appendChild(el("span", "preset-name", p.name));
        if (p.id === activeId) row.appendChild(el("span", "preset-active", "active"));
        else {
          const use = el("button", "link-btn", "Use");
          use.type = "button";
          use.addEventListener("click", () => this.selectPreset(p));
          row.appendChild(use);
        }
        const edit = el("button", "link-btn", "Edit");
        edit.type = "button";
        edit.addEventListener("click", () => { this.view = { preset: p, mode }; this.render(); });
        row.appendChild(edit);
        const del = el("button", "link-btn danger", "Delete");
        del.type = "button";
        del.disabled = list.length <= 1;
        del.title = list.length <= 1 ? "Each mode needs at least one preset" : "";
        del.addEventListener("click", () => this.deletePreset(p));
        row.appendChild(del);
        wrap.appendChild(row);
      }
      const add = el("button", "ghost-btn small-btn", mode === "swipe" ? "+ New swipe preset" : "+ New grid preset");
      add.type = "button";
      add.addEventListener("click", () => { this.view = { preset: null, mode }; this.render(); });
      wrap.appendChild(add);
    }
    body.appendChild(wrap);
  },

  async selectPreset(p) {
    try {
      await api("preset_select", { preset_id: p.id });
      await refreshState();
      if (p.mode !== S.mode) setMode(p.mode);
      refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async deletePreset(p) {
    const ok = await modal({
      title: `Delete “${p.name}”?`,
      body: "The preset is removed. Folders already created in BAKKIS_SORTED stay where they are.",
      buttons: [{ label: "Cancel", value: false }, { label: "Delete", value: true, danger: true }],
    });
    if (!ok) return;
    try {
      await api("preset_delete", { preset_id: p.id });
      await refreshState();
      refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};

// ------------------------------------------------------------------ preset editor

const PresetEditor = {
  draft: null,

  start(view) {
    const { preset, mode } = view;
    if (preset) {
      this.draft = JSON.parse(JSON.stringify(preset));
    } else {
      const base = activePreset(mode);
      this.draft = {
        id: null,
        mode,
        name: mode === "swipe" ? "New swipe preset" : "New grid preset",
        labels: [...base.labels],
        colors: [...base.colors],
        folders: [...base.folders],
        dated: !!(S.settings && S.settings.create_dated_sorting_sessions),
      };
    }
  },

  render(body, view) {
    if (!this.draft || this.draft._view !== view) { this.start(view); this.draft._view = view; }
    const d = this.draft;
    document.getElementById("settings-title").textContent = d.id ? "Edit preset" : "New preset";

    const back = el("button", "link-btn", "← Back to settings");
    back.type = "button";
    back.addEventListener("click", () => this.leave());
    body.appendChild(back);

    const nameRow = el("div", "set-row");
    nameRow.appendChild(el("label", "set-label", "Name"));
    const name = el("input", "set-input");
    name.value = d.name;
    name.maxLength = 80;
    name.addEventListener("input", () => (d.name = name.value));
    nameRow.appendChild(name);
    const tree = S.settings && S.settings.file_operation === "copy" ? "BAKKIS_SORTED_COPIES" : "BAKKIS_SORTED";
    const output = () => `Output: ${tree}/${d.name || "…"}/${d.dated ? "YYYY-MM-DD/" : ""}`;
    nameRow.appendChild(el("div", "set-note", output()));
    name.addEventListener("input", () => (nameRow.lastChild.textContent = output()));
    body.appendChild(nameRow);

    const datedRow = el("div", "set-row");
    const datedLab = el("label", "set-check");
    const dated = el("input");
    dated.type = "checkbox";
    dated.checked = !!d.dated;
    dated.addEventListener("change", () => { d.dated = dated.checked; nameRow.lastChild.textContent = output(); });
    datedLab.appendChild(dated);
    datedLab.appendChild(el("span", null, "Date folders"));
    datedRow.appendChild(datedLab);
    datedRow.appendChild(el("div", "set-note", "A new folder for each day you sort. Handy for weekly promos; leave it off to keep one folder per genre."));
    body.appendChild(datedRow);

    const slots = el("div", "slot-list");
    const n = d.mode === "swipe" ? 3 : 9;
    const head = el("div", "slot-row slot-head");
    head.appendChild(el("span"));
    head.appendChild(el("span", null, "Label"));
    head.appendChild(el("span", null, d.mode === "grid" ? "Folder" : "Color"));
    slots.appendChild(head);
    const swipeNames = ["Keep", "Archive", "Favorite"];
    for (let i = 0; i < n; i++) {
      const row = el("div", "slot-row");
      row.appendChild(el("span", "slot-key", d.mode === "swipe" ? swipeNames[i] : String(i + 1)));
      const label = el("input", "set-input");
      label.value = d.labels[i];
      label.placeholder = "Label";
      label.maxLength = 60;
      label.addEventListener("input", () => (d.labels[i] = label.value));
      row.appendChild(label);
      if (d.mode === "grid") {
        const folder = el("input", "set-input");
        folder.value = d.folders[i];
        folder.placeholder = "Folder";
        folder.maxLength = 120;
        folder.title = "Folder name inside the preset folder";
        folder.addEventListener("input", () => (d.folders[i] = folder.value));
        row.appendChild(folder);
        row.appendChild(this.palettePicker(i));
      } else {
        const color = el("input", "color-input");
        color.type = "color";
        color.value = d.colors[i].toLowerCase();
        color.addEventListener("input", () => (d.colors[i] = color.value.toUpperCase()));
        row.appendChild(color);
      }
      slots.appendChild(row);
    }
    body.appendChild(slots);
    if (d.mode === "swipe") body.appendChild(el("div", "set-note", "Swipe folders are named after the labels (as in v1)."));

    const btns = el("div", "row-btns left");
    if (d.mode === "grid" && d.id) {
      const reset = el("button", "ghost-btn", "Reset to default genres");
      reset.type = "button";
      reset.addEventListener("click", () => this.resetGrid());
      btns.appendChild(reset);
    }
    const cancel = el("button", "ghost-btn", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => this.leave());
    btns.appendChild(cancel);
    const save = el("button", "primary-btn", d.id ? "Save" : "Create");
    save.type = "button";
    save.addEventListener("click", () => this.save());
    btns.appendChild(save);
    body.appendChild(btns);
    name.focus();
  },

  /** Every slot needs a label, and two slots may not write to the same folder. */
  validate(d) {
    const n = d.mode === "swipe" ? 3 : 9;
    for (let i = 0; i < n; i++) {
      if (!String(d.labels[i] || "").trim()) return `Tile ${d.mode === "swipe" ? ["Keep", "Archive", "Favorite"][i] : i + 1} needs a label`;
    }
    // Swipe folders are named after labels; grid uses the folder field (falls back to the label).
    const folders = [];
    for (let i = 0; i < n; i++) {
      const raw = d.mode === "swipe" ? d.labels[i] : (d.folders[i] || d.labels[i]);
      folders.push(String(raw).trim().replace(/[/\\:]/g, "-").toLowerCase());
    }
    const dup = folders.find((f, i) => folders.indexOf(f) !== i);
    if (dup) return `Two tiles would sort into the same folder (“${dup}”). Give them different ${d.mode === "swipe" ? "labels" : "folders"}.`;
    return null;
  },

  palettePicker(i) {
    const d = this.draft;
    const wrap = el("div", "palette");
    for (const [label, hex] of PALETTE) {
      const b = el("button", "swatch");
      b.type = "button";
      b.title = label;
      b.style.background = hex;
      if (d.colors[i].toUpperCase() === hex) b.classList.add("on");
      b.addEventListener("click", () => {
        d.colors[i] = hex;
        for (const x of wrap.children) x.classList.toggle("on", x === b);
      });
      wrap.appendChild(b);
    }
    return wrap;
  },

  leave() {
    this.draft = null;
    SettingsDrawer.view = "main";
    SettingsDrawer.scrollTo = "presets";
    SettingsDrawer.render();
  },

  async save() {
    const d = this.draft;
    if (!d.name.trim()) { toast("Give the preset a name", { level: "warning" }); return; }
    const problem = this.validate(d);
    if (problem) { toast(problem, { level: "warning", ms: 6000 }); return; }
    const fields = { name: d.name.trim(), labels: d.labels, colors: d.colors, folders: d.folders, dated: !!d.dated };
    try {
      if (d.id) await api("preset_update", { preset_id: d.id, ...fields });
      else {
        const p = await api("preset_create", { mode: d.mode, ...fields });
        await api("preset_select", { preset_id: p.id });
      }
      await refreshState();
      refreshCountsSoon(0);
      toast(d.id ? "Preset saved" : "Preset created");
      this.leave();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async resetGrid() {
    const ok = await modal({
      title: "Reset to default genres?",
      body: "Labels, colors and folders go back to House, Tech House, Deep House, Minimal, Afro House, Melodic, Techno, Garage and Other.",
      buttons: [{ label: "Cancel", value: false }, { label: "Reset", value: true, primary: true }],
    });
    if (!ok) return;
    try {
      const p = await api("preset_reset_grid", { preset_id: this.draft.id });
      await refreshState();
      refreshCountsSoon(0);
      this.draft = null;
      SettingsDrawer.view = { preset: p, mode: "grid" };
      SettingsDrawer.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};

// ------------------------------------------------------------------ guide overlay

const Guide = {
  get isOpen() { return !document.getElementById("guide").hidden; },

  toggle() { this.isOpen ? this.close() : this.show(); },

  close() { document.getElementById("guide").hidden = true; },

  show() {
    const box = document.getElementById("guide-body");
    box.textContent = "";
    const sw = activePreset("swipe");
    const layout = swipeLayout();
    const upArchive = S.settings && S.settings.swipe_archive_hotkey === "up";
    const keyFor = (slot) => layout.find((t) => t.slot === slot).glyph;
    // Three short columns; the rest is discoverable in the app itself (tooltips, Settings, README).
    const discover = !!(S.settings && S.settings.discover_in_topbar);
    const groups = [
      ["Sort", [
        [keyFor(0), sw ? sw.labels[0] : "Keep"],
        [keyFor(1) + (upArchive ? " ↑" : ""), sw ? sw.labels[1] : "Archive"],
        [upArchive ? "drag ↑" : "↑", sw ? sw.labels[2] : "Favorite"],
        ["↓", "Skip"],
        ["1–9", "Grid tile"],
      ], "Or drag the card, or flick with two fingers."],
      ["Listen", [
        ["Space", "Play / pause"],
        ["1–8", "Jump points"],
        ["9", "Outro"],
        ["A  D", "−30 s / +30 s"],
        ["R", "Replay"],
      ]],
      ["General", [
        ["Z", "Undo"],
        ["⇧⌘Z", "Undo all"],
        ["S  G", "Swipe / Grid"],
        ...(discover ? [["L", "Discover"]] : []),
        ["Q", "Queue"],
        ["⌘F", "Fullscreen"],
        ["H", "Shortcuts"],
      ], discover ? "In Discover: ← → next / previous, ↑ heart." : ""],
    ];
    for (const [title, rows, foot] of groups) {
      const g = el("div", "guide-group");
      g.appendChild(el("h3", null, title));
      for (const [k, v] of rows) {
        const r = el("div", "guide-row");
        r.appendChild(el("kbd", null, k));
        r.appendChild(el("span", null, v));
        g.appendChild(r);
      }
      if (foot) g.appendChild(el("p", "guide-note", foot));
      box.appendChild(g);
    }
    const cb = document.getElementById("guide-startup");
    cb.checked = !!(S.settings && S.settings.show_help_guide_on_startup);
    document.getElementById("guide").hidden = false;
  },
};

// ------------------------------------------------------------------ welcome card

/** "Why I built this": once on first launch, and from About. */
const Welcome = {
  get isOpen() { return !document.getElementById("welcome").hidden; },

  show() {
    document.getElementById("welcome").hidden = false;
    document.getElementById("welcome-go").focus();
  },

  close() {
    document.getElementById("welcome").hidden = true;
    if (S.settings && !S.settings.welcome_seen) {
      S.settings.welcome_seen = true;
      api("set_settings", { welcome_seen: true, guide_seen: true }).then((x) => (S.settings = x)).catch(() => {});
    }
  },
};

// ------------------------------------------------------------------ wiring

function setupPanels() {
  for (const b of document.querySelectorAll("[data-close-drawer]")) b.addEventListener("click", () => Drawers.close());
  document.getElementById("queue-list").addEventListener("scroll", () => QueueDrawer.renderList(), { passive: true });
  let searchTimer = 0;
  const search = document.getElementById("queue-search");
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      QueueDrawer.query = search.value.trim();
      document.getElementById("queue-list").scrollTop = 0;
      QueueDrawer.refresh();
    }, 120);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); if (search.value) { search.value = ""; search.dispatchEvent(new Event("input")); } else search.blur(); }
  });
  document.getElementById("guide-close").addEventListener("click", () => Guide.close());
  document.getElementById("welcome-go").addEventListener("click", () => Welcome.close());
  document.getElementById("welcome-support").addEventListener("click", () => openExternal(SUPPORT_URL));
  document.getElementById("guide").addEventListener("click", (e) => { if (e.target.id === "guide") Guide.close(); });
  document.getElementById("guide-startup").addEventListener("change", (e) => {
    api("set_settings", { show_help_guide_on_startup: e.target.checked }).then((s) => (S.settings = s)).catch(() => {});
  });
}
