/* BAKKIS MUSIC SORTER v2 — game controllers (Xbox, PlayStation, 8BitDo, …) as a remote.
 *
 * Each button press becomes the matching key press, so every mode, setting and dialog behaves exactly as
 * with the keyboard. Read once per frame from the existing render loop; costs nothing without a controller.
 * Browsers only expose a controller after its first button press.
 */

const Pad = {
  STICK_ON: 0.6, // how far the stick must move to count as a press
  STICK_OFF: 0.3, // and how far back before it can press again
  prev: new Map(), // controller index -> previous {buttons, dirs}

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

  press(key) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, code: key === " " ? "Space" : key, bubbles: true, cancelable: true }));
  },

  tick() {
    if (!navigator.getGamepads) return;
    for (const p of navigator.getGamepads()) {
      if (!p || !p.connected) continue;
      const last = this.prev.get(p.index) || { buttons: [], dirs: {} };
      const buttons = p.buttons.map((b) => b.pressed);
      buttons.forEach((down, i) => {
        if (down && !last.buttons[i]) {
          const key = this.keyFor(i);
          if (key) this.press(key);
        }
      });
      // Left stick acts like the D-pad, with a little hysteresis so one push is one press.
      const [x = 0, y = 0] = p.axes;
      const dirs = {};
      for (const [name, v, key] of [["left", -x, "ArrowLeft"], ["right", x, "ArrowRight"], ["up", -y, "ArrowUp"], ["down", y, "ArrowDown"]]) {
        dirs[name] = last.dirs[name] ? v > this.STICK_OFF : v > this.STICK_ON;
        if (dirs[name] && !last.dirs[name]) this.press(key);
      }
      this.prev.set(p.index, { buttons, dirs });
    }
  },
};

window.addEventListener("gamepadconnected", (e) => {
  toast(`Controller connected: ${e.gamepad.id.replace(/\s*\(.*$/, "")}. D-pad sorts, A plays, B undoes.`, { ms: 6000 });
});
window.addEventListener("gamepaddisconnected", (e) => Pad.prev.delete(e.gamepad.index));
