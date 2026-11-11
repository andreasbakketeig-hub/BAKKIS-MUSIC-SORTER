/* BAKKIS MUSIC SORTER — Toast notifications & modal dialogs. */
"use strict";

import { el } from "./api.js";
import { bus } from "./bus.js";

const $ = (id) => document.getElementById(id);

export function toast(message, { level = "info", undo: withUndo = false, ms, action } = {}) {
  const box = $("toasts");
  if (!box) return;
  const t = el("div", `toast ${level}`);
  t.appendChild(el("span", "msg", message));
  if (withUndo) {
    action = { label: "Undo", fn: () => bus.emit("action:undo") };
    for (const old of box.querySelectorAll(".toast .undo-btn")) old.remove();
  }
  if (action) {
    const b = el("button", withUndo ? "undo-btn" : null, action.label);
    b.type = "button";
    b.addEventListener("click", () => {
      t.remove();
      action.fn();
    });
    t.appendChild(b);
  }
  box.appendChild(t);
  while (box.children.length > 3) box.firstElementChild.remove();
  const life = ms || (level === "error" ? 6000 : 3000);
  setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => t.remove(), 220);
  }, life);
}

export function clearUndoToasts() {
  for (const b of document.querySelectorAll("#toasts .undo-btn")) b.remove();
}

let modalQueue = Promise.resolve();

export function modal(opts) {
  const run = modalQueue.then(() => showModal(opts));
  modalQueue = run.catch(() => {});
  return run;
}

function showModal({ title, body: text, buttons }) {
  $("modal-title").textContent = title;
  $("modal-body").textContent = text;
  const row = $("modal-buttons");
  row.textContent = "";
  return new Promise((resolve) => {
    const close = (v) => {
      $("modal").hidden = true;
      resolve(v);
    };
    buttons.forEach((b) => {
      const btn = el("button", b.danger ? "danger-btn" : b.primary ? "primary-btn" : "ghost-btn", b.label);
      btn.type = "button";
      btn.addEventListener("click", () => close(b.value));
      row.appendChild(btn);
    });
    $("modal").hidden = false;
    const def = row.querySelector(".primary-btn, .danger-btn");
    if (def) def.focus();
    modal.cancel = () => close(buttons[0].value);
    modal.confirm = () => {
      const focused = row.contains(document.activeElement) ? [...row.children].indexOf(document.activeElement) : -1;
      const d =
        focused >= 0 ? buttons[focused] : buttons.find((b) => b.primary || b.danger) || buttons[buttons.length - 1];
      close(d.value);
    };
  });
}

export const modalOpen = () => {
  const m = $("modal");
  return m && !m.hidden;
};

// Global attachments
window.toast = toast;
window.clearUndoToasts = clearUndoToasts;
window.modal = modal;
window.modalOpen = modalOpen;
