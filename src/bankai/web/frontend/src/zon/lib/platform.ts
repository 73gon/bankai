import * as React from "react";

/**
 * Apple's keyboards say ⌘ where others say Ctrl. Read from User-Agent Client Hints where the
 * browser has them (Chromium: "macOS"), else from `navigator.platform` (Safari, Firefox).
 */
export function isApple() {
  if (typeof navigator === "undefined") return false;
  const hints = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  return /mac|iphone|ipad|ipod/i.test(hints?.platform || navigator.platform || "");
}

/** The modifier key for shortcuts on this device: "⌘" on Apple, "Ctrl" elsewhere. */
export const modKey = () => (isApple() ? "⌘" : "Ctrl");

const touchOnly = "(hover: none) and (pointer: coarse)";
const subscribeTouch = (onChange: () => void) => {
  const query = window.matchMedia(touchOnly);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
};
const noSubscribe = () => () => {};

/**
 * The device's shortcut keys, for hints: the modifier (`mod`, "⌘" or "Ctrl"), and whether it
 * has a keyboard at all (`hasKeyboard`) — not a touch-only phone or tablet, where nobody
 * presses ⌘V. A tablet with a keyboard and trackpad counts as having keys. On the server, and in the first client render,
 * it answers "Ctrl" with keys, then settles on the device's own answer.
 */
export function useShortcutKeys() {
  const mod = React.useSyncExternalStore(noSubscribe, modKey, () => "Ctrl");
  const hasKeyboard = React.useSyncExternalStore(
    subscribeTouch,
    () => !window.matchMedia(touchOnly).matches,
    () => true,
  );
  return { mod, hasKeyboard };
}

/** Keys that are written differently on Apple keyboards and on others. */
const appleKeys: Record<string, string> = { mod: "⌘", shift: "⇧", alt: "⌥", ctrl: "⌃", enter: "↵" };
const otherKeys: Record<string, string> = {
  mod: "Ctrl",
  shift: "Shift",
  alt: "Alt",
  ctrl: "Ctrl",
  enter: "Enter",
};

/** Each key of a shortcut as the device's keyboard prints it: `mod` is ⌘ or Ctrl, and so on. */
export function keyNames(keys: string[], mod: string) {
  const names = mod === "⌘" ? appleKeys : otherKeys;
  return keys.map((key) => names[key.toLowerCase()] ?? key);
}

/**
 * A shortcut as text, the way each platform writes it in menus: run together on Apple ("⇧⌘Z",
 * modifiers in Apple's order), joined with "+" elsewhere ("Ctrl+Shift+Z").
 */
export function formatShortcut(keys: string[], mod: string) {
  if (mod !== "⌘") return keyNames(keys, mod).join("+");
  const order = ["ctrl", "alt", "shift", "mod"];
  const sorted = [...keys].sort((a, b) => {
    const ia = order.indexOf(a.toLowerCase());
    const ib = order.indexOf(b.toLowerCase());
    return (ia === -1 ? order.length : ia) - (ib === -1 ? order.length : ib);
  });
  return keyNames(sorted, mod).join("");
}
