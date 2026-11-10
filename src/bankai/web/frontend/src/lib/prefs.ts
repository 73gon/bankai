import { useEffect, useRef } from 'react';
import { api } from '@/lib/api';

/**
 * Settings the UI keeps on the server as well, so every device has them (web/ui_prefs.py).
 *
 * The browser's copy is read at once, so nothing waits; the server's copy is
 * asked for once per page load and wins where it differs. A change is kept in
 * the browser at once and sent a moment later, in the background.
 */
const LOCAL_PREFIX = 'bankai:pref:';
let fromServer: Promise<Record<string, unknown>> | null = null;
const timers = new Map<string, number>();

/** The server's settings, asked for once per page load; empty when it cannot answer. */
export function serverPrefs(): Promise<Record<string, unknown>> {
  fromServer ??= api.uiPrefs().then((answer) => answer.prefs ?? {}, () => ({}));
  return fromServer;
}

/** This browser's copy of a setting, or undefined. */
export function localPref(key: string): unknown {
  try {
    const raw = localStorage.getItem(LOCAL_PREFIX + key);
    return raw == null ? undefined : JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function keepLocally(key: string, value: unknown): void {
  try {
    localStorage.setItem(LOCAL_PREFIX + key, JSON.stringify(value));
  } catch {
    /* a private window: the server still keeps it */
  }
}

/** Keep a setting: in the browser now, on the server once it has stopped changing. */
export function saveSharedPref(key: string, value: unknown, delay = 800): void {
  keepLocally(key, value);
  // What this page load already read from the server follows, so a later read agrees.
  void serverPrefs().then((prefs) => { prefs[key] = value; });
  window.clearTimeout(timers.get(key));
  timers.set(key, window.setTimeout(() => {
    timers.delete(key);
    api.saveUiPref(key, value).catch(() => { /* kept in the browser; the next change sends it again */ });
  }, delay));
}

/** Adopt the server's copy of a setting this browser had differently (another device changed it). */
export function adoptServerPref(key: string, value: unknown): void {
  keepLocally(key, value);
}

/**
 * The title open in a library's split view, kept while the page is left for
 * another tab and opened again on coming back. `value` is what is open now
 * (null when nothing); `restore` opens a remembered one. Closing a title --
 * its close button, Back -- forgets it; leaving the page does not.
 */
export function useRememberedOpen(scope: string, value: string | null, enabled: boolean, restore: (value: string) => void): void {
  const key = 'bankai:open:' + scope;
  // Tried once the view is a split view: the view itself may come a moment after the page (a
  // setting read from the server), and the manga library never opened its series again.
  const tried = useRef(false);
  const previous = useRef<string | null>(null);
  useEffect(() => {
    try {
      if (!tried.current && (enabled || value != null)) {
        tried.current = true;
        const stored = localStorage.getItem(key);
        if (value == null && enabled && stored) restore(stored);
      } else if (value == null && previous.current != null) {
        localStorage.removeItem(key);
      }
      if (value != null && enabled) localStorage.setItem(key, value);
    } catch {
      /* storage blocked: nothing is remembered */
    }
    previous.current = value;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, enabled]);
}
