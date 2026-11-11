/* BAKKIS MUSIC SORTER — API client & URL/DOM utilities. */
"use strict";

export const TOKEN = new URLSearchParams(location.search).get("t") || "";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(method, params = {}) {
  const res = await fetch(`/api/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Bakkis-Token": TOKEN },
    body: JSON.stringify(params),
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* non-JSON error body */
  }
  if (!res.ok) {
    throw new ApiError((data && data.error) || res.statusText, res.status);
  }
  return data.result;
}

export const audioUrl = (id) => `/audio/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;
export const coverUrl = (id) => `/cover/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;

export function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const tokenCache = {};
export const token = (name) =>
  (tokenCache[name] ??= getComputedStyle(document.documentElement).getPropertyValue(name).trim());

// Global attachments for companion scripts (panels.js, listen.js)
window.TOKEN = TOKEN;
window.api = api;
window.ApiError = ApiError;
window.audioUrl = audioUrl;
window.coverUrl = coverUrl;
window.fmtTime = fmtTime;
window.el = el;
window.token = token;
