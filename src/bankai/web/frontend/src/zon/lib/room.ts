import * as React from "react";

/*
 * A popup is portalled out of the panel its trigger sits in, so by default it only keeps inside
 * the window. Across, it should keep inside the room its trigger has — the nearest scrolling
 * container: a side panel, a dialog body, the docs' narrowed stage. Up and down it may still use
 * the window, or a short panel would crop it.
 */

/** The nearest ancestor that scrolls sideways (auto/scroll), or null for the window. */
export function roomOf(el: Element | null): HTMLElement | null {
  let room = el?.parentElement ?? null;
  while (room && room !== document.body && !/auto|scroll/.test(getComputedStyle(room).overflowX)) {
    room = room.parentElement;
  }
  return room && room !== document.body ? room : null;
}

/**
 * A collision boundary for a popup opened from `trigger`: its room's left and right edges, the
 * window's top and bottom. Undefined when the trigger has no room of its own (the window).
 */
export function roomBounds(trigger: Element | null) {
  const room = roomOf(trigger);
  if (!room) return undefined;
  const { left, width } = room.getBoundingClientRect();
  return { x: left, y: 0, width, height: window.innerHeight };
}

/** Lets a popup find the trigger that opened it: the trigger registers, the content reads. */
export interface TriggerRoom {
  trigger: HTMLElement | null;
  setTrigger: (el: HTMLElement | null) => void;
}

export const TriggerRoomContext = React.createContext<TriggerRoom | null>(null);

/** State for a popup root: the trigger it opens from. Provide it with `TriggerRoomContext`. */
export function useTriggerRoom(): TriggerRoom {
  const [trigger, setTrigger] = React.useState<HTMLElement | null>(null);
  return React.useMemo(() => ({ trigger, setTrigger }), [trigger]);
}
