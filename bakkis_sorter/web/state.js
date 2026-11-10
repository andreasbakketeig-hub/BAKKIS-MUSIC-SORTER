/* BAKKIS MUSIC SORTER — Application state & track metadata cache. */
"use strict";

import { api } from "./api.js";

export const S = {
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

export function activePreset(mode = S.mode) {
  if (!S.presets) return null;
  const id = S.presets[`active_${mode}_preset_id`];
  return S.presets.presets.find((p) => p.id === id) || null;
}

export function trackDisplay(track, meta) {
  if (meta && meta.title) return { title: meta.title, artist: meta.artist || "" };
  if (meta && meta.display && meta.display !== track.name) {
    const [t, a] = meta.display.split(" — ");
    return { title: t, artist: a || meta.artist || "" };
  }
  return { title: track.name.replace(/\.[^.]+$/, ""), artist: (meta && meta.artist) || "" };
}

export async function getMeta(id) {
  if (S.meta.has(id)) return S.meta.get(id);
  const m = await api("track_meta", { track_id: id });
  S.meta.set(id, m);
  if (S.meta.size > 300) S.meta.delete(S.meta.keys().next().value);
  return m;
}

// Global attachments for companion scripts (panels.js, listen.js, Web Inspector)
window.S = S;
window.__bakkis = S;
window.activePreset = activePreset;
window.trackDisplay = trackDisplay;
window.getMeta = getMeta;
