/* BAKKIS MUSIC SORTER v2 — Preset Editor Drawer */
"use strict";

import { PALETTE } from "./common.js";

export const PresetEditor = {
  draft: null,

  start(view) {
    const { preset, mode } = view;
    const S = window.S;
    const activePreset = window.activePreset;
    if (preset) {
      this.draft = JSON.parse(JSON.stringify(preset));
    } else {
      const base = activePreset ? activePreset(mode) : { labels: [], colors: [], folders: [] };
      this.draft = {
        id: null,
        mode,
        name: mode === "swipe" ? "New swipe preset" : "New grid preset",
        labels: [...base.labels],
        colors: [...base.colors],
        folders: [...base.folders],
        dated: !!(S && S.settings && S.settings.create_dated_sorting_sessions),
      };
    }
  },

  render(body, view) {
    const el = window.el;
    const S = window.S;
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
    const tree = S && S.settings && S.settings.file_operation === "copy" ? "BAKKIS_SORTED_COPIES" : "BAKKIS_SORTED";
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

  validate(d) {
    const n = d.mode === "swipe" ? 3 : 9;
    for (let i = 0; i < n; i++) {
      if (!String(d.labels[i] || "").trim()) return `Tile ${d.mode === "swipe" ? ["Keep", "Archive", "Favorite"][i] : i + 1} needs a label`;
    }
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
    const el = window.el;
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
    const SettingsDrawer = window.SettingsDrawer;
    if (SettingsDrawer) {
      SettingsDrawer.view = "main";
      SettingsDrawer.scrollTo = "presets";
      SettingsDrawer.render();
    }
  },

  async save() {
    const api = window.api;
    const toast = window.toast;
    const refreshState = window.refreshState;
    const refreshCountsSoon = window.refreshCountsSoon;
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
      if (refreshState) await refreshState();
      if (refreshCountsSoon) refreshCountsSoon(0);
      toast(d.id ? "Preset saved" : "Preset created");
      this.leave();
    } catch (e) { toast(e.message, { level: "error" }); }
  },

  async resetGrid() {
    const modal = window.modal;
    const api = window.api;
    const toast = window.toast;
    const refreshState = window.refreshState;
    const refreshCountsSoon = window.refreshCountsSoon;
    const SettingsDrawer = window.SettingsDrawer;

    const ok = await modal({
      title: "Reset to default genres?",
      body: "Labels, colors and folders go back to House, Tech House, Deep House, Minimal, Afro House, Melodic, Techno, Garage and Other.",
      buttons: [{ label: "Cancel", value: false }, { label: "Reset", value: true, primary: true }],
    });
    if (!ok) return;
    try {
      const p = await api("preset_reset_grid", { preset_id: this.draft.id });
      if (refreshState) await refreshState();
      if (refreshCountsSoon) refreshCountsSoon(0);
      this.draft = null;
      if (SettingsDrawer) {
        SettingsDrawer.view = { preset: p, mode: "grid" };
        SettingsDrawer.render();
      }
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};
