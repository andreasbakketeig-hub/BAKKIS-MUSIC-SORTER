/* BAKKIS MUSIC SORTER v2 — Guide & Welcome Modals */
"use strict";

export const Guide = {
  get isOpen() { return !document.getElementById("guide").hidden; },

  toggle() { this.isOpen ? this.close() : this.show(); },

  close() { document.getElementById("guide").hidden = true; },

  show() {
    const el = window.el;
    const S = window.S;
    const activePreset = window.activePreset;
    const swipeLayout = window.swipeLayout;
    const Pad = window.Pad;

    const box = document.getElementById("guide-body");
    box.textContent = "";
    const sw = activePreset ? activePreset("swipe") : null;
    const layout = swipeLayout ? swipeLayout() : [];
    const upArchive = S && S.settings && S.settings.swipe_archive_hotkey === "up";
    const keyFor = (slot) => {
      const found = layout.find((t) => t.slot === slot);
      return found ? found.glyph : "";
    };
    const discover = !!(S && S.settings && S.settings.discover_in_topbar);
    const groups = [
      ["Workflow 1: Local Sorter", [
        [keyFor(0), sw ? sw.labels[0] : "Keep"],
        [keyFor(1) + (upArchive ? " ↑" : ""), sw ? sw.labels[1] : "Archive"],
        [upArchive ? "drag ↑" : "↑", sw ? sw.labels[2] : "Favorite"],
        ["↓", "Skip"],
        ["1–9", "Grid crate tile"],
        ["S  G", "Swipe mode / Grid mode"],
      ], "Or drag the card, or flick with two fingers."],
      ["Workflow 2: Promo Reviewer", [
        ["↑", "5★ Favorite + Comment + Sync"],
        ["→", "4★ Keep + Comment + Sync"],
        ["←", "Decline / Dismiss"],
        ["↓", "Next track / Skip"],
      ], "Inflyte & Trackstack: submits feedback & downloads full-res."],
      ["Playback", [
        ["Space", "Play / pause"],
        ["1–8", "Jump points"],
        ["9", "Outro"],
        ["[  ]", "−32 / +32 beats"],
        ["⇧D", "Drop 1"],
        ["A  D", "−30 s / +30 s"],
        ["R", "Replay"],
      ]],
      ["General", [
        ["Z", "Undo last"],
        ["⇧⌘Z", "Undo whole session"],
        ...(discover ? [["L", "Discover mode"]] : []),
        ["Q", "Queue drawer"],
        ["⌘F", "Fullscreen toggle"],
        ["H", "Shortcuts guide"],
      ], discover ? "In Discover: ← → next / previous, ↑ heart." : ""],
    ];
    if (typeof Pad !== "undefined" && Pad.enabled) {
      groups.push(["Game controller", [
        ["D-pad", "Sort (or left stick)"], ["✕ / A", "Play / pause"], ["○ / B", "Undo / close"],
        ["L1 R1 / LB RB", "−30 s / +30 s"], ["Options / ☰", "Shortcuts"],
      ], "PlayStation / Xbox names. Works with PS4, PS5, Xbox, Switch Pro and most 8BitDo."]);
    }
    for (const [title, rows, foot] of groups) {
      const g = el("div", "guide-group");
      g.appendChild(el("h3", null, title));
      for (const [k, v] of rows) {
        const r = el("div", "guide-row");
        r.appendChild(el("kbd", null, k));
        r.appendChild(el("span", null, v));
        g.appendChild(r);
      }
      if (foot) g.appendChild(el("p", "guide-note", foot));
      box.appendChild(g);
    }
    const cb = document.getElementById("guide-startup");
    if (cb) cb.checked = !!(S && S.settings && S.settings.show_help_guide_on_startup);
    document.getElementById("guide").hidden = false;
  },
};

/** "Why I built this": once on first launch, and from About. */
export const Welcome = {
  get isOpen() { return !document.getElementById("welcome").hidden; },

  show() {
    document.getElementById("welcome").hidden = false;
    const go = document.getElementById("welcome-go");
    if (go) go.focus();
  },

  close() {
    const S = window.S;
    const api = window.api;
    document.getElementById("welcome").hidden = true;
    if (S && S.settings && !S.settings.welcome_seen) {
      S.settings.welcome_seen = true;
      if (api) api("set_settings", { welcome_seen: true, guide_seen: true }).then((x) => (S.settings = x)).catch(() => {});
    }
  },
};
