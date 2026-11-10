/* BAKKIS MUSIC SORTER — Sorting decisions, skip, undo, and optimistic queue mutations. */
"use strict";

import { api } from "./api.js";
import { bus } from "./bus.js";
import { cardEnter, flyDirection, flyOutCard } from "./cards.js";
import { Player, clickSound, warmUpAudio } from "./player.js";
import { S, activePreset, trackDisplay } from "./state.js";
import { clearUndoToasts, modal, modalOpen, toast } from "./toasts.js";
import { Wave } from "./wave.js";

const MAX_AUTO_SKIPS = 3;
const COACH_SORTS = 3;
const SUPPORT_URL = "https://buymeacoffee.com/bakkis";

const $ = (id) => document.getElementById(id);

export function pressTile(slot) {
  const box = S.mode === "grid" ? $("tiles-grid") : $("tiles-swipe");
  if (!box) return;
  const t = box.querySelector(`.tile[data-slot="${slot}"]`);
  if (!t) return;
  t.classList.add("pressed");
  setTimeout(() => t.classList.remove("pressed"), 120);
}

export function finishCoach() {
  if (!S.settings || S.settings.coach_done) return;
  S.settings.coach_done = true;
  const c = $("coach");
  if (c) c.hidden = true;
  api("set_settings", { coach_done: true })
    .then((x) => (S.settings = x))
    .catch(() => {});
}

