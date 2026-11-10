/* BAKKIS MUSIC SORTER v2 — Common Drawer Constants & External Utilities */
"use strict";

export const QUEUE_ROW_H = 40;
export const QUEUE_PAGE = 100;
export const PALETTE = [
  ["Teal", "#00D6B4"], ["Blue", "#18A0FB"], ["Purple", "#7C5CFF"], ["Gray", "#8A8F98"], ["Orange", "#FF7A1A"],
  ["Pink", "#D65CFF"], ["Red", "#FF2D55"], ["Yellow", "#FFF200"], ["Dark", "#2A2F36"],
];
export const START_CHOICES = [
  [0, "0:00"], [30, "0:30"], [60, "1:00"], [90, "1:30"], [120, "2:00"],
  [150, "2:30"], [180, "3:00"], [240, "4:00"], [300, "5:00"], [360, "6:00"],
];

// Promo / contact. Links open in the default browser via the backend (never inside the app window).
export const CONTACT_EMAIL = "bakkis@bakkis.com";
export const SUPPORT_URL = "https://ko-fi.com/bakkis";
export const FOLLOW_LINKS = [
  ["Spotify", "https://open.spotify.com/artist/4gKuT9rOlvCpHJUkWp4JFv"],
  ["SoundCloud", "https://soundcloud.com/bakkismusic"],
  ["Instagram", "https://www.instagram.com/bakkismusic/"],
  ["Beatport", "https://www.beatport.com/artist/bakkis/1216154"],
  ["Traxsource", "https://www.traxsource.com/artist/842861/bakkis"],
  ["bakkis.com", "https://bakkis.com"],
];

export function openExternal(url) {
  const callApi = window.api || (async () => {});
  const showToast = window.toast || (() => {});
  callApi("open_url", { url }).catch(() => showToast("Could not open the link", { level: "error" }));
}

/** mailto: link with app + macOS version and the latest warnings, so a bug report is useful right away. */
export async function reportProblem() {
  const callApi = window.api || (async () => ({}));
  const state = window.S || {};
  let about = {};
  try { about = await callApi("about"); } catch (_) { /* send what we have */ }
  let recent = [];
  try { recent = (await callApi("notices", { since: 0 })).filter((n) => n.level !== "info").slice(-5); } catch (_) { /* no session */ }
  let dbErrors = [];
  try { dbErrors = (await callApi("get_logs", { min_level: "ERROR", limit: 3 })) || []; } catch (_) {}
  const lines = [
    "What happened?",
    "",
    "",
    "What did you expect?",
    "",
    "",
    "---",
    `App: BAKKIS MUSIC SORTER ${about.version || state.version || ""}`,
    `macOS: ${about.macos || ""}`,
    `Mode: ${state.mode || ""} · ${state.settings ? state.settings.file_operation : ""}`,
  ];
  if (recent.length) lines.push("Recent messages:", ...recent.map((n) => `- [${n.level}] ${n.message}`));
  if (dbErrors.length) {
    lines.push("Recent System Exceptions:");
    for (const err of dbErrors) {
      lines.push(`- [${err.module}] ${err.exception_type || 'Error'}: ${err.message}`);
      if (err.traceback) {
        lines.push(`  ${err.traceback.trim().split("\n").slice(-3).join("\n  ")}`);
      }
    }
  }
  const subject = `BAKKIS MUSIC SORTER ${about.version || ""} – problem`;
  openExternal(`mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(lines.join("\n"))}`);
}
