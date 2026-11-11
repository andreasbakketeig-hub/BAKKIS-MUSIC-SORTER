/* BAKKIS MUSIC SORTER — Card rendering, metadata tags, and fly-out animations. */
"use strict";

import { api, coverUrl, el, fmtTime } from "./api.js";
import { Player } from "./player.js";
import { S, getMeta, trackDisplay } from "./state.js";
import { Wave } from "./wave.js";

const $ = (id) => document.getElementById(id);
const body = document.body;

const FLY_MS = 180;
const FLY_GRID_MS = 160;
const reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

export function swipeLayout() {
  const keepRight = !S.settings || S.settings.swipe_keep_direction !== "left";
  const upArchive = S.settings && S.settings.swipe_archive_hotkey === "up";
  const keep = { slot: 0, dir: keepRight ? "right" : "left" };
  const archive = { slot: 1, dir: keepRight ? "left" : "right" };
  const glyph = { left: "←", right: "→" };
  const tiles = [
    {
      slot: archive.slot,
      glyph: glyph[archive.dir],
      key: glyph[archive.dir] + (upArchive ? " ↑" : ""),
      dir: archive.dir,
    },
    { slot: "skip", glyph: "↓", key: "↓", dir: "down" },
    { slot: 2, glyph: "↑", key: upArchive ? "swipe ↑" : "↑", dir: "up" },
    { slot: keep.slot, glyph: glyph[keep.dir], key: glyph[keep.dir], dir: keep.dir },
  ];
  return tiles.sort(
    (a, b) => ["left", "down", "up", "right"].indexOf(a.dir) - ["left", "down", "up", "right"].indexOf(b.dir)
  );
}

export function slotForDirection(dir) {
  const t = swipeLayout().find((x) => x.dir === dir);
  return t ? t.slot : null;
}

export function flyDirection(kind, slot) {
  if (S.mode === "grid") return "grid";
  if (kind === "skip") return "down";
  const t = swipeLayout().find((x) => x.slot === slot);
  return t ? t.dir : "right";
}

export function flyOutCard(dir) {
  const card = $("card");
  if (!card) return;
  const from = card.style.transform || "";
  card.classList.remove("springing");
  card.style.transform = "";
  const dragHint = $("drag-hint");
  if (dragHint) dragHint.classList.remove("show");
  if (reduceMotion.matches || !body.classList.contains("is-player") || !card.animate) return;
  if (S.ghost) S.ghost.remove();
  const r = card.getBoundingClientRect();
  const g = card.cloneNode(true);
  g.removeAttribute("id");
  for (const n of g.querySelectorAll("[id]")) n.removeAttribute("id");
  g.classList.add("card-ghost");
  Object.assign(g.style, {
    left: `${r.left}px`,
    top: `${r.top}px`,
    width: `${r.width}px`,
    height: `${r.height}px`,
    transform: from,
  });
  document.body.appendChild(g);
  const D = 150;
  const [dx, dy, rot] = {
    right: [D, 0, 5],
    left: [-D, 0, -5],
    up: [0, -D * 0.7, 0],
    down: [0, D * 0.6, 0],
    grid: [0, 48, 0],
  }[dir];
  const anim = g.animate(
    [
      { transform: `${from} translate(0, 0) rotate(0deg)`, opacity: 0.95 },
      { transform: `${from} translate(${dx}px, ${dy}px) rotate(${rot}deg)`, opacity: 0 },
    ],
    { duration: dir === "grid" ? FLY_GRID_MS : FLY_MS, easing: "cubic-bezier(.25,.7,.35,1)" }
  );
  S.ghost = g;
  S.ghostCount = (S.ghostCount || 0) + 1;
  const done = () => {
    g.remove();
    if (S.ghost === g) S.ghost = null;
  };
  anim.onfinish = done;
  anim.oncancel = done;
}

export function cardEnter() {
  const card = $("card");
  if (!card || reduceMotion.matches || !card.animate) return;
  card.animate(
    [
      { opacity: 0.35, transform: "scale(0.985)" },
      { opacity: 1, transform: "scale(1)" },
    ],
    { duration: 140, easing: "ease-out" }
  );
}

export async function renderCard() {
  const cur = S.session && S.session.current;
  if (!cur) return;
  const id = cur.id;
  const cached = S.meta.get(id);
  fillCard(cur, cached);
  if (!cached) {
    try {
      const m = await getMeta(id);
      if (S.session && S.session.current && S.session.current.id === id) {
        fillCard(cur, m);
        Wave.markersDirty();
      }
    } catch (_) {
      /* keep the file-name view */
    }
  }
}