export async function ensureMoveAcknowledged() {
  if (S.session && S.session.is_inflyte) return true;
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

export function cantPlay(track, name) {
  const m = S.meta.get(track.id);
  return m && m.size === 0 ? `“${name}” is an empty file (0 bytes)` : `Can't play “${name}”`;
}

export async function decide({ kind, slot, auto }) {
  if ((window.Listen && window.Listen.active) || modalOpen()) return;
  const sess = S.session;
  if (S.busy || !sess || !sess.current) return;
  const cur = sess.current;
  const s = S.settings;
  if (auto && s && s.file_operation === "move" && !s.move_warning_acknowledged) {
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    toast(
      auto === "error" ? `${cantPlay(cur, name)}. Press ↓ to skip it.` : "Track ended. Press ↓ to skip it.",
      { level: "warning", ms: 6000 }
    );
    return;
  }
  if (auto && S.restoredIds.has(cur.id)) {
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    toast(auto === "error" ? `${cantPlay(cur, name)}. Press ↓ to skip it.` : "Track ended.", {
      level: "warning",
      ms: 6000,
    });
    return;
  }
  if (auto === "ended" && S.autoSkips >= MAX_AUTO_SKIPS) {
    Player.userPaused = true;
    toast(`Paused: ${MAX_AUTO_SKIPS} tracks ended without input. Press ↓ to skip this one.`, {
      level: "warning",
      ms: 10000,
    });
    return;
  }
  S.busy = true;
  let go = false;
  try {
    go = auto || (await ensureMoveAcknowledged());
  } finally {
    if (!go) S.busy = false;
  }
  if (!go) return;
  if (!S.session || !S.session.current || S.session.current.id !== cur.id) {
    S.busy = false;
    return;
  }
  S.autoSkips = auto === "ended" ? S.autoSkips + 1 : 0;
  const preset = activePreset();
  const label = kind === "skip" ? "Skipped" : preset ? preset.labels[slot] : `Slot ${slot}`;
  S.pendingDecisionAt = performance.now();
  try {
    const snap =
      kind === "skip"
        ? await api("skip", { track_id: cur.id, mode: S.mode })
        : await api("sort", { track_id: cur.id, mode: S.mode, slot });
    if (!auto && S.settings && !S.settings.coach_done && ++S.coachSorts >= COACH_SORTS) finishCoach();
    if (kind === "sort") {
      S.counts[slot] = (S.counts[slot] || 0) + 1;
      pressTile(slot);
    } else pressTile("skip");
    if (!auto && window.Pad && window.Pad.feedback) {
      window.Pad.feedback(
        kind === "skip" ? "skip" : preset && preset.mode === "swipe" ? ["keep", "archive", "favorite"][slot] : "keep"
      );
    }
    flyOutCard(flyDirection(kind, slot));
    if (window.applySession) window.applySession(snap);
    cardEnter();
    if (!auto) clickSound();
    const verb = snap.operation === "copy" ? "Copied to" : "Moved to";
    const name = trackDisplay(cur, S.meta.get(cur.id)).title;
    if (!snap.is_inflyte) {
      if (auto === "error") toast(`${cantPlay(cur, name)} — skipped`, { level: "warning", undo: true });
      else if (auto === "ended") toast(`Track ended — skipped “${name}”`, { undo: true });
      else toast(kind === "skip" ? `Skipped “${name}”` : `${verb} ${label}`, { undo: true });
    }
    if (snap.support_nudge && SUPPORT_URL) {
      toast(`${snap.support_nudge} tracks sorted! If it saves you time, buy me a coffee ☕`, {
        ms: 12000,
        action: { label: "Support", fn: () => window.openExternal && window.openExternal(SUPPORT_URL) },
      });
    }
    refreshCountsSoon();
  } catch (e) {
    S.pendingDecisionAt = 0;
    if (window.Pad && window.Pad.feedback) window.Pad.feedback("error");
    toast(e.message, { level: "error" });
    if (window.refreshState) await window.refreshState().catch(() => {});
  } finally {
    S.busy = false;
  }
}

export async function undo() {
  if (S.busy || !S.session || modalOpen()) return;
  if (!S.session.history || !S.session.history.length) {
    toast("Nothing to undo");
    return;
  }
  S.busy = true;
  try {
    const snap = await api("undo");
    if (snap.current) S.restoredIds.add(snap.current.id);
    if (window.applySession) window.applySession(snap);
    if (window.Pad && window.Pad.feedback) window.Pad.feedback("undo");
    clearUndoToasts();
    toast("Undone");
    refreshCountsSoon(400);
  } catch (e) {
    toast(e.message, { level: "error" });
  } finally {
    S.busy = false;
  }
}

export async function undoAll() {
  if (S.busy || !S.session) return;
  const n = S.session.sorted;
  if (!n) {
    toast("Nothing to undo");
    return;
  }
  const ok = await modal({
    title: "Undo the whole session?",
    body: `All ${n} sorted tracks go back to where they were.`,
    buttons: [
      { label: "Cancel", value: false },
      { label: "Undo all", value: true, danger: true },
    ],
  });
  if (!ok) return;
  S.busy = true;
  try {
    const snap = await api("undo_all");
    for (const t of [snap.current, ...(snap.upcoming || [])].filter(Boolean)) S.restoredIds.add(t.id);
    if (window.applySession) window.applySession(snap);
    clearUndoToasts();
    toast(`Undid ${n} actions`);
    refreshCountsSoon(600);
  } catch (e) {
    toast(e.message, { level: "error" });
  } finally {
    S.busy = false;
  }
}

let countsTimer = 0;
export function refreshCountsSoon(delay = 500) {
  clearTimeout(countsTimer);
  countsTimer = setTimeout(async () => {
    if (!S.session) return;
    try {
      S.counts = await api("folder_counts", { mode: S.mode });
      if (window.renderTiles) window.renderTiles();
    } catch (_) {}
  }, delay);
}

export async function queueChange(method, params = {}) {
  for (let i = 0; S.busy && i < 150; i++) await new Promise((r) => setTimeout(r, 20));
  S.busy = true;
  try {
    return await api(method, params);
  } finally {
    S.busy = false;
  }
}

export async function setOrder(order) {
  if (!S.session) return;
  try {
    const snap = await queueChange("set_order", { order });
    S.settings.track_order = order;
    if (window.applySession) window.applySession(snap);
    toast(order === "random" ? "Random order" : "In order");
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export async function setMode(mode) {
  if (mode === "listen") {
    if (window.Listen && window.Listen.openPicker) window.Listen.openPicker();
    return;
  }
  if (window.Listen && window.Listen.active) {
    await window.Listen.exit();
    if (mode === S.mode) return;
  }
  if (mode === S.mode) return;
  S.mode = mode;
  S.counts = [];
  Wave.markersDirty();
  if (window.renderAll) window.renderAll();
  refreshCountsSoon(0);
}

// Wire bus listeners
bus.on("action:sort", ({ slot }) => decide({ kind: "sort", slot }));
bus.on("action:skip", () => decide({ kind: "skip" }));
bus.on("action:undo", () => undo());
bus.on("playback:ended", ({ auto }) => decide({ kind: "skip", auto }));
bus.on("playback:error", ({ auto }) => decide({ kind: "skip", auto }));

// Global attachments
window.decide = decide;
window.undo = undo;
window.undoAll = undoAll;
window.pressTile = pressTile;
window.refreshCountsSoon = refreshCountsSoon;
window.setMode = setMode;
window.setOrder = setOrder;
