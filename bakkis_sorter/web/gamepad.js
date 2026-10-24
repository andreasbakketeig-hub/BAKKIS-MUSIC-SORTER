/* BAKKIS MUSIC SORTER v2 — game controllers (Xbox, PlayStation, 8BitDo, …) as a remote.
 *
 * Each button press becomes the matching key press, so every mode, setting and dialog behaves exactly as
 * with the keyboard. Read once per frame from the existing render loop; costs nothing without a controller.
 * Browsers only expose a controller after its first button press. Input only counts while this window is
 * active, so a controller used for a game elsewhere never sorts anything. Sorting rumbles the controller.
 */

const Pad = {
  STICK_ON: 0.6, // how far the stick must move to count as a press
  STICK_OFF: 0.3, // and how far back before it can press again
  prev: new Map(), // controller index -> previous {buttons, dirs}
  lastPad: null, // index of the controller used last, for rumble
  lastInputAt: 0, // rumble only follows controller input, not keyboard sorting

  get enabled() { return !S.settings || S.settings.game_controller !== false; },

  /** Standard layout: 0 A/✕, 1 B/○, 4 LB/L1, 5 RB/R1, 9 Start/Options, 12–15 D-pad. */
  keyFor(button) {
    const dialog = modalOpen() || Guide.isOpen || Welcome.isOpen || Listen.pickerOpen;
    switch (button) {
      case 12: return "ArrowUp";
      case 13: return "ArrowDown";
      case 14: return "ArrowLeft";
      case 15: return "ArrowRight";
      case 0: return dialog ? "Enter" : " ";
      case 1: return dialog || Drawers.open ? "Escape" : "z";
      case 4: return "a";
      case 5: return "d";
      case 9: return "h";
      default: return null;
    }
  },

  press(key, index) {
    this.lastPad = index;
    this.lastInputAt = performance.now();
    window.dispatchEvent(new KeyboardEvent("keydown", { key, code: key === " " ? "Space" : key, bubbles: true, cancelable: true }));
  },

  tick() {
    if (!navigator.getGamepads) return;
    const active = this.enabled && document.hasFocus();
    for (const p of navigator.getGamepads()) {
      if (!p || !p.connected) continue;
      const last = active ? this.prev.get(p.index) || { buttons: [], dirs: {} } : null;
      const buttons = p.buttons.map((b) => b.pressed);
      // While inactive, only remember the state, so buttons held at that moment don't fire on return.
      if (last) buttons.forEach((down, i) => {
        if (down && !last.buttons[i]) {
          const key = this.keyFor(i);
          if (key) this.press(key, p.index);
        }
      });
      // Left stick acts like the D-pad, with a little hysteresis so one push is one press.
      const [x = 0, y = 0] = p.axes;
      const dirs = {};
      for (const [name, v, key] of [["left", -x, "ArrowLeft"], ["right", x, "ArrowRight"], ["up", -y, "ArrowUp"], ["down", y, "ArrowDown"]]) {
        const was = last ? last.dirs[name] : v > this.STICK_OFF;
        dirs[name] = was ? v > this.STICK_OFF : v > this.STICK_ON;
        if (last && dirs[name] && !was) this.press(key, p.index);
      }
      this.prev.set(p.index, { buttons, dirs });
    }
  },

  // ---------------------------------------------------------------- rumble

  /** Short pulses: [duration ms, strong 0–1, weak 0–1, pause before the next ms]. */
  PATTERNS: {
    keep: [[60, 0.6, 0.3]],
    archive: [[50, 0.15, 0.5]],
    favorite: [[45, 0.8, 0.4, 70], [45, 0.8, 0.4]],
    skip: [[25, 0, 0.25]],
    undo: [[140, 0.3, 0.3]],
    error: [[40, 1, 0.6, 60], [40, 1, 0.6, 60], [40, 1, 0.6]],
  },

  /** Rumble after an action the controller triggered (keyboard sorting stays silent in the hands). */
  feedback(kind) {
    if (!this.enabled || this.lastPad == null || performance.now() - this.lastInputAt > 5000) return;
    const pad = navigator.getGamepads && navigator.getGamepads()[this.lastPad];
    const act = pad && pad.vibrationActuator;
    const steps = this.PATTERNS[kind];
    if (!act || !act.playEffect || !steps) return;
    let at = 0;
    for (const [duration, strongMagnitude, weakMagnitude, gap = 0] of steps) {
      setTimeout(() => act.playEffect("dual-rumble", { duration, strongMagnitude, weakMagnitude }).catch(() => {}), at);
      at += duration + gap;
    }
  },
};

window.addEventListener("gamepadconnected", (e) => {
  if (!Pad.enabled) return;
  toast(`Controller connected: ${e.gamepad.id.replace(/\s*\(.*$/, "")}. D-pad sorts, A plays, B undoes.`, { ms: 6000 });
});
window.addEventListener("gamepaddisconnected", (e) => Pad.prev.delete(e.gamepad.index));
