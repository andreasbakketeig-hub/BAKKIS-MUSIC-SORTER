/* BAKKIS MUSIC SORTER — Waveform canvas renderer & marker overlay. */
"use strict";

import { api, audioUrl, el, fmtTime, token } from "./api.js";
import { Player, EndWatch } from "./player.js";
import { S, getMeta } from "./state.js";

const JUMPS = [0, 30, 60, 90, 120, 180, 240, 300]; // keys 1–8; key 9 = outro
const OUTRO_SEC = 60;
const WAVE_PEAKS = 800;
const WAVE_MAX_BYTES = 200 * 1024 * 1024;
const WAVE_MAX_SEC = 20 * 60;
const WAVE_DECODE_RATE = 4000;

const $ = (id) => document.getElementById(id);
const body = document.body;

export const Wave = {
  canvas: null,
  peaks: null, // Uint8Array
  peaksFor: null,
  base: null, // offscreen canvases (unplayed / played)
  played: null,
  loadingFor: null,
  dirtyMarkers: true,
  flashTimer: 0,

  init() {
    this.canvas = $("wave-canvas");
  },

  reset(id) {
    this.peaks = null;
    this.peaksFor = id;
    this.render();
    this.markersDirty();
  },

  resize() {
    if (!this.canvas) this.canvas = $("wave-canvas");
    if (!this.canvas) return;
    const r = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.render();
    this.markersDirty();
  },

  render() {
    if (!this.canvas) return;
    const w = this.canvas.width,
      h = this.canvas.height;
    const mk = (color) => {
      const c = document.createElement("canvas");
      c.width = w;
      c.height = h;
      const g = c.getContext("2d");
      g.fillStyle = color;
      if (this.peaks) {
        const n = this.peaks.length;
        const dpr = window.devicePixelRatio || 1;
        const barW = Math.max(1, Math.floor(2 * dpr));
        const gap = Math.max(1, Math.floor(dpr));
        const step = barW + gap;
        const bars = Math.floor(w / step);
        for (let i = 0; i < bars; i++) {
          const a = Math.floor((i / bars) * n),
            b = Math.max(a + 1, Math.floor(((i + 1) / bars) * n));
          let v = 0;
          for (let j = a; j < b; j++) v = Math.max(v, this.peaks[j]);
          const bh = Math.max(2 * dpr, (v / 255) * (h - 8 * dpr));
          g.fillRect(i * step, (h - bh) / 2, barW, bh);
        }
      } else {
        const lh = Math.max(2, Math.round(2 * (window.devicePixelRatio || 1)));
        g.fillRect(0, (h - lh) / 2, w, lh);
      }
      return c;
    };
    this.base = mk(token("--line-strong"));
    this.played = mk(token("--accent"));
  },

  draw() {
    if (!this.canvas) return;
    const g = this.canvas.getContext("2d");
    const w = this.canvas.width,
      h = this.canvas.height;
    g.clearRect(0, 0, w, h);
    if (!this.base) return;
    g.drawImage(this.base, 0, 0);
    const d = Player.duration;
    if (d > 0 && Player.curId) {
      const x = Math.round((Player.cur.currentTime / d) * w);
      if (x > 0) g.drawImage(this.played, 0, 0, x, h, 0, 0, x, h);
      // active start marker
      const sx = Math.round((Player.activeStart / d) * w);
      g.globalAlpha = 0.5;
      g.fillStyle = token("--accent");
      g.fillRect(sx, 0, Math.max(1, window.devicePixelRatio || 1), h);
      g.globalAlpha = 1;
      g.fillStyle = token("--ink");
      g.fillRect(x, 0, Math.max(1, window.devicePixelRatio || 1), h);
    }
  },

  markersDirty() {
    this.dirtyMarkers = true;
  },

  drawMarkers() {
    if (!this.dirtyMarkers) return;
    this.dirtyMarkers = false;
    const box = $("markers");
    if (!box) return;
    box.textContent = "";
    if (!["swipe", "grid", "listen"].includes(S.mode)) return;
    const d = Player.duration;
    const points = JUMPS.map((sec, i) => ({ sec, key: String(i + 1), label: fmtTime(sec) })).filter(
      (p) => !d || p.sec < d
    );
    if (d > OUTRO_SEC) points.push({ sec: d - OUTRO_SEC, key: "9", label: "outro", outro: true });
    const curMeta = Player.curId ? S.meta.get(Player.curId) : null;
    if (curMeta && curMeta.drop_seconds && (!d || curMeta.drop_seconds < d)) {
      points.push({ sec: curMeta.drop_seconds, key: "D", label: "drop", drop: true });
    }
    points.sort((a, b) => a.sec - b.sec);
    const showKeys = S.mode !== "grid";
    for (const p of points) {
      const b = el("button", "marker");
      b.type = "button";
      if (showKeys) b.appendChild(el("kbd", null, p.key));
      b.appendChild(el("span", "t", p.label));
      const pos = d ? p.sec / d : JUMPS.indexOf(p.sec) / (JUMPS.length - 1);
      b.style.left = `${(pos * 100).toFixed(3)}%`;
      if (Math.abs(p.sec - Player.activeStart) < 0.5) b.classList.add("active");
      b.dataset.sec = String(p.sec);
      b.dataset.pos = String(pos);
      if (p.outro) b.dataset.outro = "1";
      if (p.drop) {
        b.dataset.drop = "1";
        b.classList.add("marker-drop");
      }
      b.addEventListener("click", () =>
        p.drop ? Player.dropJump() : p.outro ? Player.outro() : Player.jump(p.sec)
      );
      box.appendChild(b);
    }
    const first = box.firstElementChild;
    if (first) first.style.transform = "translateX(0)";
    const last = box.lastElementChild;
    if (last && last !== first && Number(last.dataset.pos) > 0.92) last.style.transform = "translateX(-100%)";
    const GAP = 8;
    let prev = null;
    for (const m of box.children) {
      if (prev && m.getBoundingClientRect().left < prev.getBoundingClientRect().right + GAP) {
        if (m.dataset.outro && !prev.dataset.outro) {
          prev.hidden = true;
          prev = m;
        } else m.hidden = true;
        continue;
      }
      prev = m;
    }
  },

  flash(sec) {
    const wave = $("wave");
    if (!wave) return;
    wave.classList.add("flash");
    const m = [...document.querySelectorAll(".marker")].find((b) => Number(b.dataset.sec) === sec);
    if (m) m.classList.add("flash");
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => {
      wave.classList.remove("flash");
      if (m) m.classList.remove("flash");
    }, 350);
  },

  async loadPeaks(id) {
    if (!id || (this.peaksFor === id && this.peaks)) return;
    if (this.loadingFor === id) return;
    this.loadingFor = id;
    try {
      const cached = await api("peaks_get", { track_id: id });
      if (Player.curId !== id) return;
      if (cached) return this.setPeaks(id, Uint8Array.from(atob(cached), (c) => c.charCodeAt(0)));
      const meta = await getMeta(id).catch(() => null);
      if (!meta || meta.pcm || meta.size > WAVE_MAX_BYTES || (meta.duration && meta.duration > WAVE_MAX_SEC))
        return;
      await new Promise((r) => setTimeout(r, 300));
      if (Player.curId !== id) return;
      const buf = await (await fetch(audioUrl(id))).arrayBuffer();
      if (Player.curId !== id) return;
      const audio = await new OfflineAudioContext(1, 1, WAVE_DECODE_RATE).decodeAudioData(buf);
      if (Player.curId !== id) return;
      const peaks = computePeaks(audio, WAVE_PEAKS);
      this.setPeaks(id, peaks);
      let bin = "";
      for (let i = 0; i < peaks.length; i++) bin += String.fromCharCode(peaks[i]);
      api("peaks_put", { track_id: id, peaks: btoa(bin) }).catch(() => {});
    } catch (_) {
      /* no waveform: the flat progress line stays */
    } finally {
      if (this.loadingFor === id) this.loadingFor = null;
    }
  },

  setPeaks(id, peaks) {
    if (Player.curId !== id) return;
    this.peaks = peaks;
    this.peaksFor = id;
    this.render();
  },
};

