/* BAKKIS MUSIC SORTER — Virtual Scrolling Queue Drawer & Filter Panel */

import { api, el } from "./api.js";
import { S } from "./state.js";
import { bus } from "./bus.js";
import { toast } from "./toasts.js";
import { queueChange } from "./decisions.js";

const QUEUE_ROW_H = 40;
const QUEUE_PAGE = 100;

export const QueueDrawer = {
  query: "",
  pages: new Map(),
  total: 0,
  timer: 0,
  inflight: new Set(),
  renderedRows: new Map(), // index -> HTMLElement
  recycledPool: [],        // HTMLElement[]
  rafPending: false,

  refreshSoon() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.refresh(), 80);
  },

  async refresh() {
    this.pages.clear();
    this.renderHistory();
    FilterPanel.render();
    await this.fetchPage(0);
    this.renderList();
  },

  /** Resolves true only when a page was actually loaded (callers re-render on that). */
  async fetchPage(n) {
    if (this.pages.has(n) || this.inflight.has(n)) return false;
    this.inflight.add(n);
    try {
      const q = this.query;
      const res = await api("queue", { offset: n * QUEUE_PAGE, limit: QUEUE_PAGE, q });
      if (q !== this.query) return false; // the search changed while loading
      this.total = res.total;
      this.pages.set(n, res.items);
      return true;
    } catch (_) {
      return false; // the next refresh retries
    } finally {
      this.inflight.delete(n);
    }
  },

  renderHistory() {
    const box = document.getElementById("queue-history");
    if (!box) return;
    box.textContent = "";
    const hist = (S.session && S.session.history) || [];
    if (!hist.length) {
      box.appendChild(el("div", "q-empty", "Nothing sorted yet"));
      return;
    }
    for (const a of hist) {
      const row = el("div", "q-row q-hist");
      row.appendChild(el("span", `q-label q-${a.status}`, a.label));
      row.appendChild(el("span", "q-name", a.name));
      if (a.status === "pending") row.appendChild(el("span", "q-state", "…"));
      if (a.status === "failed") row.appendChild(el("span", "q-state err", "failed"));
      box.appendChild(row);
    }
    const hint = el("div", "q-hint", "Z undoes the most recent action");
    box.appendChild(hint);
  },

  renderList() {
    if (this.rafPending) return;
    this.rafPending = true;
    window.requestAnimationFrame(() => {
      this.rafPending = false;
      this._doRenderList();
    });
  },

  _doRenderList() {
    const scroller = document.getElementById("queue-list");
    const inner = document.getElementById("queue-inner");
    if (!scroller || !inner) return;

    const countEl = document.getElementById("queue-count");
    if (countEl) countEl.textContent = this.total ? String(this.total) : "";
    inner.style.height = `${this.total * QUEUE_ROW_H}px`;

    if (!this.total) {
      for (const row of this.renderedRows.values()) {
        row.remove();
        this.recycledPool.push(row);
      }
      this.renderedRows.clear();
      inner.textContent = "";
      inner.appendChild(el("div", "q-empty", this.query ? "No matches" : "No more tracks"));
      return;
    }

    const emptyEl = inner.querySelector(".q-empty");
    if (emptyEl) emptyEl.remove();

    const first = Math.max(0, Math.floor(scroller.scrollTop / QUEUE_ROW_H) - 5);
    const last = Math.min(this.total - 1, Math.ceil((scroller.scrollTop + scroller.clientHeight) / QUEUE_ROW_H) + 5);

    // 1. Recycle rows that scrolled out of view
    for (const [idx, row] of this.renderedRows.entries()) {
      if (idx < first || idx > last) {
        row.remove();
        this.renderedRows.delete(idx);
        this.recycledPool.push(row);
      }
    }

    // 2. Render or reuse rows in view
    const missing = new Set();
    for (let i = first; i <= last; i++) {
      if (this.renderedRows.has(i)) continue;

      const pageNum = Math.floor(i / QUEUE_PAGE);
      const page = this.pages.get(pageNum);
      if (!page) {
        missing.add(pageNum);
        continue;
      }

      const t = page[i % QUEUE_PAGE];
      if (!t) continue;

      // Borrow from pool or create once
      let row = this.recycledPool.pop();
      if (!row) {
        row = el("button", "q-row q-track");
        row.type = "button";
        row.title = "Play next";
      } else {
        row.textContent = "";
      }

      row.style.top = `${i * QUEUE_ROW_H}px`;
      row.appendChild(el("span", "q-idx", String(t.index + 1)));

      const txt = el("span", "q-text");
      txt.appendChild(el("span", "q-name", t.name.replace(/\.[^.]+$/, "")));
      if (t.folder) txt.appendChild(el("span", "q-folder", t.folder));
      row.appendChild(txt);

      if (t.bpm || t.key) row.appendChild(el("span", "q-meta", [t.bpm, t.key].filter(Boolean).join(" · ")));
      if (t.index === 0) row.appendChild(el("span", "q-state", "next"));

      row.onclick = () => this.playNext(t);

      inner.appendChild(row);
      this.renderedRows.set(i, row);
    }

    for (const n of missing) {
      this.fetchPage(n).then((loaded) => loaded && this.renderList());
    }
  },

  async playNext(t) {
    try {
      const snap = await queueChange("play_next", { track_id: t.id });
      if (window.applySession) window.applySession(snap);
      toast(`“${t.name.replace(/\.[^.]+$/, "")}” plays next`);
    } catch (e) {
      toast(e.message, { level: "error" });
    }
  },
};

