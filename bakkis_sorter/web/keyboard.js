/* BAKKIS MUSIC SORTER — Keyboard shortcuts router & fullscreen handling. */
"use strict";

import { api } from "./api.js";
import { slotForDirection } from "./cards.js";
import { decide, setMode, setOrder, undo, undoAll } from "./decisions.js";
import { Player } from "./player.js";
import { S } from "./state.js";
import { StashModal } from "./stash.js";
import { modal, modalOpen } from "./toasts.js";

const JUMPS = [0, 30, 60, 90, 120, 180, 240, 300];
const SEEK_STEP = 30;
const body = document.body;

export function toggleFullscreen() {
  api("toggle_fullscreen").catch(() => {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
  });
}

export function onKey(e) {
  S.autoSkips = 0;
  if (modalOpen()) {
    if (e.key === "Escape") {
      e.preventDefault();
      modal.cancel();
    } else if (e.key === "Enter") {
      e.preventDefault();
      modal.confirm();
    }
    return;
  }
  if (StashModal.isOpen) {
    if (e.key === "Escape") {
      e.preventDefault();
      StashModal.close();
    }
    return;
  }
  if (window.Welcome && window.Welcome.isOpen) {
    if (e.key === "Escape" || e.key === "Enter") {
      e.preventDefault();
      window.Welcome.close();
    }
    return;
  }
  if (window.Guide && window.Guide.isOpen) {
    if (["Escape", "h", "H", "?"].includes(e.key)) {
      e.preventDefault();
      window.Guide.close();
    }
    return;
  }
  if (window.Listen && window.Listen.pickerOpen) {
    if (e.key === "Escape") {
      e.preventDefault();
      window.Listen.closePicker();
    } else if (e.key === "Enter") {
      e.preventDefault();
      window.Listen.startFromPicker();
    }
    return;
  }
  if (
    window.Drawers &&
    (window.Drawers.open === "settings" || window.Drawers.open === "history" || window.Drawers.open === "hearts")
  ) {
    if (e.key === "Escape") {
      e.preventDefault();
      window.Drawers.close();
    }
    return;
  }
  const tag = (e.target && e.target.tagName) || "";
  if ((tag === "INPUT" && e.target.type !== "range") || tag === "TEXTAREA") return;
  if (tag === "SELECT") return;

  const meta = e.metaKey,
    ctrl = e.ctrlKey,
    shift = e.shiftKey;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  const digit = /^(Digit|Numpad)([1-9])$/.exec(e.code);

  if (window.Listen && window.Listen.active && !meta && !ctrl && window.Listen.onKey(e, key)) return;
  if (key === "l" && !meta && !ctrl && S.session && S.settings && S.settings.discover_in_topbar) {
    e.preventDefault();
    if (window.Listen && window.Listen.openPicker) window.Listen.openPicker();
    return;
  }

  // Undo
  if (key === "z" && ((meta && shift) || (ctrl && !meta))) {
    e.preventDefault();
    if (!e.repeat) undoAll();
    return;
  }
  if (key === "z" && !ctrl) {
    e.preventDefault();
    if (!e.repeat) undo();
    return;
  }
  if (meta && key === "f") {
    e.preventDefault();
    toggleFullscreen();
    return;
  }
  if (meta || ctrl || e.altKey) return;

  if (key === "Escape") {
    e.preventDefault();
    if (window.Drawers && window.Drawers.open) window.Drawers.close();
    else if (document.fullscreenElement) document.exitFullscreen();
    else api("exit_fullscreen").catch(() => {});
    return;
  }
  if (key === "Tab") {
    e.preventDefault();
    body.classList.toggle("focus");
    return;
  }
  if (key === "h" || key === "?") {
    e.preventDefault();
    if (window.Guide && window.Guide.toggle) window.Guide.toggle();
    return;
  }
  if (key === "q") {
    e.preventDefault();
    if (window.Drawers && window.Drawers.toggle) window.Drawers.toggle("queue");
    return;
  }

  const hasTrack = !!(S.session && S.session.current) || (window.Listen && window.Listen.active);
  if (!hasTrack) return;

  if (key === "s" || key === "t") {
    setMode("swipe");
    return;
  }
  if (key === "g") {
    setMode("grid");
    return;
  }
  if (key === "u") {
    setOrder("random");
    return;
  }
  if (key === "o") {
    setOrder("in_order");
    return;
  }
  if (key === " ") {
    e.preventDefault();
    if (!e.repeat) Player.toggle();
    return;
  }
  if (key === "[") {
    e.preventDefault();
    Player.phraseJump(false);
    return;
  }
  if (key === "]") {
    e.preventDefault();
    Player.phraseJump(true);
    return;
  }
  if (e.key === "D" || (shift && key === "d")) {
    e.preventDefault();
    Player.dropJump();
    return;
  }
  if (key === "a") {
    Player.nudge(-SEEK_STEP);
    return;
  }
  if (key === "d") {
    Player.nudge(SEEK_STEP);
    return;
  }
  if (key === "r") {
    if (!e.repeat) Player.replay();
    return;
  }

  if (digit) {
    e.preventDefault();
    const n = Number(digit[2]);
    if (S.mode === "grid") {
      if (!e.repeat) decide({ kind: "sort", slot: n - 1 });
    } else if (n === 9) Player.outro();
    else Player.jump(JUMPS[n - 1]);
    return;
  }

  const arrows = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  if (arrows[key]) {
    e.preventDefault();
    if (e.repeat) return;
    const dir = arrows[key];
    if (dir === "down") {
      decide({ kind: "skip" });
      return;
    }
    if (S.mode !== "swipe") return;
    if (dir === "up") {
      decide({ kind: "sort", slot: S.settings && S.settings.swipe_archive_hotkey === "up" ? 1 : 2 });
      return;
    }
    decide({ kind: "sort", slot: slotForDirection(dir) });
  }
}

export function initKeyboard() {
  window.addEventListener("keydown", onKey);
}

window.onKey = onKey;
window.toggleFullscreen = toggleFullscreen;
window.initKeyboard = initKeyboard;