export function computePeaks(audio, n) {
  const chans = [];
  for (let c = 0; c < Math.min(2, audio.numberOfChannels); c++) chans.push(audio.getChannelData(c));
  const len = chans[0].length;
  const per = Math.max(1, Math.floor(len / n));
  const stride = Math.max(1, Math.floor(per / 256));
  const out = new Float32Array(n);
  let max = 0;
  for (let i = 0; i < n; i++) {
    const a = i * per,
      b = Math.min(len, a + per);
    let v = 0;
    for (const ch of chans)
      for (let j = a; j < b; j += stride) {
        const s = Math.abs(ch[j]);
        if (s > v) v = s;
      }
    out[i] = v;
    if (v > max) max = v;
  }
  const res = new Uint8Array(n);
  for (let i = 0; i < n; i++) res[i] = max ? Math.round((out[i] / max) * 255) : 0;
  return res;
}

export function startAnimationLoop() {
  function frame() {
    if (window.Pad && window.Pad.tick) window.Pad.tick();
    if (body.classList.contains("is-player")) {
      Wave.draw();
      Wave.drawMarkers();
      EndWatch.tick();
      if (window.Listen && window.Listen.tick) window.Listen.tick();
      const t = Player.cur.currentTime || 0;
      const txt = `${fmtTime(t)} / ${fmtTime(Player.duration)}`;
      const timeEl = $("time");
      if (timeEl && timeEl.textContent !== txt) timeEl.textContent = txt;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// Global attachments
window.Wave = Wave;
window.computePeaks = computePeaks;
