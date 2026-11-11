/* BAKKIS MUSIC SORTER v2 — Main frontend coordinator (ES Module).
 *
 * Coordinates state, audio engines, waveform rendering, cards, gestures, and modals.
 */
"use strict";

import { api, el, fmtTime } from "./api.js";
import { bus } from "./bus.js";
import {
  cardEnter,
  describeFolder,
  fillCard,
  flyDirection,
  flyOutCard,
  missingFolderMessage,
  renderCard,
  slotForDirection,
  swipeLayout,
} from "./cards.js";
import {
  decide,
  finishCoach,
  pressTile,
  refreshCountsSoon,
  setMode,
  setOrder,
  undo,
  undoAll,
} from "./decisions.js";
import {
  checkForUpdate,
  chooseFolder,
  copyFeedback,
  exportPlaylists,
  FOLLOW_LINKS,
  loadFolder,
  loadInflyte,
  loginInflyte,
  loginTrackstack,
  openCommentsFile,
  refreshAuthStatus,
  showMoveHint,
} from "./folder.js";
import { initGestures } from "./gestures.js";
import { initKeyboard } from "./keyboard.js";
import { clickSound, EndWatch, Player, warmUpAudio } from "./player.js";
import { S, activePreset, getMeta, trackDisplay } from "./state.js";
import { initStashModal, StashModal, syncOfflineReviews } from "./stash.js";
import { initCommentsModal, CommentsModal } from "./comments.js";
import { initQueue, QueueDrawer, FilterPanel } from "./queue.js";
import { clearUndoToasts, modal, modalOpen, toast } from "./toasts.js";
import { startAnimationLoop, Wave } from "./wave.js";

const $ = (id) => document.getElementById(id);
const body = document.body;
const POLL_MS = 1000;

export function setScreen(name) {
  for (const s of ["is-empty", "is-scanning", "is-player", "is-done"]) {
    body.classList.toggle(s, s === `is-${name}`);
  }
}

