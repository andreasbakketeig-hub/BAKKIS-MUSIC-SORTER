/* BAKKIS MUSIC SORTER — Folder choosing, library scanning, promo logins, and updater. */
"use strict";

import { api } from "./api.js";
import { Player } from "./player.js";
import { S } from "./state.js";
import { modal, toast } from "./toasts.js";

const $ = (id) => document.getElementById(id);
const body = document.body;

export const FOLLOW_LINKS = [
  ["Instagram", "https://instagram.com/bakkismusic"],
  ["SoundCloud", "https://soundcloud.com/bakkismusic"],
  ["Spotify", "https://open.spotify.com/artist/77E3j4V0F4KzL8dE99aXkE"],
];

export async function pickFolderPath() {
  try {
    return await api("choose_folder");
  } catch (e) {
    if (e.status !== 404) {
      toast(e.message, { level: "error" });
      return null;
    }
    return (
      window.prompt("Music folder path (use a TEST folder):", (S.settings && S.settings.default_folder_path) || "") ||
      null
    );
  }
}

export async function chooseFolder() {
  const path = await pickFolderPath();
  if (path) loadFolder(path);
}

export async function loadFolder(path) {
  if (window.Listen && window.Listen.active) await window.Listen.exit();
  const emptyMsg = $("empty-message");
  if (emptyMsg) emptyMsg.hidden = true;
  try {
    Player.clear();
    S.meta.clear();
    S.counts = [];
    S.scan = await api("load_folder", { path });
    S.session = null;
    if (window.renderAll) window.renderAll();
  } catch (e) {
    if (emptyMsg) {
      emptyMsg.textContent = e.message;
      emptyMsg.hidden = false;
    }
    if (window.setScreen) window.setScreen("empty");
  }
}

export function renderAuthIndicators(auth) {
  if (!auth) return;
  S.auth = auth;
  const infConn = !!(auth.inflyte && auth.inflyte.connected);
  const tsConn = !!(auth.trackstack && auth.trackstack.connected);
  for (const id of ["dot-main-inflyte", "dot-top-inflyte"]) {
    const d = $(id);
    if (d) d.classList.toggle("connected", infConn);
  }
  for (const id of ["dot-main-trackstack", "dot-top-trackstack"]) {
    const d = $(id);
    if (d) d.classList.toggle("connected", tsConn);
  }
  if ($("lbl-main-inflyte")) $("lbl-main-inflyte").textContent = infConn ? "Inflyte · Connected" : "Inflyte · Log In";
  if ($("lbl-main-trackstack"))
    $("lbl-main-trackstack").textContent = tsConn ? "Trackstack · Connected" : "Trackstack · Log In";
  if ($("btn-top-login-inflyte"))
    $("btn-top-login-inflyte").title = infConn
      ? "Inflyte Connected (click to view window)"
      : "Click to log into Inflyte";
  if ($("btn-top-login-trackstack"))
    $("btn-top-login-trackstack").title = tsConn
      ? "Trackstack Connected (click to view window)"
      : "Click to log into Trackstack";
}

let authPollTimer = 0;
export async function refreshAuthStatus() {
  try {
    const auth = await api("auth_status");
    renderAuthIndicators(auth);
    const anyWindowOpenNotConnected =
      (auth.inflyte && auth.inflyte.window_open && !auth.inflyte.connected) ||
      (auth.trackstack && auth.trackstack.window_open && !auth.trackstack.connected);
    clearTimeout(authPollTimer);
    if (anyWindowOpenNotConnected || body.classList.contains("is-empty")) {
      authPollTimer = setTimeout(refreshAuthStatus, anyWindowOpenNotConnected ? 2000 : 5000);
    }
  } catch (_) {}
}