// ------------------------------------------------------------------ BPM / key filter (queue drawer)

const KEY_CODES = Array.from({ length: 12 }, (_, i) => [`${i + 1}A`, `${i + 1}B`]).flat();
const KEY_NAMES = {
  "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm", "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "C#m",
  "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb", "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
};

export const FilterPanel = {
  built: false,

  build() {
    if (this.built) return;
    this.built = true;
    const sel = document.getElementById("qf-key");
    if (!sel) return;
    const any = el("option", null, "Any");
    any.value = "";
    sel.appendChild(any);
    for (const c of KEY_CODES) {
      const o = el("option", null, `${c} · ${KEY_NAMES[c]}`);
      o.value = c;
      sel.appendChild(o);
    }
    const applyBtn = document.getElementById("qf-apply");
    if (applyBtn) applyBtn.addEventListener("click", () => this.apply());
    const clearBtn = document.getElementById("qf-clear");
    if (clearBtn) clearBtn.addEventListener("click", () => this.clear());
    for (const id of ["qf-bpm-min", "qf-bpm-max"]) {
      const input = document.getElementById(id);
      if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") this.apply(); });
    }
  },

  render() {
    const box = document.getElementById("queue-filter");
    if (!box) return;
    const on = !!(S.settings && S.settings.queue_filter && S.session);
    box.hidden = !on;
    if (!on) return;
    this.build();
    const f = S.session.filter;
    const status = document.getElementById("qf-status");
    if (!status) return;
    if (f) {
      const parts = [];
      if (f.bpm_min != null || f.bpm_max != null) parts.push(`${f.bpm_min ?? "…"}–${f.bpm_max ?? "…"} BPM`);
      if (f.key) parts.push(`${f.key}${f.compatible ? " + compatible" : ""}`);
      status.textContent = `Filtered${parts.length ? ` (${parts.join(", ")})` : ""}: ${S.session.remaining - S.session.held} to go, ${S.session.held} set aside. Tracks without BPM/key tags are set aside when filtering on them.`;
    } else {
      status.textContent = "Show only tracks in a BPM range and/or key. Nothing is moved; other tracks wait until you clear the filter.";
    }
  },

  async apply() {
    const num = (id) => {
      const elNode = document.getElementById(id);
      if (!elNode) return null;
      const v = elNode.value.trim();
      return v === "" ? null : Number(v);
    };
    const keyEl = document.getElementById("qf-key");
    const compEl = document.getElementById("qf-compatible");
    const orderEl = document.getElementById("qf-order");

    const params = {
      bpm_min: num("qf-bpm-min"),
      bpm_max: num("qf-bpm-max"),
      key: (keyEl && keyEl.value) || null,
      compatible: compEl ? compEl.checked : false,
      order: (orderEl && orderEl.value) || "queue",
    };
    if (params.bpm_min != null && params.bpm_max != null && params.bpm_min > params.bpm_max) {
      [params.bpm_min, params.bpm_max] = [params.bpm_max, params.bpm_min];
    }
    try {
      const snap = await queueChange("set_filter", params);
      if (window.applySession) window.applySession(snap);
      QueueDrawer.refresh();
    } catch (e) {
      toast(e.message, { level: "error" });
    }
  },

  async clear() {
    try {
      const snap = await queueChange("clear_filter");
      if (window.applySession) window.applySession(snap);
      QueueDrawer.refresh();
    } catch (e) {
      toast(e.message, { level: "error" });
    }
  },
};

export function initQueue() {
  const scroller = document.getElementById("queue-list");
  if (scroller) {
    scroller.addEventListener("scroll", () => QueueDrawer.renderList(), { passive: true });
  }

  let searchTimer = 0;
  const search = document.getElementById("queue-search");
  if (search) {
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        QueueDrawer.query = search.value.trim();
        if (scroller) scroller.scrollTop = 0;
        QueueDrawer.refresh();
      }, 120);
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (search.value) {
          search.value = "";
          search.dispatchEvent(new Event("input"));
        } else {
          search.blur();
        }
      }
    });
  }

  bus.on("session:changed", () => {
    if (window.Drawers && window.Drawers.open === "queue") {
      QueueDrawer.refreshSoon();
    }
  });
  bus.on("queue:refresh", () => QueueDrawer.refresh());
}

// Window globals for backwards compatibility
window.QueueDrawer = QueueDrawer;
window.FilterPanel = FilterPanel;
window.initQueue = initQueue;
