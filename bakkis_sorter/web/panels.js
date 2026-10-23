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
const START_CHOICES = [[0, "0:00"], [30, "0:30"], [60, "1:00"], [90, "1:30"], [120, "2:00"], [180, "3:00"]];

// Promo / contact. Links open in the default browser via the backend (never inside the app window).
const CONTACT_EMAIL = "bakkis@bakkis.com";
const SUPPORT_URL = null; // Ko-fi link once the account exists; the Support button stays hidden until then
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
    if (name === "queue") QueueDrawer.refresh();
    if (name === "settings") SettingsDrawer.render();
  },

  close() {
    if (!this.open) return;
    document.getElementById(`drawer-${this.open}`).hidden = true;
    document.body.classList.remove("drawer-open");
    this.open = null;
  },

  /** Called by app.js after every session snapshot. */
  onSession() {
    if (this.open === "queue") QueueDrawer.refreshSoon();
  },
};

// ------------------------------------------------------------------ queue drawer

const QueueDrawer = {
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
    await this.fetchPage(0);
    this.renderList();
  },

  /** Resolves true only when a page was actually loaded (callers re-render on that). */
  async fetchPage(n) {
    if (this.pages.has(n) || this.inflight.has(n)) return false;
    this.inflight.add(n);
    try {
      const res = await api("queue", { offset: n * QUEUE_PAGE, limit: QUEUE_PAGE });
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
    if (!this.total) { inner.appendChild(el("div", "q-empty", "No more tracks")); return; }
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
      row.appendChild(el("span", "q-idx", String(i + 1)));
      const txt = el("span", "q-text");
      txt.appendChild(el("span", "q-name", t.name.replace(/\.[^.]+$/, "")));
      if (t.folder) txt.appendChild(el("span", "q-folder", t.folder));
      row.appendChild(txt);
      if (i === 0) row.appendChild(el("span", "q-state", "next"));
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

// ------------------------------------------------------------------ settings drawer

const SETTINGS_SCHEMA = [
  { section: "Library" },
  { key: "default_folder_path", type: "folder", label: "Default folder" },
  { key: "auto_load_default_folder", type: "bool", label: "Load the default folder on startup" },
  { key: "include_subfolders", type: "bool", label: "Include subfolders", note: "Applies the next time a folder is loaded." },
  { key: "track_order", type: "select", label: "Track order", options: [["random", "Random"], ["in_order", "In order"]] },
  { section: "Sorting" },
  {
    key: "file_operation", type: "select", label: "When sorting", options: [["move", "Move files"], ["copy", "Copy files"]],
    note: "Moving breaks Rekordbox / Serato library links. Copying keeps the originals in place.",
  },
  { key: "hide_copied_on_rescan", type: "bool", label: "Hide tracks I already copied", note: "Copy mode only. Applies when a folder is loaded." },
  { key: "create_dated_sorting_sessions", type: "bool", label: "Create dated session folders", note: "BAKKIS_SORTED/<preset>/YYYY-MM-DD/<folder>" },
  { key: "cleanup_empty_source_folders_after_sort", type: "bool", label: "Remove source folders that become empty", note: "Move mode only. Undo recreates them." },
  { key: "swipe_keep_direction", type: "select", label: "Keep is", options: [["right", "→ Right (Archive ←)"], ["left", "← Left (Archive →)"]] },
  { key: "swipe_archive_hotkey", type: "select", label: "↑ arrow sorts to", options: [["down", "Favorite"], ["up", "Archive"]] },
  { key: "end_of_track", type: "select", label: "When a track ends", options: [["skip", "Skip it and play the next"], ["stop", "Stop"]] },
  { key: "sort_confirmation_sound", type: "bool", label: "Click sound on sort" },
  { section: "Playback" },
  { key: "playback_start_seconds", type: "select", label: "Start tracks at", options: START_CHOICES, number: true, note: "Tracks shorter than start + 30 s play from 0:00." },
  { section: "Startup" },
  { key: "default_mode_on_startup", type: "select", label: "Mode on startup", options: [["swipe", "Swipe"], ["grid", "Grid"]] },
  { key: "startup_preset_id", type: "startup_preset", label: "Preset on startup" },
  { key: "show_help_guide_on_startup", type: "bool", label: "Show the shortcut guide on startup" },
  { key: "show_queue_panel_on_startup", type: "bool", label: "Open the queue on startup" },
  { section: "Presets" },
  { type: "presets" },
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
    for (const item of SETTINGS_SCHEMA) {
      if (item.section) { body.appendChild(el("h3", "set-section", item.section)); continue; }
      if (item.type === "presets") { this.renderPresets(body); continue; }
      body.appendChild(this.renderItem(item));
    }
    this.renderAbout(body);
    if (this.scrollTo) {
      const target = body.querySelector(`[data-anchor="${this.scrollTo}"]`);
      if (target) target.scrollIntoView();
      this.scrollTo = null;
    }
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
        this.save(changes);
      });
      row.appendChild(sel);
    } else if (item.type === "folder") {
      row.appendChild(el("div", "set-label", item.label));
      const path = s.default_folder_path;
      const val = el("div", "set-path", path || "None");
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
      mk("Use current folder", () => this.save({ default_folder_path: S.session.root }).then(refreshState), !S.session);
      mk("Choose…", async () => { const p = await pickFolderPath(); if (p) { await this.save({ default_folder_path: p }); await refreshState(); this.render(); } });
      mk("Clear", () => this.save({ default_folder_path: null }).then(() => this.render()), !path);
      if (path && S.defaultFolderExists) mk("Load now", () => { Drawers.close(); loadFolder(path); });
      row.appendChild(btns);
    }
    if (item.note) row.appendChild(el("div", "set-note", item.note));
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
    this.save({ [item.key]: cb.checked });
  },

  renderAbout(body) {
    body.appendChild(el("h3", "set-section", "About"));
    const box = el("div", "about");
    const logo = el("span", "logo logo-boxed about-logo");
    logo.setAttribute("role", "img");
    logo.setAttribute("aria-label", "BAKKIS");
    box.appendChild(logo);
    box.appendChild(el("div", "muted small", `BAKKIS MUSIC SORTER ${S.version || ""} · Free and open source (MIT)`));
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
    nameRow.appendChild(el("div", "set-note", `Output: BAKKIS_SORTED/${d.name || "…"}/`));
    name.addEventListener("input", () => (nameRow.lastChild.textContent = `Output: BAKKIS_SORTED/${d.name || "…"}/`));
    body.appendChild(nameRow);

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
    const fields = { name: d.name.trim(), labels: d.labels, colors: d.colors, folders: d.folders };
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
    const groups = [
      ["Swipe mode", [
        [keyFor(0), sw ? sw.labels[0] : "Keep"],
        [keyFor(1) + (upArchive ? "  ↑" : ""), sw ? sw.labels[1] : "Archive"],
        [upArchive ? "drag ↑" : "↑", sw ? sw.labels[2] : "Favorite"],
        ["↓", "Skip"],
        ["1 – 8", "Jump to 0:00 0:30 1:00 1:30 2:00 3:00 4:00 5:00"],
        ["9", "Outro (last 60 s)"],
        ["Drag card", "Sort in that direction"],
        ["Two-finger flick", "← / → on the trackpad"],
      ]],
      ["Grid mode", [["1 – 9", "Sort to that tile"], ["↓", "Skip"]]],
      ["Playback", [["Space", "Play / pause"], ["A  /  D", "−30 s / +30 s"], ["R", "Replay from the jump point"], ["Click waveform", "Seek"]]],
      ["Session", [["Z  or  ⌘Z", "Undo last"], ["⇧⌘Z  or  ⌃Z", "Undo all (asks first)"], ["U  /  O", "Random order / in order"], ["S  /  G", "Swipe / Grid mode"]]],
      ["Window", [["Q", "Queue"], ["H  or  ?", "This guide"], ["Tab", "Focus mode"], ["⌘F", "Fullscreen"], ["Esc", "Close / exit fullscreen"]]],
    ];
    for (const [title, rows] of groups) {
      const g = el("div", "guide-group");
      g.appendChild(el("h3", null, title));
      for (const [k, v] of rows) {
        const r = el("div", "guide-row");
        r.appendChild(el("kbd", null, k));
        r.appendChild(el("span", null, v));
        g.appendChild(r);
      }
      box.appendChild(g);
    }
    const cb = document.getElementById("guide-startup");
    cb.checked = !!(S.settings && S.settings.show_help_guide_on_startup);
    document.getElementById("guide").hidden = false;
  },
};

// ------------------------------------------------------------------ wiring

function setupPanels() {
  for (const b of document.querySelectorAll("[data-close-drawer]")) b.addEventListener("click", () => Drawers.close());
  document.getElementById("queue-list").addEventListener("scroll", () => QueueDrawer.renderList(), { passive: true });
  document.getElementById("guide-close").addEventListener("click", () => Guide.close());
  document.getElementById("guide").addEventListener("click", (e) => { if (e.target.id === "guide") Guide.close(); });
  document.getElementById("guide-startup").addEventListener("change", (e) => {
    api("set_settings", { show_help_guide_on_startup: e.target.checked }).then((s) => (S.settings = s)).catch(() => {});
  });
}
