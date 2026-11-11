/* BAKKIS MUSIC SORTER v2 — Settings Drawer */
"use strict";

import {
  openExternal,
  reportProblem,
  FOLLOW_LINKS,
  CONTACT_EMAIL,
  SUPPORT_URL,
  START_CHOICES,
} from "./common.js";
import { PresetEditor } from "./presets.js";
import { describeFolder } from "../cards.js";
import { pickFolderPath, loadFolder, exportPlaylists, installUpdate } from "../folder.js";
import { setMode } from "../decisions.js";
import { CommentsModal } from "../comments.js";

export const isCopy = () => window.S && window.S.settings && window.S.settings.file_operation === "copy";

export const SETTINGS_SCHEMA = [
  { section: "Music folder" },
  { key: "default_folder_path", type: "folder", label: "Default folder" },
  { key: "auto_load_default_folder", type: "bool", label: "Load it on startup", show: () => !!(window.S && window.S.settings && window.S.settings.default_folder_path) },
  { key: "include_subfolders", type: "bool", label: "Include subfolders" },
  { key: "track_order", type: "select", label: "Track order", options: [["random", "Random"], ["in_order", "In order"]] },
  { section: "Sorting" },
  {
    key: "file_operation", type: "select", label: "Files", options: [["move", "Move tracks"], ["copy", "Copy tracks"]],
    note: () => isCopy()
      ? "Copies go to BAKKIS_SORTED_COPIES; your originals stay where they are."
      : "Tracks move into BAKKIS_SORTED. Rekordbox and Serato lose track of moved files; choose Copy if that matters.",
  },
  { key: "cleanup_empty_source_folders_after_sort", type: "bool", label: "Remove folders that become empty", note: "Undo recreates them.", show: () => !isCopy(), cls: "set-sub-option" },
  {
    key: "swipe_keep_direction", type: "select", label: "Swipe direction",
    options: [["right", "Keep → right, Archive ← left"], ["left", "Keep ← left, Archive → right"]],
  },
  { section: "Playback" },
  { key: "playback_start_seconds", type: "select", label: "Start tracks at", options: START_CHOICES, number: true, note: "Short tracks start about a third of the way in." },
  { key: "end_of_track", type: "select", label: "When a track ends", options: [["skip", "Skip it and play the next"], ["stop", "Stop"]] },
  { section: "Presets" },
  { type: "presets" },
  { section: "Music Promos (Inflyte & Trackstack)" },
  { type: "promo_settings" },
  { advanced: true },
  { sub: "Files" },
  { key: "hide_copied_on_rescan", type: "bool", label: "Hide tracks I already copied", note: "When the folder is loaded again.", show: isCopy },
  { key: "create_dated_sorting_sessions", type: "bool", label: "Date folders for new presets", note: "A folder per day: <preset>/YYYY-MM-DD/. Each preset has its own switch." },
  { sub: "Controls" },
  { key: "swipe_archive_hotkey", type: "select", label: "Up arrow key", options: [["down", "Favorite"], ["up", "Archive (drag the card up for Favorite)"]] },
  { key: "game_controller", type: "bool", label: "Game controller", note: "PlayStation 4/5, Xbox One/Series, Switch Pro and most 8BitDo controllers. Connect it (USB or Bluetooth), press any button, and it sorts like the arrow keys and rumbles on every sort. Buttons: press H." },
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
  { sub: "Diagnostics & Storage" },
  { type: "diagnostics" },
];

