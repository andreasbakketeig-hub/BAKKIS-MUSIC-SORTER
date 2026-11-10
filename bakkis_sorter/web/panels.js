/* BAKKIS MUSIC SORTER v2 — Drawers and Overlays Coordinator */
"use strict";

import {
  QUEUE_ROW_H,
  QUEUE_PAGE,
  PALETTE,
  START_CHOICES,
  CONTACT_EMAIL,
  SUPPORT_URL,
  FOLLOW_LINKS,
  openExternal,
  reportProblem,
} from "./drawers/common.js";
import { HistoryDrawer } from "./drawers/history.js";
import { SettingsDrawer, SETTINGS_SCHEMA, isCopy } from "./drawers/settings.js";
import { PresetEditor } from "./drawers/presets.js";
import { Guide, Welcome } from "./drawers/modals.js";
import { PromoHistoryDrawer } from "./drawers/promo_history.js";

export const Drawers = {
  open: null,

  toggle(name) { this.open === name ? this.close() : this.show(name); },

  show(name) {
    this.close();
    this.open = name;
    const el = document.getElementById(`drawer-${name}`);
    if (el) el.hidden = false;
    document.body.classList.add("drawer-open");
    document.body.classList.toggle("drawer-wide-open", !!(el && el.classList.contains("drawer-wide")));
    if (name === "queue" && window.QueueDrawer) window.QueueDrawer.refresh();
    if (name === "settings") SettingsDrawer.render();
    if (name === "history") HistoryDrawer.render();
    if (name === "promo-history") PromoHistoryDrawer.render();
    if (name === "hearts" && window.HeartsDrawer) window.HeartsDrawer.render();
  },

  close() {
    if (!this.open) return;
    const el = document.getElementById(`drawer-${this.open}`);
    if (el) el.hidden = true;
    document.body.classList.remove("drawer-open", "drawer-wide-open");
    this.open = null;
  },

  /** Called by app.js after every session snapshot. */
  onSession() {
    if (this.open === "queue" && window.QueueDrawer) window.QueueDrawer.refreshSoon();
  },
};

export function setupPanels() {
  for (const b of document.querySelectorAll("[data-close-drawer]")) {
    b.addEventListener("click", () => Drawers.close());
  }
  const guideClose = document.getElementById("guide-close");
  if (guideClose) guideClose.addEventListener("click", () => Guide.close());

  const welcomeGo = document.getElementById("welcome-go");
  if (welcomeGo) welcomeGo.addEventListener("click", () => Welcome.close());

  const welcomeSupport = document.getElementById("welcome-support");
  if (welcomeSupport) welcomeSupport.addEventListener("click", () => openExternal(SUPPORT_URL));

  const guide = document.getElementById("guide");
  if (guide) {
    guide.addEventListener("click", (e) => {
      if (e.target.id === "guide") Guide.close();
    });
  }

  const guideStartup = document.getElementById("guide-startup");
  if (guideStartup) {
    guideStartup.addEventListener("change", (e) => {
      const api = window.api;
      const S = window.S;
      if (api) {
        api("set_settings", { show_help_guide_on_startup: e.target.checked })
          .then((s) => { if (S) S.settings = s; })
          .catch(() => {});
      }
    });
  }
}

// Global attachments for backward compatibility and test suite inspections
window.Drawers = Drawers;
window.SettingsDrawer = SettingsDrawer;
window.HistoryDrawer = HistoryDrawer;
window.PromoHistoryDrawer = PromoHistoryDrawer;
window.PresetEditor = PresetEditor;
window.Guide = Guide;
window.Welcome = Welcome;
window.openExternal = openExternal;
window.reportProblem = reportProblem;
window.setupPanels = setupPanels;

export {
  QUEUE_ROW_H,
  QUEUE_PAGE,
  PALETTE,
  START_CHOICES,
  CONTACT_EMAIL,
  SUPPORT_URL,
  FOLLOW_LINKS,
  openExternal,
  reportProblem,
  HistoryDrawer,
  SettingsDrawer,
  SETTINGS_SCHEMA,
  isCopy,
  PresetEditor,
  Guide,
  Welcome,
  PromoHistoryDrawer,
};
