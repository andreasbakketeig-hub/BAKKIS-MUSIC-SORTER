/* BAKKIS MUSIC SORTER — EventBus.
 * Lightweight pub-sub event emitter for decoupled inter-module messaging.
 */
"use strict";

export class EventBus {
  constructor() {
    this.events = new Map();
  }

  on(event, handler) {
    if (!this.events.has(event)) {
      this.events.set(event, []);
    }
    this.events.get(event).push(handler);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    const list = this.events.get(event);
    if (!list) return;
    const filtered = list.filter((h) => h !== handler);
    if (filtered.length) {
      this.events.set(event, filtered);
    } else {
      this.events.delete(event);
    }
  }

  emit(event, data) {
    const list = this.events.get(event);
    if (!list) return;
    for (const handler of [...list]) {
      try {
        handler(data);
      } catch (err) {
        console.error(`EventBus error on '${event}':`, err);
      }
    }
  }

  clear() {
    this.events.clear();
  }
}

export const bus = new EventBus();
window.bus = bus;
