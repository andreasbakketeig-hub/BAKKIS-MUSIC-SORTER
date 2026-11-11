/* BAKKIS MUSIC SORTER — Mouse drag, trackpad flick, and dropzone gestures. */
"use strict";

import { fmtTime } from "./api.js";
import { slotForDirection } from "./cards.js";
import { decide } from "./decisions.js";
import { Player } from "./player.js";
import { S, activePreset } from "./state.js";
import { modalOpen, toast } from "./toasts.js";

const DRAG_THRESHOLD = 38;
const $ = (id) => document.getElementById(id);

export function gestureTarget(dir) {
  const sw = activePreset("swipe");
  if (dir === "down") return { label: "Skip", color: "#8A8F98", action: { kind: "skip" } };
  const slot = dir === "up" ? 2 : slotForDirection(dir);
  return { label: sw ? sw.labels[slot] : `Slot ${slot}`, color: sw ? sw.colors[slot] : "#8A8F98", action: { kind: "sort", slot } };
}

export function setupDrag() {
  const card = $("card");
  const hint = $("drag-hint");
  if (!card || !hint) return;
  let start = null;

  const dirOf = (dx, dy) => {
    if (Math.max(Math.abs(dx), Math.abs(dy)) < DRAG_THRESHOLD) return null;
    return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
  };

  card.addEventListener("pointerdown", (e) => {
    if (S.mode !== "swipe" || e.button !== 0) return;
    start = { x: e.clientX, y: e.clientY };
    card.setPointerCapture(e.pointerId);
    card.classList.add("dragging");
    card.classList.remove("springing");
  });

  card.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x,
      dy = e.clientY - start.y;
    card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 40}deg)`;
    const dir = dirOf(dx, dy);
    if (dir) {
      const t = gestureTarget(dir);
      hint.textContent = t.label;
      hint.style.background = t.color + "33";
      hint.style.color = t.color;
      hint.classList.add("show");
    } else hint.classList.remove("show");
  });

  const end = (e) => {
    if (!start) return;
    const dir = dirOf(e.clientX - start.x, e.clientY - start.y);
    start = null;
    card.classList.remove("dragging");
    const springBack = () => {
      card.classList.add("springing");
      card.style.transform = "";
      hint.classList.remove("show");
    };
    if (!dir || e.type !== "pointerup") {
      springBack();
      return;
    }
    decide(gestureTarget(dir).action).finally(() => {
      if (card.style.transform) springBack();
    });
  };

  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

export function setupTrackpadFlick() {
  let accX = 0,
    accY = 0,
    lastT = 0,
    lockedUntil = 0;
  const main = $("main");
  if (!main) return;
  main.addEventListener(
    "wheel",
    (e) => {
      if (S.mode !== "swipe" || !S.session || !S.session.current || modalOpen()) return;
      const vertical = Math.abs(e.deltaY) > Math.abs(e.deltaX);
      if (vertical && e.wheelDeltaY && e.wheelDeltaY % 120 === 0) return;
      if (vertical && main.scrollHeight > main.clientHeight + 4) return;
      e.preventDefault();
      const now = performance.now();
      if (now < lockedUntil) {
        lockedUntil = now + 200;
        return;
      }
      if (now - lastT > 250) accX = accY = 0;
      lastT = now;
      const sign = e.webkitDirectionInvertedFromDevice !== false ? 1 : -1;
      if (vertical) accY += sign * e.deltaY;
      else accX += sign * e.deltaX;
      let dir = null;
      if (Math.abs(accX) > 140) dir = accX < 0 ? "right" : "left";
      else if (Math.abs(accY) > 160) dir = accY < 0 ? "down" : "up";
      if (!dir) return;
      accX = accY = 0;
      lockedUntil = now + 700;
      decide(gestureTarget(dir).action);
    },
    { passive: false }
  );
}

export function setupWaveSeek() {
  const wave = $("wave");
  const hover = $("wave-hover");
  if (!wave || !hover) return;
  const secAt = (e) => {
    const r = wave.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * Player.duration;
  };
  wave.addEventListener("click", (e) => Player.duration && Player.seek(secAt(e)));
  wave.addEventListener("mousemove", (e) => {
    if (!Player.duration) return;
    const r = wave.getBoundingClientRect();
    hover.hidden = false;
    hover.style.left = `${e.clientX - r.left}px`;
    hover.textContent = fmtTime(secAt(e));
  });
  wave.addEventListener("mouseleave", () => {
    hover.hidden = true;
  });
}

export function setupDrop() {
  const overlay = $("drop-overlay");
  if (!overlay) return;
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    depth++;
    overlay.hidden = false;
  });
  window.addEventListener("dragleave", () => {
    if (--depth <= 0) {
      depth = 0;
      overlay.hidden = true;
    }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    if (window.pywebview) return;
    toast("Drag and drop works in the app window. Use “Choose music folder” here.", { level: "warning" });
  });
}

export function initGestures() {
  setupDrag();
  setupTrackpadFlick();
  setupWaveSeek();
  setupDrop();
}

window.initGestures = initGestures;