export function fillCard(track, meta) {
  const { title, artist } = trackDisplay(track, meta);
  const t = $("title");
  if (!t) return;
  t.textContent = title;
  t.dataset.text = title;
  t.classList.remove("marquee");
  const artistEl = $("artist");
  if (artistEl) artistEl.textContent = artist;
  const cover = $("cover");
  const letter = ((artist || title || "").match(/\p{L}/u) || ["♪"])[0];
  const coverFallback = $("cover-fallback");
  if (coverFallback) coverFallback.textContent = letter.toUpperCase();
  const img = $("cover-img");
  if (meta && meta.has_cover && img && cover) {
    if (img.dataset.id !== track.id) {
      cover.classList.remove("has-img");
      img.onload = () => img.dataset.id === track.id && cover.classList.add("has-img");
      img.dataset.id = track.id;
      img.src = coverUrl(track.id);
    }
  } else if (img && cover) {
    img.dataset.id = "";
    img.removeAttribute("src");
    cover.classList.remove("has-img");
  }
  const chips = $("chips");
  if (chips) {
    chips.textContent = "";
    const chip = (label, value) => {
      const c = el("span", "chip");
      if (label) c.appendChild(document.createTextNode(label + " "));
      c.appendChild(el("b", null, value));
      chips.appendChild(c);
    };
    if (S.session && S.session.is_inflyte) {
      const src = (track && track.source) || (meta && meta.source) || "inflyte";
      const badge = el("span", `chip chip-${src}`, src === "trackstack" ? "TRACKSTACK" : "INFLYTE");
      chips.appendChild(badge);
    }
    if (meta && meta.duplicate) {
      const c = el("span", "chip chip-warn", `Already in ${meta.duplicate.folder}`);
      c.title = meta.duplicate.path;
      chips.appendChild(c);
    }
    const dupRev = (track && track.duplicate_review) || (meta && meta.duplicate_review);
    if (dupRev) {
      let text = "Previously Reviewed";
      if (dupRev.rating) {
        text = `⚠️ Previously Rated ${dupRev.rating}/5`;
      } else if (dupRev.decision === "dismiss" || dupRev.decision === "decline") {
        text = `⚠️ Previously Dismissed`;
      }
      const daysAgo = dupRev.reviewed_at
        ? Math.max(0, Math.round((Date.now() / 1000 - dupRev.reviewed_at) / 86400))
        : null;
      const timeLabel = daysAgo !== null ? (daysAgo === 0 ? "today" : `${daysAgo}d ago`) : "";
      const chipText = timeLabel ? `${text} (${timeLabel})` : text;
      const c = el("span", "chip chip-warn chip-duplicate-review", chipText);
      c.title = `Promo: ${dupRev.promo_name || "Previous submission"} · Decision: ${dupRev.decision}${
        dupRev.rating ? ` (${dupRev.rating}/5)` : ""
      }`;
      chips.appendChild(c);
    }
    const displayBpm = (meta && meta.bpm) || (meta && meta.detected_bpm ? String(meta.detected_bpm) : null);
    if (displayBpm) chip("BPM", displayBpm);
    if (meta && meta.camelot) {
      const c = el("span", "chip chip-camelot");
      c.appendChild(document.createTextNode("Camelot "));
      c.appendChild(el("b", null, meta.camelot + (meta.key && meta.key !== meta.camelot ? ` (${meta.key})` : "")));
      chips.appendChild(c);
    } else if (meta && meta.key) {
      chip("Key", meta.key);
    }
    if (meta && meta.drop_seconds) {
      const c = el("span", "chip chip-drop");
      c.appendChild(document.createTextNode("Drop 1 "));
      c.appendChild(el("b", null, fmtTime(meta.drop_seconds)));
      c.title = `Jump to Drop 1 (${fmtTime(meta.drop_seconds)}) [⇧D]`;
      c.addEventListener("click", () => Player.dropJump());
      chips.appendChild(c);
    }
    if (meta && meta.duration) chip("", fmtTime(meta.duration));
    chip("", track.name.split(".").pop().toUpperCase());
  }
  renderCardPath(track);
  requestAnimationFrame(() => {
    const wrap = t.parentElement;
    if (wrap && t.scrollWidth > wrap.clientWidth + 2) {
      const shift = t.scrollWidth + 96;
      t.classList.add("marquee");
      t.style.setProperty("--marquee-shift", `-${shift}px`);
      t.style.setProperty("--marquee-dur", `${Math.max(8, shift / 60)}s`);
    }
  });
}

export function describeFolder(path) {
  const parts = String(path || "")
    .split("/")
    .filter(Boolean);
  const name = parts.pop() || path || "";
  let where = "/" + parts.join("/");
  let disk = null;
  if (parts[0] === "Volumes" && parts[1]) {
    disk = parts[1];
    where = parts.length > 2 ? `on ${disk} · ${parts.slice(2).join("/")}` : `on ${disk}`;
  } else if (S.home && where === S.home) {
    where = "~";
  } else if (S.home && where.startsWith(S.home + "/")) {
    where = "~" + where.slice(S.home.length);
  }
  return { name, where, disk };
}

export function missingFolderMessage(path) {
  const f = describeFolder(path);
  if (f.disk)
    return `Your default folder “${f.name}” is on the disk “${f.disk}”. Is it connected? Or choose another folder.`;
  return `Your default folder “${f.name}” (${f.where}) was not found. Choose a folder, or change it in Settings.`;
}

export function renderCardPath(track) {
  const box = $("card-path");
  if (!box) return;
  const root = (S.session && S.session.root) || "";
  const rel = track.rel || track.name;
  const rootName =
    root
      .split("/")
      .filter(Boolean)
      .pop() || "";
  box.textContent = "";
  box.appendChild(el("span", "card-path-icon", "📁"));
  [rootName, ...rel.split("/")].filter(Boolean).forEach((part, i, all) => {
    if (i) box.appendChild(el("span", "card-path-sep", "›"));
    box.appendChild(el("span", i === all.length - 1 ? "card-path-file" : null, part));
  });
  box.title = `${root}/${rel}\nClick to show in Finder`;
  box.onclick = (e) => {
    e.stopPropagation();
    api("reveal_track", { track_id: track.id }).catch((err) => {
      if (window.toast) window.toast(err.message, { level: "error" });
    });
  };
  box.onpointerdown = (e) => e.stopPropagation();
}

window.renderCard = renderCard;
window.fillCard = fillCard;
window.flyOutCard = flyOutCard;
window.cardEnter = cardEnter;
window.swipeLayout = swipeLayout;
window.slotForDirection = slotForDirection;
