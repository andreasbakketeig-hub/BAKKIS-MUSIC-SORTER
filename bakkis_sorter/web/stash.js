/* BAKKIS MUSIC SORTER — Offline Flight Stash modal & sync engine. */
"use strict";

import { api } from "./api.js";
import { bus } from "./bus.js";

const $ = (id) => document.getElementById(id);

export const StashModal = {
  get modal() {
    return $("stash-modal");
  },
  get isOpen() {
    return this.modal && !this.modal.hidden;
  },

  open() {
    if (!this.modal) return;
    this.modal.hidden = false;
    const progressBox = $("stash-progress-box");
    if (progressBox) progressBox.hidden = true;
    const startBtn = $("btn-start-stash");
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = "Start Stash";
    }
  },

  close() {
    if (this.modal) this.modal.hidden = true;
  },

  async start() {
    const sel = document.querySelector('input[name="stash_count"]:checked');
    const count = sel ? Number(sel.value) : 50;
    const startBtn = $("btn-start-stash");
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.textContent = "Stashing…";
    }
    const progressBox = $("stash-progress-box");
    if (progressBox) progressBox.hidden = false;
    const statusText = $("stash-status-text");
    if (statusText) statusText.textContent = `Stashing up to ${count} promos for offline use…`;
    const progressFill = $("stash-progress-fill");
    if (progressFill) progressFill.style.width = "5%";

    try {
      await api("stash_promos", { count });
      this.pollProgress();
    } catch (err) {
      if (window.toast) window.toast(`Stash error: ${err.message}`, { level: "error" });
      if (startBtn) {
        startBtn.disabled = false;
        startBtn.textContent = "Start Stash";
      }
    }
  },

  async pollProgress() {
    try {
      const st = await api("stash_status");
      if (st.target_count > 0) {
        const pct = Math.min(100, Math.round((st.completed_promos / st.target_count) * 100));
        const fill = $("stash-progress-fill");
        if (fill) fill.style.width = `${pct}%`;
        const txt = $("stash-status-text");
        if (txt) txt.textContent = st.message || `Stashed ${st.completed_promos}/${st.target_count} promos`;
      }
      if (st.status === "stashing") {
        setTimeout(() => this.pollProgress(), 600);
      } else {
        const startBtn = $("btn-start-stash");
        if (startBtn) {
          startBtn.textContent = "Done";
          startBtn.disabled = false;
          startBtn.onclick = () => this.close();
        }
        bus.emit("stash:completed", st);
      }
    } catch (_) {
      setTimeout(() => this.pollProgress(), 1000);
    }
  },
};

export async function syncOfflineReviews() {
  const btn = $("btn-sync-offline");
  if (!btn) return;
  btn.disabled = true;
  const lbl = $("lbl-sync-offline");
  if (lbl) lbl.textContent = "Syncing…";
  try {
    const res = await api("sync_offline_reviews");
    if (res.synced > 0) {
      if (window.toast) window.toast(`Synced ${res.synced} offline review(s)!`, { level: "info" });
    } else {
      if (window.toast) window.toast("No reviews pending sync.", { level: "info" });
    }
    bus.emit("stash:synced", res);
  } catch (err) {
    if (window.toast) window.toast(`Sync failed: ${err.message}`, { level: "error" });
  } finally {
    btn.disabled = false;
  }
}

export function initStashModal() {
  const openBtn = $("btn-stash-flight");
  if (openBtn) openBtn.addEventListener("click", () => StashModal.open());
  const cancelBtn = $("btn-cancel-stash");
  if (cancelBtn) cancelBtn.addEventListener("click", () => StashModal.close());
  const startBtn = $("btn-start-stash");
  if (startBtn) startBtn.addEventListener("click", () => StashModal.start());
  const syncBtn = $("btn-sync-offline");
  if (syncBtn) syncBtn.addEventListener("click", () => syncOfflineReviews());
}

window.StashModal = StashModal;
window.syncOfflineReviews = syncOfflineReviews;