export async function loginInflyte() {
  try {
    const auth = await api("login_inflyte");
    renderAuthIndicators(auth);
    toast(
      auth.inflyte && auth.inflyte.connected
        ? "Inflyte is connected!"
        : "Opened Inflyte login window — sign in to connect",
      { ms: 4000 }
    );
    clearTimeout(authPollTimer);
    authPollTimer = setTimeout(refreshAuthStatus, 2000);
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export async function loginTrackstack() {
  try {
    const auth = await api("login_trackstack");
    renderAuthIndicators(auth);
    toast(
      auth.trackstack && auth.trackstack.connected
        ? "Trackstack is connected!"
        : "Opened Trackstack login window — sign in to connect",
      { ms: 4000 }
    );
    clearTimeout(authPollTimer);
    authPollTimer = setTimeout(refreshAuthStatus, 2000);
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export async function loadInflyte(source = "all") {
  const srcArg = typeof source === "string" ? source : "all";
  if (window.Listen && window.Listen.active) await window.Listen.exit();
  const emptyMsg = $("empty-message");
  if (emptyMsg) emptyMsg.hidden = true;
  try {
    Player.clear();
    S.meta.clear();
    S.counts = [];
    S.mode = "swipe";
    S.scan = await api("load_inflyte", { source: srcArg });
    S.session = null;
    if (window.refreshState) await window.refreshState().catch(() => {});
    if (window.renderAll) window.renderAll();
  } catch (e) {
    toast(e.message, { level: "error" });
    if (emptyMsg) {
      emptyMsg.textContent = e.message;
      emptyMsg.hidden = false;
    }
    if (window.setScreen) window.setScreen("empty");
  }
}

export async function openCommentsFile() {
  try {
    const path = await api("open_comments_file");
    toast(`Opened ${path.split("/").pop()} — edit [5/5] and [4/5] sections anytime`, { ms: 4000 });
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export function treePath(path) {
  const m = /\/(BAKKIS_SORTED(?:_COPIES)?\/.*)$/.exec(path || "");
  return m ? m[1] : path;
}

export async function copyFeedback(sessionId) {
  try {
    const r = await api("feedback", sessionId ? { session_id: sessionId } : {});
    if (!r.count) {
      toast("Nothing to report yet: sort a few tracks first (skips are not included)");
      return;
    }
    toast(`Feedback for ${r.count} track${r.count === 1 ? "" : "s"} copied. Paste it anywhere.`, { ms: 5000 });
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export async function exportPlaylists() {
  try {
    const r = await api("export_playlists");
    const n = r.playlists.length;
    if (!n) {
      toast("Nothing sorted yet to export");
      return;
    }
    toast(`Exported ${n} playlist${n === 1 ? "" : "s"} to ${treePath(r.folder)}`, {
      ms: 8000,
      action: {
        label: "Show",
        fn: () => api("reveal", { target: "playlists" }).catch(() => {}),
      },
    });
  } catch (e) {
    toast(e.message, { level: "error" });
  }
}

export async function checkForUpdate() {
  try {
    const r = await api("check_update");
    S.update = r;
    if (r.newer && r.url) {
      if (r.can_install)
        toast(`BAKKIS MUSIC SORTER ${r.latest} is available`, {
          ms: 15000,
          action: { label: "Update now", fn: installUpdate },
        });
      else
        toast(`BAKKIS MUSIC SORTER ${r.latest} is available. ${r.install_problem || ""}`.trim(), {
          ms: 15000,
          action: {
            label: "Download",
            fn: () => window.openExternal && window.openExternal(r.url),
          },
        });
    }
  } catch (_) {}
}

export function showMoveHint(text) {
  setTimeout(
    () =>
      toast(text, {
        ms: 20000,
        action: { label: "Show Applications", fn: () => api("show_applications").catch(() => {}) },
      }),
    2500
  );
  api("set_settings", { move_hint_seen: true })
    .then((x) => (S.settings = x))
    .catch(() => {});
}

let updating = false;
export async function installUpdate() {
  const u = S.update;
  if (!u || !u.newer || updating) return;
  updating = true;
  try {
    await runUpdate(u);
  } finally {
    updating = false;
  }
}

async function runUpdate(u) {
  if (!u.can_install) {
    toast(u.install_problem || "Download the new version from the website.", {
      level: "warning",
      ms: 8000,
      action: { label: "Download", fn: () => window.openExternal && window.openExternal(u.url) },
    });
    return;
  }
  const ok = await modal({
    title: `Update to ${u.latest}?`,
    body: "The new version downloads in the background and is checked before anything changes. Then the app restarts. Your settings, presets and history stay.",
    buttons: [
      { label: "Not now", value: false },
      { label: "Update", value: true, primary: true },
    ],
  });
  if (!ok) return;
  try {
    await api("update_install");
    let last = -1,
      misses = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      let st;
      try {
        st = await api("update_status");
        misses = 0;
      } catch (err) {
        if (++misses > 5) throw err;
        continue;
      }
      if (st.status === "error") throw new Error(st.error || "Update failed");
      if (st.status === "restarting") return;
      if (st.status === "ready") break;
      if (st.status === "unpacking") {
        if (last < 100) {
          last = 100;
          toast(`Checking ${u.latest}…`, { ms: 3000 });
        }
        continue;
      }
      const pct = Math.round((st.progress || 0) * 100);
      if (pct >= last + 25) {
        last = pct;
        toast(`Downloading ${u.latest}… ${pct}%`, { ms: 2500 });
      }
    }
    toast(`Restarting into ${u.latest}…`, { ms: 5000 });
    await new Promise((r) => setTimeout(r, 600));
    await api("update_restart");
  } catch (e) {
    toast(`${e.message}. You can also download it from the website.`, {
      level: "error",
      ms: 10000,
      action: { label: "Download", fn: () => window.openExternal && window.openExternal(u.url) },
    });
  }
}

// Global attachments
window.pickFolderPath = pickFolderPath;
window.chooseFolder = chooseFolder;
window.loadFolder = loadFolder;
window.loadInflyte = loadInflyte;
window.loginInflyte = loginInflyte;
window.loginTrackstack = loginTrackstack;
window.refreshAuthStatus = refreshAuthStatus;
window.openCommentsFile = openCommentsFile;
window.exportPlaylists = exportPlaylists;
window.copyFeedback = copyFeedback;
