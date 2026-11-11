/* BAKKIS MUSIC SORTER v2 — History Drawer (Past Sessions) */
"use strict";

export const HistoryDrawer = {
  SHOW: 40,
  expanded: new Set(),

  async render() {
    const el = window.el;
    const api = window.api;
    const body = document.getElementById("history-body");
    body.textContent = "";
    body.appendChild(el("p", "muted small", "Everything sorted in the last 90 days that is still in place. Put tracks back where they came from; nothing is ever overwritten."));
    let sessions = [];
    try { sessions = await api("history"); } catch (e) { body.appendChild(el("p", "warn small", e.message)); return; }
    if (!sessions.length) { body.appendChild(el("div", "q-empty", "Nothing sorted yet")); return; }
    for (const sess of sessions) body.appendChild(this.renderSession(sess));
  },

  renderSession(sess) {
    const el = window.el;
    const copyFeedback = window.copyFeedback;
    const box = el("div", "hist-session");
    const head = el("div", "hist-head");
    const when = sess.started ? new Date(sess.started).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : sess.id;
    const folder = (sess.root || "").split("/").filter(Boolean).pop() || "";
    const title = el("div", "hist-title");
    title.appendChild(el("span", null, when));
    title.appendChild(el("span", "muted small", `${folder ? folder + " · " : ""}${sess.items.length} track${sess.items.length === 1 ? "" : "s"}`));
    head.appendChild(title);
    const actions = el("div", "hist-actions");
    const fb = el("button", "ghost-btn small-btn", "Copy feedback");
    fb.type = "button";
    fb.title = "Copy a summary of this session's decisions";
    if (copyFeedback) fb.addEventListener("click", () => copyFeedback(sess.id));
    actions.appendChild(fb);
    head.appendChild(actions);
    const restorable = sess.items.filter((i) => i.status === "sorted");
    if (restorable.length) {
      const all = el("button", "ghost-btn small-btn", `Put back all (${restorable.length})`);
      all.type = "button";
      all.addEventListener("click", () => this.putBack(restorable, `Put back ${restorable.length} tracks from ${when}?`));
      actions.appendChild(all);
    }
    box.appendChild(head);
    const list = el("div", "hist-items");
    const open = this.expanded.has(sess.id);
    const shown = open ? sess.items : sess.items.slice(0, this.SHOW);
    for (const it of shown) {
      const row = el("div", "q-row q-hist");
      row.appendChild(el("span", `q-label${it.op === "copy" ? " copy" : ""}`, it.label || ""));
      const name = el("span", "q-name", it.name);
      name.title = it.dest;
      row.appendChild(name);
      if (it.status === "sorted") {
        const b = el("button", "link-btn", it.op === "copy" ? "Remove copy" : "Put back");
        b.type = "button";
        b.addEventListener("click", () => this.putBack([it]));
        row.appendChild(b);
      } else {
        row.appendChild(el("span", "q-state", it.status === "current" ? "this session · Z" : "moved since"));
      }
      list.appendChild(row);
    }
    if (sess.items.length > shown.length) {
      const more = el("button", "link-btn", `Show all ${sess.items.length}`);
      more.type = "button";
      more.addEventListener("click", () => { this.expanded.add(sess.id); this.render(); });
      list.appendChild(more);
    }
    box.appendChild(list);
    return box;
  },

  async putBack(items, confirmText) {
    const modal = window.modal;
    const api = window.api;
    const toast = window.toast;
    const S = window.S;
    const refreshState = window.refreshState;
    const refreshCountsSoon = window.refreshCountsSoon;

    if (confirmText) {
      const ok = await modal({
        title: confirmText,
        body: "Moved tracks go back to their original folders (with a new name if the old one is taken). Copies are deleted; originals are untouched.",
        buttons: [{ label: "Cancel", value: false }, { label: "Put back", value: true, primary: true }],
      });
      if (!ok) return;
    }
    try {
      const res = await api("history_restore", { entry_ids: items.map((i) => i.id) });
      const ok = res.filter((r) => r.ok).length;
      const failed = res.filter((r) => !r.ok);
      if (ok) toast(ok === 1 ? res.find((r) => r.ok).message : `Put back ${ok} tracks`);
      for (const f of failed.slice(0, 3)) toast(f.message, { level: "warning", ms: 6000 });
      if (S && S.session && refreshState) await refreshState();
      if (refreshCountsSoon) refreshCountsSoon(0);
      this.render();
    } catch (e) { toast(e.message, { level: "error" }); }
  },
};