export const SettingsDrawer = {
  view: "main",
  scrollTo: null,

  async save(changes) {
    const api = window.api;
    const toast = window.toast;
    const renderAll = window.renderAll;
    const S = window.S;
    try {
      if (S) S.settings = await api("set_settings", changes);
      if (renderAll) renderAll();
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
      if (item.section) { box.appendChild(window.el("h3", "set-section", item.section)); continue; }
      if (item.sub) { box.appendChild(window.el("h4", "set-sub", item.sub)); continue; }
      if (item.advanced) { box = this.advancedBox(body); continue; }
      if (item.type === "presets") { this.renderPresets(box); continue; }
      if (item.type === "promo_settings") { this.renderPromoSettings(box); continue; }
      if (item.type === "export") { box.appendChild(this.exportRow()); continue; }
      if (item.type === "reset") { box.appendChild(this.resetRow()); continue; }
      if (item.type === "diagnostics") { this.renderDiagnostics(box); continue; }
      box.appendChild(this.renderItem(item));
    }
    this.renderAbout(body);
    if (this.scrollTo) {
      const target = body.querySelector(`[data-anchor="${this.scrollTo}"]`);
      if (target) target.scrollIntoView();
      this.scrollTo = null;
    }
  },

  advancedBox(body) {
    const el = window.el;
    const d = el("details", "set-advanced");
    try { d.open = localStorage.getItem("bakkis.settings.advanced") === "1"; } catch (_) {}
    d.addEventListener("toggle", () => { try { localStorage.setItem("bakkis.settings.advanced", d.open ? "1" : "0"); } catch (_) {} });
    d.appendChild(el("summary", "set-section", "Advanced"));
    body.appendChild(d);
    return d;
  },

  exportRow() {
    const el = window.el;
    const S = window.S;
    const row = el("div", "set-row");
    const b = el("button", "ghost-btn small-btn", "Export playlists (M3U8)");
    b.type = "button";
    b.disabled = !(S && S.session);
    b.addEventListener("click", exportPlaylists);
    row.appendChild(b);
    row.appendChild(el("div", "set-note", "One playlist per sorted folder, for Rekordbox (File → Import → Playlist) or Serato (drag onto the crate list)."));
    return row;
  },

  resetRow() {
    const el = window.el;
    const modal = window.modal;
    const api = window.api;
    const toast = window.toast;
    const S = window.S;
    const renderAll = window.renderAll;

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
        if (S) S.settings = await api("reset_settings");
        if (renderAll) renderAll();
        this.render();
        toast("Settings reset");
      } catch (e) { toast(e.message, { level: "error" }); }
    });
    row.appendChild(b);
    return row;
  },

  renderDiagnostics(box) {
    const el = window.el;
    const api = window.api;
    const toast = window.toast;
    const modal = window.modal;

    const wrap = el("div", "set-diagnostics");
    wrap.dataset.anchor = "diagnostics";

    const statusRow = el("div", "diag-status-row");
    const dbSizeBadge = el("span", "diag-badge", "Database: calculating…");
    const reclaimBadge = el("span", "diag-badge muted", "");
    statusRow.appendChild(dbSizeBadge);
    statusRow.appendChild(reclaimBadge);
    wrap.appendChild(statusRow);

    const countsRow = el("div", "diag-counts-row small muted");
    wrap.appendChild(countsRow);

    const btnRow = el("div", "diag-btns");
    const vacBtn = el("button", "ghost-btn small-btn", "⚡ Optimize & Vacuum");
    vacBtn.type = "button";
    const copyBtn = el("button", "ghost-btn small-btn", "📋 Copy Diagnostic Bundle");
    copyBtn.type = "button";
    const clearBtn = el("button", "link-btn danger small-btn", "Clear Logs");
    clearBtn.type = "button";
    btnRow.appendChild(vacBtn);
    btnRow.appendChild(copyBtn);
    btnRow.appendChild(clearBtn);
    wrap.appendChild(btnRow);

    const filterRow = el("div", "diag-filter-row");
    let currentFilter = "ALL";
    const filterBtns = {};
    for (const flt of ["ALL", "ERROR", "WARNING", "INFO"]) {
      const label = flt === "ALL" ? "All" : (flt === "ERROR" ? "Errors" : (flt === "WARNING" ? "Warnings" : "Info"));
      const b = el("button", `diag-filter-btn ${flt === "ALL" ? "active" : ""}`, label);
      b.type = "button";
      filterBtns[flt] = b;
      b.addEventListener("click", () => {
        currentFilter = flt;
        Object.values(filterBtns).forEach((btn) => btn.classList.remove("active"));
        b.classList.add("active");
        renderLogs();
      });
      filterRow.appendChild(b);
    }
    wrap.appendChild(filterRow);

    const logBox = el("div", "diag-log-box");
    wrap.appendChild(logBox);

    box.appendChild(wrap);

    let cachedLogs = [];

    const updateStats = async () => {
      try {
        const stats = await api("get_db_stats");
        const totalMb = (stats.total_size_bytes / (1024 * 1024)).toFixed(2);
        const reclaimKb = (stats.reclaimable_bytes / 1024).toFixed(1);
        dbSizeBadge.textContent = `Database: ${totalMb} MB`;
        reclaimBadge.textContent = stats.reclaimable_bytes > 0 ? `(${reclaimKb} KB reclaimable)` : "(compacted)";
        if (stats.counts) {
          countsRow.textContent = `Logs: ${stats.counts.app_logs || 0} · Peaks: ${stats.counts.waveform_peaks || 0} · Reviews: ${stats.counts.promo_reviews || 0} · History: ${stats.counts.session_history || 0}`;
        }
      } catch (_) {
        dbSizeBadge.textContent = "Database: bakkis.db";
      }
    };

    const renderLogs = () => {
      logBox.textContent = "";
      const filtered = cachedLogs.filter((l) => {
        if (currentFilter === "ALL") return true;
        return l.level === currentFilter;
      });
      if (filtered.length === 0) {
        logBox.appendChild(el("div", "diag-empty muted small", "No logs recorded matching this filter."));
        return;
      }
      for (const log of filtered) {
        const item = el("div", `diag-log-item log-${(log.level || "info").toLowerCase()}`);
        const header = el("div", "diag-log-header");
        const timeStr = log.created_at ? new Date(log.created_at * 1000).toLocaleTimeString() : "";
        header.appendChild(el("span", "diag-log-time", timeStr));
        header.appendChild(el("span", `diag-log-pill pill-${(log.level || "info").toLowerCase()}`, log.level));
        header.appendChild(el("span", "diag-log-mod", log.module));
        item.appendChild(header);

        const msg = el("div", "diag-log-msg", log.message);
        item.appendChild(msg);

        if (log.traceback) {
          const details = el("details", "diag-log-tb");
          details.appendChild(el("summary", "small muted", `${log.exception_type || "Exception"} details`));
          const pre = el("pre", null, log.traceback.trim());
          details.appendChild(pre);
          item.appendChild(details);
        }
        logBox.appendChild(item);
      }
    };

    const refreshLogs = async () => {
      try {
        cachedLogs = (await api("get_logs", { limit: 200, min_level: "ALL" })) || [];
        renderLogs();
      } catch (_) {}
    };

    vacBtn.addEventListener("click", async () => {
      vacBtn.disabled = true;
      try {
        const res = await api("vacuum_db");
        const reclaimedKb = (res.reclaimed_bytes / 1024).toFixed(1);
        toast(`Database optimized · Reclaimed ${reclaimedKb} KB`);
        await updateStats();
      } catch (err) {
        toast(`Vacuum failed: ${err.message}`, { level: "error" });
      } finally {
        vacBtn.disabled = false;
      }
    });

    copyBtn.addEventListener("click", async () => {
      try {
        await api("get_diagnostic_bundle");
        toast("Diagnostic bundle copied to clipboard");
      } catch (err) {
        toast(`Could not copy diagnostic bundle: ${err.message}`, { level: "error" });
      }
    });

    clearBtn.addEventListener("click", async () => {
      const ok = await modal({
        title: "Clear all system logs?",
        body: "This purges recent log entries from bakkis.db. Database statistics, review history, and peaks will not be affected.",
        buttons: [{ label: "Cancel", value: false }, { label: "Clear Logs", value: true, danger: true }],
      });
      if (!ok) return;
      try {
        await api("clear_logs");
        toast("Logs cleared");
        await refreshLogs();
        await updateStats();
      } catch (err) {
        toast(`Failed to clear logs: ${err.message}`, { level: "error" });
      }
    });

    updateStats();
    refreshLogs();
  },

  renderPromoSettings(box) {
    const el = window.el;
    const S = window.S;
    const s = S ? S.settings : {};
    const api = window.api;

    const wrap = el("div", "set-promo-group");
    wrap.dataset.anchor = "promos";

    // 1. Promo Download Folder
    const dlRow = el("div", "set-row");
    dlRow.appendChild(el("div", "set-label", "Promo download folder"));
    const dlPath = (s && s.promo_download_dir) || "~/Downloads";
    const pathDiv = el("div", "set-path");
    pathDiv.textContent = dlPath;
    pathDiv.title = dlPath;
    dlRow.appendChild(pathDiv);

    const dlBtns = el("div", "set-btns");
    const chooseBtn = el("button", "ghost-btn small-btn", "Choose…");
    chooseBtn.type = "button";
    chooseBtn.addEventListener("click", async () => {
      try {
        const folder = await api("choose_folder");
        if (folder) {
          await this.save({ promo_download_dir: folder });
          this.render();
        }
      } catch (err) {
        window.toast("Folder chooser failed: " + err.message, { level: "error" });
      }
    });
    dlBtns.appendChild(chooseBtn);

    if (dlPath !== "~/Downloads") {
      const resetBtn = el("button", "ghost-btn small-btn", "Reset to ~/Downloads");
      resetBtn.type = "button";
      resetBtn.addEventListener("click", async () => {
        await this.save({ promo_download_dir: "~/Downloads" });
        this.render();
      });
      dlBtns.appendChild(resetBtn);
    }
    dlRow.appendChild(dlBtns);
    dlRow.appendChild(el("div", "set-note", "Where full-resolution downloads from Trackstack are saved. Existing files are never moved."));
    wrap.appendChild(dlRow);

    // 2. Comments & Ratings
    const commRow = el("div", "set-row");
    commRow.appendChild(el("div", "set-label", "Feedback comments & ratings"));
    const commBtns = el("div", "set-btns");
    const editCommBtn = el("button", "ghost-btn small-btn", "💬 Configure Comments & Ratings…");
    editCommBtn.type = "button";
    editCommBtn.id = "btn-settings-comments";
    editCommBtn.addEventListener("click", () => {
      CommentsModal.open();
    });
    commBtns.appendChild(editCommBtn);
    commRow.appendChild(commBtns);
    commRow.appendChild(el("div", "set-note", "Customize star ratings (5★ Feeling this, 4★ Keep / Maybe) and randomized comment pools."));
    wrap.appendChild(commRow);

    // 3. Review Previous Feedback
    const histRow = el("div", "set-row");
    histRow.appendChild(el("div", "set-label", "Review previous feedback"));
    const histBtns = el("div", "set-btns");
    const histBtn = el("button", "ghost-btn small-btn", "📜 Review Previous Feedback…");
    histBtn.type = "button";
    histBtn.id = "btn-settings-promo-history";
    histBtn.addEventListener("click", () => {
      if (window.Drawers) window.Drawers.show("promo-history");
    });
    histBtns.appendChild(histBtn);
    histRow.appendChild(histBtns);
    histRow.appendChild(el("div", "set-note", "Browse past promo reviews, track signatures, delivery status, and submitted feedback."));
    wrap.appendChild(histRow);

    // 4. Duplicate Campaign Shield
    const retItem = {
      key: "promo_duplicate_retention_days",
      type: "select",
      label: "Duplicate campaign shield",
      options: [[90, "Keep for 90 days"], [180, "Keep for 180 days (recommended)"], [365, "Keep for 1 year"], [0, "Keep forever"]],
      number: true,
      note: "Flags previously rated or dismissed tracks if re-sent under a new promo campaign.",
    };
    wrap.appendChild(this.renderItem(retItem));

    box.appendChild(wrap);
  },

  renderItem(item) {
    const el = window.el;
    const S = window.S;
    const s = S ? S.settings : {};
    const row = el("div", item.cls ? `set-row ${item.cls}` : "set-row");
    const id = `set-${item.key}`;
    if (item.type === "bool") {
      const lab = el("label", "set-check");
      const cb = el("input");
      cb.type = "checkbox";
      cb.id = id;
      cb.checked = !!(s && s[item.key]);
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
        opts = [["", "Last used"]].concat(S && S.presets ? S.presets.presets.map((p) => [p.id, `${p.mode === "swipe" ? "Swipe" : "Grid"} · ${p.name}`]) : []);
      }
      for (const [v, label] of opts) {
        const o = el("option", null, label);
        o.value = String(v);
        if (String(s && s[item.key] == null ? "" : s[item.key]) === String(v)) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", () => {
        let v = sel.value;
        if (item.number) v = Number(v);
        if (item.type === "startup_preset" && !v) v = null;
        const changes = { [item.key]: v };
        if (item.key === "file_operation" && v === "move") changes.move_warning_acknowledged = true;
        this.save(changes).then(() => { if (item.key === "file_operation") this.render(); });
      });
      row.appendChild(sel);
    } else if (item.type === "folder") {
      row.appendChild(el("div", "set-label", item.label));
      const path = s ? s.default_folder_path : null;
      const val = el("div", "set-path");
      if (path) {
        const f = describeFolder(path);
        val.appendChild(el("span", "set-path-name", f.name));
        val.appendChild(el("span", null, ` · ${f.where}`));
      } else {
        val.textContent = "None";
      }
      val.title = path || "";
      if (path && S && !S.defaultFolderExists) val.classList.add("warn");
      row.appendChild(val);
      const btns = el("div", "set-btns");
      const mk = (label, fn, disabled) => {
        const b = el("button", "ghost-btn small-btn", label);
        b.type = "button";
        b.disabled = !!disabled;
        b.addEventListener("click", fn);
        btns.appendChild(b);
      };
      mk("Use current folder", () => this.save({ default_folder_path: S.session.root }).then(window.refreshState).then(() => this.render()), !(S && S.session));
      mk("Choose…", async () => { const p = await pickFolderPath(); if (p) { await this.save({ default_folder_path: p }); if (window.refreshState) await window.refreshState(); this.render(); } });
      mk("Clear", () => this.save({ default_folder_path: null }).then(() => this.render()), !path);
      if (path && S && S.defaultFolderExists) mk("Load now", () => { if (window.Drawers) window.Drawers.close(); loadFolder(path); });
      row.appendChild(btns);
    }
    const note = typeof item.note === "function" ? item.note() : item.note;
    if (note) row.appendChild(el("div", "set-note", note));
    return row;
  },

  async onBool(item, cb) {
    const modal = window.modal;
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
    const el = window.el;
    const S = window.S;
    const Drawers = window.Drawers;
    const Welcome = window.Welcome;

    body.appendChild(el("h3", "set-section", "About"));
    const box = el("div", "about");
    const logo = el("span", "logo logo-boxed about-logo");
    logo.setAttribute("role", "img");
    logo.setAttribute("aria-label", "BAKKIS");
    box.appendChild(logo);
    box.appendChild(el("div", "muted small", `BAKKIS MUSIC SORTER ${S ? S.version || "" : ""} · Free and open source (MIT)`));
    if (S && S.update && S.update.newer) {
      const up = el("button", "link-btn", `Version ${S.update.latest} is available: ${S.update.can_install ? "update now" : "download"}`);
      up.type = "button";
      up.addEventListener("click", () => (S.update.can_install ? installUpdate() : openExternal(S.update.url)));
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
    mk("Why I built this", () => { if (Drawers) Drawers.close(); if (Welcome) Welcome.show(); });
    mk("Report a problem", reportProblem);
    mk(`Contact: ${CONTACT_EMAIL}`, () => openExternal(`mailto:${CONTACT_EMAIL}`));
    box.appendChild(btns);
    body.appendChild(box);
  },

  renderPresets(body) {
    const el = window.el;
    const S = window.S;
    const wrap = el("div", "set-presets");
    wrap.dataset.anchor = "presets";
    for (const mode of ["swipe", "grid"]) {
      wrap.appendChild(el("div", "set-label", mode === "swipe" ? "Swipe presets" : "Grid presets"));
      const activeId = S && S.presets ? S.presets[`active_${mode}_preset_id`] : null;
      const list = S && S.presets ? S.presets.presets.filter((p) => p.mode === mode) : [];
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
    const api = window.api;
    const toast = window.toast;
    const refreshState = window.refreshState;
    const refreshCountsSoon = window.refreshCountsSoon;
    const S = window.S;
    try {
      await api("preset_select", { preset_id: p.id });
      if (refreshState) await refreshState();
      if (p.mode !== (S ? S.mode : null)) setMode(p.mode);
      if (refreshCountsSoon) refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async deletePreset(p) {
    const modal = window.modal;
    const api = window.api;
    const toast = window.toast;
    const refreshState = window.refreshState;
    const refreshCountsSoon = window.refreshCountsSoon;
    const ok = await modal({
      title: `Delete “${p.name}”?`,
      body: "The preset is removed. Folders already created in BAKKIS_SORTED stay where they are.",
      buttons: [{ label: "Cancel", value: false }, { label: "Delete", value: true, danger: true }],
    });
    if (!ok) return;
    try {
      await api("preset_delete", { preset_id: p.id });
      if (refreshState) await refreshState();
      if (refreshCountsSoon) refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};
