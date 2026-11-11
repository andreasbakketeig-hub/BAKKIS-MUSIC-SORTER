/* BAKKIS MUSIC SORTER v2 — Promo History Drawer (Past Reviews) */
"use strict";

export const PromoHistoryDrawer = {
  reviews: [],
  filter: "all",
  search: "",

  async render() {
    const el = window.el;
    const api = window.api;
    const body = document.getElementById("promo-history-body");
    if (!body) return;
    body.textContent = "";
    const loading = el("p", "muted small", "Loading promo reviews from encrypted database…");
    body.appendChild(loading);

    try {
      const res = await api("promo_history", { limit: 100, offset: 0 });
      this.reviews = (res && res.reviews) || [];
      this.bindControls();
      this.renderList();
    } catch (e) {
      body.textContent = "";
      body.appendChild(el("p", "warn small", `Failed to load promo history: ${e.message}`));
    }
  },

  bindControls() {
    const searchInput = document.getElementById("promo-hist-search");
    if (searchInput && !searchInput.dataset.bound) {
      searchInput.dataset.bound = "true";
      searchInput.addEventListener("input", (e) => {
        this.search = e.target.value.trim().toLowerCase();
        this.renderList();
      });
    }

    const filterBtns = document.querySelectorAll(".promo-filter-btn");
    for (const btn of filterBtns) {
      if (!btn.dataset.bound) {
        btn.dataset.bound = "true";
        btn.addEventListener("click", () => {
          for (const b of filterBtns) b.classList.remove("active");
          btn.classList.add("active");
          this.filter = btn.dataset.filter || "all";
          this.renderList();
        });
      }
    }
  },

  renderList() {
    const el = window.el;
    const body = document.getElementById("promo-history-body");
    if (!body) return;
    body.textContent = "";

    let list = this.reviews;

    if (this.filter === "inflyte") {
      list = list.filter((r) => r.source === "inflyte");
    } else if (this.filter === "trackstack") {
      list = list.filter((r) => r.source === "trackstack");
    } else if (this.filter === "favorites") {
      list = list.filter((r) => r.decision === "rate_5" || r.rating === 5);
    } else if (this.filter === "keeps") {
      list = list.filter((r) => r.decision === "rate_4" || r.rating === 4);
    } else if (this.filter === "dismissed") {
      list = list.filter((r) => r.decision === "dismiss" || r.decision === "decline");
    }

    if (this.search) {
      list = list.filter((r) => {
        const text = `${r.promo_name || ""} ${r.artist || ""} ${r.title || ""} ${r.comment || ""} ${r.source || ""}`.toLowerCase();
        return text.includes(this.search);
      });
    }

    if (!list.length) {
      body.appendChild(el("div", "q-empty", this.reviews.length ? "No matching reviews found" : "No promo reviews recorded yet"));
      return;
    }

    for (const r of list) {
      body.appendChild(this.renderCard(r));
    }
  },

  renderCard(r) {
    const el = window.el;
    const card = el("div", "promo-hist-card");
    const head = el("div", "promo-hist-head");

    const src = el("span", `promo-hist-source ${r.source}`, r.source === "trackstack" ? "Trackstack" : "Inflyte");
    head.appendChild(src);

    const dec = el("span", "promo-hist-decision");
    if (r.decision === "rate_5" || r.rating === 5) {
      dec.className += " fav";
      dec.textContent = "⭐⭐⭐⭐⭐ 5/5";
    } else if (r.decision === "rate_4" || r.rating === 4) {
      dec.className += " keep";
      dec.textContent = "⭐⭐⭐⭐ 4/5";
    } else if (r.decision === "dismiss" || r.decision === "decline") {
      dec.className += " dismiss";
      dec.textContent = "👎 Dismissed";
    } else {
      dec.textContent = r.decision;
    }
    head.appendChild(dec);

    const when = r.reviewed_at
      ? new Date(r.reviewed_at * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
      : "";
    const timeSpan = el("span", "promo-hist-time", when);
    head.appendChild(timeSpan);
    card.appendChild(head);

    const trackTitle = r.title || r.promo_name || "Untitled Track";
    card.appendChild(el("div", "promo-hist-track", trackTitle));
    if (r.artist || (r.promo_name && r.title && r.promo_name !== r.title)) {
      const art = r.artist ? `${r.artist}${r.promo_name && r.promo_name !== r.title ? ` · ${r.promo_name}` : ""}` : r.promo_name;
      card.appendChild(el("div", "promo-hist-artist", art));
    }

    if (r.comment) {
      const c = el("div", "promo-hist-comment", `“${r.comment}”`);
      card.appendChild(c);
    }

    const del = el("div", "promo-hist-delivery");
    if (r.source === "trackstack") {
      const dlCount = (r.downloaded && r.downloaded.length) || 0;
      del.textContent = dlCount > 0 ? `✓ Downloaded ${dlCount} track(s) to ~/Music/Inflyte Music` : "✓ Accepted & moved to Downloaded";
    } else {
      del.textContent = r.decision === "dismiss" ? "Inbox promo dismissed" : "⚡ Auto-synced to Inflyte Dropbox";
    }
    card.appendChild(del);

    return card;
  },
};