export function currentOperation() {
  if (S.session && S.session.operation) return S.session.operation;
  return S.settings && S.settings.file_operation === "copy" ? "copy" : "move";
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

export function renderCoach() {
  const box = $("coach");
  if (!box) return;
  const show = S.settings && !S.settings.coach_done;
  box.hidden = !show;
  if (!show) return;
  const t = $("coach-text");
  if (!t) return;
  if (S.mode === "grid") {
    t.innerHTML =
      "Listen, then press <kbd>1</kbd>–<kbd>9</kbd> to file the track under that genre. <kbd>↓</kbd> skips, <kbd>Z</kbd> undoes. No track is ever deleted.";
    return;
  }
  const L = swipeLayout();
  const sw = activePreset("swipe");
  const k = (slot) =>
    `<kbd>${L.find((x) => x.slot === slot).glyph}</kbd> ${escapeHtml(sw ? sw.labels[slot] : `Slot ${slot}`)}`;
  const fav = S.settings && S.settings.swipe_archive_hotkey === "up" ? "" : ` · <kbd>↑</kbd> ${escapeHtml(sw ? sw.labels[2] : "Slot 2")}`;
  t.innerHTML = `Listen, then decide: ${k(0)} · ${k(1)}${fav} · <kbd>↓</kbd> Skip. <kbd>1</kbd>–<kbd>9</kbd> jump through the track, <kbd>Z</kbd> undoes. No track is ever deleted.`;
}

export async function goHome() {
  Player.clear();
  try {
    await api("close_session");
  } catch (_) {}
  S.session = null;
  setScreen("empty");
  renderEmpty();
  if ($("promo-caught-up")) $("promo-caught-up").hidden = true;
  renderTopbar();
  toast("Returned to Welcome Hub", { ms: 1800 });
}

export function renderTopbar() {
  const isPromo = !!(S.session && S.session.is_inflyte);
  const hasSession = !!(S.session && S.session.total > 0) || (window.Listen && window.Listen.active);
  if ($("btn-home")) $("btn-home").hidden = !hasSession;

  const root = (S.session && S.session.root) || (S.scan && S.scan.root) || "";
  const name = root.split("/").filter(Boolean).pop() || "";
  if ($("folder-name")) $("folder-name").textContent = name;
  if ($("folder-chip")) {
    $("folder-chip").title = root ? `${root}\nClick to choose another folder` : "Choose a folder";
    $("folder-chip").hidden = isPromo || !hasSession;
  }
  for (const b of document.querySelectorAll(".mode-switch button")) {
    b.classList.toggle("active", b.dataset.mode === S.mode);
  }
  body.classList.toggle("mode-swipe", S.mode === "swipe");
  body.classList.toggle("mode-grid", S.mode === "grid");
  body.classList.toggle("mode-listen", S.mode === "listen");
  body.classList.toggle("plain-tiles", !!S.settings && S.settings.colored_tiles === false);
  const listenBtn = document.querySelector('.mode-switch [data-mode="listen"]');
  if (listenBtn) listenBtn.hidden = !(S.settings && S.settings.discover_in_topbar && S.session && !isPromo);

  const sel = $("preset-select");
  if (sel) {
    sel.textContent = "";
    if (S.presets) {
      const act = activePreset();
      for (const p of S.presets.presets.filter((q) => q.mode === S.mode)) {
        const o = el("option", null, p.name);
        o.value = p.id;
        if (act && p.id === act.id) o.selected = true;
        sel.appendChild(o);
      }
      const edit = el("option", null, "Edit presets…");
      edit.value = "__edit__";
      sel.appendChild(edit);
    }
    sel.hidden = isPromo;
  }
  if ($("op-badge")) $("op-badge").hidden = isPromo || currentOperation() !== "copy";
  if ($("filter-badge")) $("filter-badge").hidden = !(S.session && S.session.filter);
  const sess = S.session;
  if (sess && sess.total) {
    const done = sess.total - sess.remaining;
    if ($("count"))
      $("count").textContent =
        window.Listen && window.Listen.active ? "Discover" : `${Math.min(done + 1, sess.total)} / ${sess.total}`;
    if ($("top-progress-fill")) $("top-progress-fill").style.width = `${(done / sess.total) * 100}%`;
  } else {
    if ($("count")) $("count").textContent = "";
    if ($("top-progress-fill")) $("top-progress-fill").style.width = "0";
  }

  if ($("btn-top-inflyte")) $("btn-top-inflyte").hidden = true;
  if ($("btn-top-login-inflyte")) $("btn-top-login-inflyte").hidden = !isPromo;
  if ($("btn-top-login-trackstack")) $("btn-top-login-trackstack").hidden = !isPromo;
  if ($("btn-edit-comments")) $("btn-edit-comments").hidden = !isPromo;
  if ($("btn-stash-flight")) $("btn-stash-flight").hidden = !isPromo;
  const queuedOffline = (S.session && S.session.offline_queued_count) || 0;
  if ($("btn-sync-offline")) {
    $("btn-sync-offline").hidden = !isPromo || queuedOffline <= 0;
    $("lbl-sync-offline").textContent = `Sync (${queuedOffline})`;
  }
}

export function renderTiles() {
  const sw = activePreset("swipe");
  const swBox = $("tiles-swipe");
  if (swBox) {
    swBox.textContent = "";
    const isInflyte = !!(S.session && S.session.is_inflyte);
    const cur = S.session && S.session.current;
    if (sw) {
      for (const t of swipeLayout()) {
        const skip = t.slot === "skip";
        const color = skip ? "#8A8F98" : sw.colors[t.slot];
        const tile = el("button", "tile");
        tile.type = "button";
        tile.style.setProperty("--tile-color", color);
        tile.dataset.slot = String(t.slot);
        tile.appendChild(el("div", "glyph", t.glyph));
        let tileLabel = skip ? "Skip" : sw.labels[t.slot];
        if (isInflyte) {
          const isTrackstack = cur && cur.source === "trackstack";
          if (skip) {
            tileLabel =
              cur && cur.track_index && cur.track_total && cur.track_index < cur.track_total
                ? `Next Track (${cur.track_index + 1}/${cur.track_total})`
                : "Skip Promo";
          } else if (isTrackstack) {
            if (t.slot === 2 || t.slot === 0) {
              tileLabel = `${sw.labels[t.slot]} (👍 + Download)`;
            } else if (t.slot === 1) {
              tileLabel = `${sw.labels[t.slot]} (👎 Decline)`;
            }
          } else if (t.slot === 2) {
            tileLabel = `${sw.labels[t.slot]} (5/5)`;
          } else if (t.slot === 0) {
            tileLabel = `${sw.labels[t.slot]} (4/5)`;
          } else if (t.slot === 1) {
            tileLabel = `${sw.labels[t.slot]} (Dismiss)`;
          }
        }
        tile.appendChild(el("div", "label", tileLabel));
        tile.appendChild(el("div", "key", t.key));
        const n =
          S.mode !== "swipe" ? null : skip ? (S.session && S.session.counts.skip) || 0 : S.counts[t.slot];
        if (n != null) tile.appendChild(el("div", "badge", String(n)));
        tile.addEventListener("click", () =>
          decide(skip ? { kind: "skip" } : { kind: "sort", slot: t.slot })
        );
        swBox.appendChild(tile);
      }
    }
  }
  const gr = activePreset("grid");
  const grBox = $("tiles-grid");
  if (grBox) {
    grBox.textContent = "";
    if (gr) {
      gr.labels.forEach((label, i) => {
        const color = gr.colors[i];
        const tile = el("button", "tile");
        tile.type = "button";
        tile.style.setProperty("--tile-color", color);
        tile.dataset.slot = String(i);
        tile.appendChild(el("div", "label", label));
        tile.appendChild(el("div", "key", String(i + 1)));
        const n = S.mode === "grid" ? S.counts[i] : null;
        if (n != null) tile.appendChild(el("div", "badge", String(n)));
        tile.addEventListener("click", () => decide({ kind: "sort", slot: i }));
        grBox.appendChild(tile);
      });
    }
  }
}

export function renderFooterStats() {
  const box = $("footer-stats");
  if (!box) return;
  const sess = S.session;
  const show = !!(sess && sess.sorted && (!window.Listen || !window.Listen.active));
  box.hidden = !show;
  body.classList.toggle("has-stats", show);
  if (!show) return;
  const info = sess.count_info || {};
  const groups = Object.entries(sess.counts)
    .filter(([k, n]) => n && k !== "skip")
    .map(([k, n]) => ({ n, label: (info[k] && info[k].label) || k, slot: info[k] ? info[k].slot : 99 }))
    .sort((a, b) => b.n - a.n);
  const skipped = sess.counts.skip || 0;
  const parts = [`${sess.sorted} sorted`, ...groups.slice(0, 4).map((g) => `${g.n} ${g.label}`)];
  if (skipped) parts.push(`${skipped} skipped`);
  const kept = Object.entries(sess.counts)
    .filter(([k]) => k !== "skip" && info[k] && (info[k].mode === "grid" || info[k].slot !== 1))
    .reduce((sum, [, n]) => sum + n, 0);
  if (sess.sorted) parts.push(`${Math.round((100 * kept) / sess.sorted)}% kept`);
  const mins = Math.floor(sess.elapsed / 60);
  parts.push(mins >= 1 ? `${mins}m` : `${Math.floor(sess.elapsed)}s`);
  box.textContent = parts.join(" · ");
}

export function renderHints() {
  const h = $("hints");
  if (!h) return;
  h.textContent = "";
  const add = (k, label) => {
    const s = el("span");
    s.appendChild(el("kbd", null, k));
    s.appendChild(document.createTextNode(label));
    h.appendChild(s);
  };
  if (!S.session) {
    add("H", "shortcuts");
    return;
  }
  if (window.Listen && window.Listen.active) {
    add("← →", "previous / next");
    add("↑ or F", "heart");
    add("Space", "play");
    add("1–9", "jump");
    add("L", "back to sorting");
    return;
  }
  if (S.mode === "swipe") {
    add("← → ↑", "sort");
    add("↓", "skip");
    add("1–9", "jump");
  } else {
    add("1–9", "sort");
    add("↓", "skip");
  }
  add("Space", "play");
  add("[ ]", "±32b");
  add("⇧D", "drop");
  add("A D", "±30s");
  add("Z", "undo");
  add("H", "all shortcuts");
}

export function renderEmpty() {
  const path = S.settings && S.settings.default_folder_path;
  const btn = $("btn-load-default");
  if (btn) {
    btn.hidden = !(path && S.defaultFolderExists);
    if (path) btn.textContent = `Load ${path.split("/").filter(Boolean).pop()}`;
  }
  const msg = $("empty-message");
  if (msg && path && !S.defaultFolderExists) {
    msg.textContent = missingFolderMessage(path);
    msg.title = path;
    msg.hidden = false;
  }
}

export function renderDoneFollow() {
  const p = $("done-follow");
  if (!p) return;
  p.textContent = "";
  p.appendChild(el("span", "muted", "Sorted with BAKKIS MUSIC SORTER · Follow BAKKIS on "));
  FOLLOW_LINKS.slice(0, 3).forEach(([label, url], i) => {
    if (i) p.appendChild(el("span", "muted", " · "));
    const b = el("button", "link-btn", label);
    b.type = "button";
    b.addEventListener("click", () => window.openExternal && window.openExternal(url));
    p.appendChild(b);
  });
}

export function renderDone() {
  const box = $("done-stats");
  if (!box) return;
  box.textContent = "";
  const sess = S.session;
  if (!sess) return;
  const rows = [];
  for (const [k, v] of Object.entries(sess.counts)) {
    if (!v) continue;
    if (k === "skip") {
      rows.push({ label: "Skipped", sub: "", n: v, color: "#8A8F98", order: 99 });
      continue;
    }
    const [pid, slot] = k.split(":");
    const info = (sess.count_info || {})[k] || {};
    const p = S.presets && S.presets.presets.find((q) => q.id === pid);
    rows.push({
      label: info.label || (p ? p.labels[Number(slot)] : k),
      sub: info.preset || (p ? p.name : ""),
      n: v,
      color: info.color || (p ? p.colors[Number(slot)] : "#8A8F98"),
      order: Number(slot),
    });
  }
  rows.sort((a, b) => (a.order === 99) - (b.order === 99) || a.sub.localeCompare(b.sub) || a.order - b.order);
  for (const r of rows) {
    const name = el("div", "done-label");
    const dot = el("i", "dot");
    dot.style.background = r.color;
    name.appendChild(dot);
    name.appendChild(document.createTextNode(r.label));
    if (r.sub) name.appendChild(el("span", "muted small", ` · ${r.sub}`));
    box.appendChild(name);
    box.appendChild(el("div", "n", String(r.n)));
  }
  const total = rows.reduce((a, r) => a + r.n, 0);
  box.appendChild(el("div", "done-sep"));
  box.appendChild(el("div", "done-sep"));
  box.appendChild(el("div", null, "Total"));
  box.appendChild(el("div", "n", String(total)));
  box.appendChild(el("div", "muted", "Time"));
  box.appendChild(el("div", "n muted", fmtTime(sess.elapsed)));
  const verb = currentOperation() === "copy" ? "copied" : "moved";
  renderDoneFollow();
  const held = sess.held || 0;
  const isPromo = !!(sess.is_inflyte || (sess.root && sess.root.includes("Inflyte Promo Box")));
  const h1 = document.querySelector("#screen-done h1");
  if (h1) h1.textContent = held ? "No more tracks match the filter" : "All done";

  if (isPromo) {
    if ($("btn-done-clear-filter")) $("btn-done-clear-filter").hidden = true;
    if ($("btn-done-undo")) $("btn-done-undo").hidden = true;
    if ($("btn-done-feedback")) $("btn-done-feedback").hidden = true;
    if ($("btn-done-reveal")) $("btn-done-reveal").hidden = true;
    if ($("btn-done-export")) $("btn-done-export").hidden = true;
    if ($("btn-done-load")) $("btn-done-load").hidden = true;
    if ($("btn-done-inflyte")) $("btn-done-inflyte").hidden = false;
    if ($("btn-done-promo-history")) $("btn-done-promo-history").hidden = false;
    if ($("btn-done-home")) $("btn-done-home").hidden = false;

    if ($("done-note")) {
      $("done-note").textContent = total
        ? "All promo feedback was submitted to Inflyte & Trackstack. Full-res files downloaded to your Downloads folder."
        : "No promos were reviewed.";
    }
  } else {
    if ($("btn-done-clear-filter")) $("btn-done-clear-filter").hidden = !held;
    if ($("btn-done-undo")) $("btn-done-undo").hidden = false;
    if ($("btn-done-feedback")) $("btn-done-feedback").hidden = false;
    if ($("btn-done-reveal")) $("btn-done-reveal").hidden = false;
    if ($("btn-done-export")) $("btn-done-export").hidden = !total;
    if ($("btn-done-load")) $("btn-done-load").hidden = false;
    if ($("btn-done-inflyte")) $("btn-done-inflyte").hidden = true;
    if ($("btn-done-promo-history")) $("btn-done-promo-history").hidden = true;
    if ($("btn-done-home")) $("btn-done-home").hidden = false;

    const folder = sess.root ? sess.root.split("/").filter(Boolean).pop() : "";
    const tree = sess.in_place ? folder : verb === "copied" ? "BAKKIS_SORTED_COPIES" : "BAKKIS_SORTED";
    let note = total
      ? `Files were ${verb} into ${sess.in_place ? "preset folders inside " + folder : tree + " inside " + folder}.`
      : "";
    const grid = rows.find(
      (r) => r.order !== 99 && S.presets && S.presets.presets.some((p) => p.mode === "grid" && p.name === r.sub)
    );
    if (grid && !sess.in_place) note += ` Next step: load ${tree}/${grid.sub}/${grid.label} and Quick Sort it.`;
    if ($("done-note")) $("done-note").textContent = note;
  }
}

export function renderAll() {
  renderTopbar();
  renderTiles();
  renderHints();
  renderFooterStats();
  if (window.Listen && window.Listen.active) {
    setScreen("player");
    return;
  }
  const sess = S.session;
  if (S.scan && S.scan.status === "scanning") {
    setScreen("scanning");
    if ($("scan-text"))
      $("scan-text").textContent = S.scan.message
        ? S.scan.message
        : S.scan.count
        ? `Scanning… ${S.scan.count} tracks`
        : "Scanning…";
    const f = describeFolder(S.scan.root);
    if ($("scan-root")) {
      $("scan-root").textContent = S.scan.root ? `${f.name}${f.where ? " · " + f.where : ""}` : "";
      $("scan-root").title = S.scan.root || "";
    }
  } else if (!sess) {
    setScreen("empty");
    renderEmpty();
  } else if (!sess.total) {
    setScreen("empty");
    renderEmpty();
    if (sess.is_inflyte) {
      if ($("promo-caught-up")) $("promo-caught-up").hidden = false;
      if ($("empty-message")) $("empty-message").hidden = true;
    } else {
      const msg = $("empty-message");
      if (msg) {
        msg.textContent = `No audio files found in ${sess.root
          .split("/")
          .filter(Boolean)
          .pop()}. Supported: mp3, wav, aiff, flac, m4a, aac, alac, ogg.`;
        msg.hidden = false;
      }
      if ($("promo-caught-up")) $("promo-caught-up").hidden = true;
    }
  } else if (!sess.current) {
    setScreen("done");
    renderDone();
    Player.clear();
  } else {
    setScreen("player");
    renderCoach();
  }
}

export function applySession(snap) {
  const prevId = S.session && S.session.current && S.session.current.id;
  const prevRoot = S.session && S.session.root;
  S.session = snap;
  if (snap && snap.in_place && snap.root !== prevRoot) {
    const copying = S.settings && S.settings.file_operation === "copy";
    toast(
      `Sorting inside ${snap.root.split("/").filter(Boolean).pop()}: results go into preset folders right here.` +
        (copying
          ? " These tracks are already copies, so they are moved, not copied again. Your originals stay untouched."
          : ""),
      { ms: 9000 }
    );
  }
  if (snap && snap.last_notice > S.noticeSeq) pollNotices();
  if (snap && snap.current) warmUpAudio();
  renderAll();
  if (window.Drawers && window.Drawers.onSession) window.Drawers.onSession();
  if (window.Listen && window.Listen.active) return;
  const cur = snap && snap.current;
  if (!cur) return;
  if (cur.id !== prevId || cur.id !== Player.curId) {
    Wave.reset(cur.id);
    Player.load(cur.id);
    renderCard();
    bus.emit("track:selected", { track: cur });
  }
  const nxt = snap.upcoming && snap.upcoming[0];
  if (nxt) {
    Player.preload(nxt.id);
  }
  if (snap.next_in_promo_track_id) {
    Player.preloadAlt(snap.next_in_promo_track_id);
  }
  if (snap.upcoming) {
    for (const u of snap.upcoming.slice(0, 4)) {
      getMeta(u.id)
        .then((m) => {
          if (m && m.has_cover) {
            const preImg = new Image();
            preImg.src = coverUrl(u.id);
          }
        })
        .catch(() => {});
    }
  }
}

export async function refreshState() {
  const st = await api("state");
  S.settings = st.settings;
  S.presets = st.presets;
  S.scan = st.scan;
  S.version = st.version;
  S.defaultFolderExists = st.default_folder_exists;
  S.home = st.home;
  applySession(st.session);
  return st;
}

export async function pollNotices() {
  if (!S.session) return;
  try {
    const list = await api("notices", { since: S.noticeSeq });
    let needRefresh = false;
    for (const n of list) {
      S.noticeSeq = Math.max(S.noticeSeq, n.seq);
      toast(n.message, { level: n.level === "error" ? "error" : n.level === "warning" ? "warning" : "info" });
      if (n.level === "error") needRefresh = true;
    }
    if (needRefresh) {
      await refreshState();
      refreshCountsSoon(0);
    }
  } catch (_) {}
}

export async function poll() {
  try {
    if (S.scan && S.scan.status === "scanning") {
      const st = await refreshState();
      if (st.scan.status === "ready") refreshCountsSoon(0);
      if (st.scan.status === "error") toast(`Scan failed: ${st.scan.error}`, { level: "error" });
    } else {
      await pollNotices();
    }
  } finally {
    setTimeout(poll, S.scan && S.scan.status === "scanning" ? 200 : POLL_MS);
  }
}

export async function boot() {
  Wave.init();
  Player.init();
  initGestures();
  initKeyboard();
  initStashModal();
  initQueue();

  if (Wave.canvas) {
    new ResizeObserver(() => Wave.resize()).observe(Wave.canvas);
  }
  for (const b of document.querySelectorAll(".mode-switch button")) {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  }
  if ($("btn-play")) $("btn-play").addEventListener("click", () => Player.toggle());
  if ($("btn-home")) $("btn-home").addEventListener("click", goHome);
  if ($("brand-home")) $("brand-home").addEventListener("click", goHome);
  if ($("btn-choose")) $("btn-choose").addEventListener("click", chooseFolder);
  if ($("btn-done-load")) $("btn-done-load").addEventListener("click", chooseFolder);
  if ($("btn-load-inflyte")) $("btn-load-inflyte").addEventListener("click", () => loadInflyte("all"));
  if ($("btn-top-inflyte")) $("btn-top-inflyte").addEventListener("click", () => loadInflyte("all"));
  if ($("btn-done-inflyte")) $("btn-done-inflyte").addEventListener("click", () => loadInflyte("all"));
  if ($("btn-done-promo-history"))
    $("btn-done-promo-history").addEventListener("click", () => window.Drawers && window.Drawers.show("promo-history"));
  if ($("btn-done-home")) $("btn-done-home").addEventListener("click", goHome);
  if ($("btn-login-inflyte")) $("btn-login-inflyte").addEventListener("click", loginInflyte);
  if ($("btn-top-login-inflyte")) $("btn-top-login-inflyte").addEventListener("click", loginInflyte);
  if ($("btn-login-trackstack")) $("btn-login-trackstack").addEventListener("click", loginTrackstack);
  if ($("btn-top-login-trackstack")) $("btn-top-login-trackstack").addEventListener("click", loginTrackstack);
  initCommentsModal();
  if ($("btn-done-undo")) $("btn-done-undo").addEventListener("click", undo);

  if (window.setupPanels) window.setupPanels();
  if (window.setupListen) window.setupListen();

  if ($("btn-promo-history"))
    $("btn-promo-history").addEventListener("click", () => window.Drawers && window.Drawers.show("promo-history"));
  if ($("btn-caught-up-history"))
    $("btn-caught-up-history").addEventListener("click", () => window.Drawers && window.Drawers.show("promo-history"));
  if ($("btn-caught-up-refresh"))
    $("btn-caught-up-refresh").addEventListener("click", () => loadInflyte("all"));
  if ($("btn-caught-up-dismiss"))
    $("btn-caught-up-dismiss").addEventListener("click", () => {
      if ($("promo-caught-up")) $("promo-caught-up").hidden = true;
    });
  if ($("btn-queue")) $("btn-queue").addEventListener("click", () => window.Drawers && window.Drawers.toggle("queue"));
  if ($("btn-history"))
    $("btn-history").addEventListener("click", () => window.Drawers && window.Drawers.toggle("history"));
  if ($("folder-chip")) $("folder-chip").addEventListener("click", chooseFolder);
  if ($("btn-settings"))
    $("btn-settings").addEventListener("click", () => {
      if (window.SettingsDrawer) window.SettingsDrawer.view = "main";
      if (window.Drawers) window.Drawers.toggle("settings");
    });
  if ($("btn-sorter-settings"))
    $("btn-sorter-settings").addEventListener("click", () => {
      if (window.SettingsDrawer) {
        window.SettingsDrawer.view = "main";
        window.SettingsDrawer.scrollTo = "presets";
      }
      if (window.Drawers) window.Drawers.show("settings");
    });
  if ($("btn-help")) $("btn-help").addEventListener("click", () => window.Guide && window.Guide.toggle());
  if ($("coach-close")) $("coach-close").addEventListener("click", finishCoach);
  if ($("filter-badge"))
    $("filter-badge").addEventListener("click", () => window.Drawers && window.Drawers.show("queue"));
  if ($("btn-done-clear-filter"))
    $("btn-done-clear-filter").addEventListener("click", () => window.FilterPanel && window.FilterPanel.clear());
  if ($("op-badge"))
    $("op-badge").addEventListener("click", () => {
      if (window.SettingsDrawer) window.SettingsDrawer.view = "main";
      if (window.Drawers) window.Drawers.show("settings");
    });
  if ($("btn-done-export")) $("btn-done-export").addEventListener("click", exportPlaylists);
  if ($("btn-done-feedback")) $("btn-done-feedback").addEventListener("click", () => copyFeedback());
  if ($("btn-feedback-queue")) $("btn-feedback-queue").addEventListener("click", () => copyFeedback());
  if ($("btn-done-reveal"))
    $("btn-done-reveal").addEventListener("click", () =>
      api("reveal").catch((e) => toast(e.message, { level: "error" }))
    );
  if ($("btn-load-default"))
    $("btn-load-default").addEventListener("click", () =>
      S.settings && S.settings.default_folder_path && loadFolder(S.settings.default_folder_path)
    );
  if ($("preset-select")) {
    $("preset-select").addEventListener("change", async (e) => {
      if (e.target.value === "__edit__") {
        e.target.blur();
        renderTopbar();
        if (window.SettingsDrawer) {
          window.SettingsDrawer.view = "main";
          window.SettingsDrawer.scrollTo = "presets";
        }
        if (window.Drawers) window.Drawers.show("settings");
        return;
      }
      try {
        await api("preset_select", { preset_id: e.target.value });
        await refreshState();
        refreshCountsSoon(0);
      } catch (err) {
        toast(err.message, { level: "error" });
      }
      e.target.blur();
    });
  }

  let volTimer = 0;
  if ($("volume")) {
    $("volume").addEventListener("input", (e) => {
      const v = Number(e.target.value);
      Player.setVolume(v);
      clearTimeout(volTimer);
      volTimer = setTimeout(
        () =>
          api("set_settings", { volume: v })
            .then((s) => (S.settings = s))
            .catch(() => {}),
        400
      );
    });
  }

  const st = await refreshState();
  S.mode = st.settings.default_mode_on_startup === "grid" ? "grid" : "swipe";
  if (st.settings.startup_preset_id) {
    const p = st.presets.presets.find((q) => q.id === st.settings.startup_preset_id);
    if (p) {
      S.mode = p.mode;
      await api("preset_select", { preset_id: p.id }).catch(() => {});
      await refreshState();
    }
  }
  if ($("volume")) $("volume").value = String(st.settings.volume);
  Player.setVolume(st.settings.volume);
  renderAll();
  refreshAuthStatus();
  startAnimationLoop();

  if (
    !st.session &&
    st.scan.status === "idle" &&
    st.settings.auto_load_default_folder &&
    st.settings.default_folder_path
  ) {
    await loadFolder(st.settings.default_folder_path);
    if (!S.scan || S.scan.status !== "scanning") {
      const msg = $("empty-message");
      if (msg) {
        msg.textContent = missingFolderMessage(st.settings.default_folder_path);
        msg.title = st.settings.default_folder_path;
        msg.hidden = false;
      }
    }
  } else if (st.session) {
    refreshCountsSoon(0);
  }
  if (S.settings.show_queue_panel_on_startup && S.session && window.Drawers) window.Drawers.show("queue");
  if (st.move_hint && !S.settings.move_hint_seen) showMoveHint(st.move_hint);
  if (S.settings.check_for_updates) setTimeout(checkForUpdate, 4000);
  if (!S.settings.welcome_seen) {
    if (window.Welcome) window.Welcome.show();
  } else if (S.settings.show_help_guide_on_startup || !S.settings.guide_seen) {
    if (window.Guide) window.Guide.show();
    if (!S.settings.guide_seen)
      api("set_settings", { guide_seen: true })
        .then((x) => (S.settings = x))
        .catch(() => {});
  }
  poll();
}

// Global attachments
window.setScreen = setScreen;
window.renderAll = renderAll;
window.renderTiles = renderTiles;
window.renderTopbar = renderTopbar;
window.renderHints = renderHints;
window.renderFooterStats = renderFooterStats;
window.applySession = applySession;
window.refreshState = refreshState;
window.goHome = goHome;

// Boot application
boot().catch((e) => {
  console.error("Boot failure:", e);
  document.body.innerHTML = `<pre style="padding:24px;color:#ff7a7f">Failed to start: ${String(
    e.message || e
  )}</pre>`;
});
