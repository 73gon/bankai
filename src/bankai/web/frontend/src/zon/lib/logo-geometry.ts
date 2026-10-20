import { useSyncExternalStore } from "react";

/*
 * The zon bracket geometry, measured from assets/logo.svg, logo-left.svg and
 * logo-right.svg (every transform and clipPath resolved) and expressed as fractions of the
 * bracket height. Each bracket is a full-height stem and one arm; the arms differ in height
 * (the left arm sits high, the right one near the middle), the strokes are identical.
 *
 * The source files draw the right stem 0.8% shorter than the left one (262.27 vs 264.42
 * units) — an export artefact that shows as a sub-pixel mismatch. Both stems are the full
 * bracket height here, so the halves always share height, stroke and baseline.
 */

/** Stroke width of stems and arms: 36.757812 / 264.457031. */
export const BRACKET_STROKE = 0.138993;
/** Arm length, including the stem it grows from: 93.171875 / 264.457031. */
export const BRACKET_ARM = 0.352315;
/** Top of the left arm: 56.367188 / 264.457031. */
export const LEFT_ARM_Y = 0.213142;
/** Top of the right arm: 112.757813 / 264.457031. */
export const RIGHT_ARM_Y = 0.426373;
/** Gap between the two stems in logo.svg: 76.128907 / 264.53125. */
export const MARK_GAP = 0.287786;

export type Rect = readonly [x: number, y: number, w: number, h: number];

export interface BracketGeometry {
  /** Bracket height. */
  h: number;
  /** Bracket width (= arm length). */
  w: number;
  /** Space between the stems in the complete symbol. */
  gap: number;
  left: readonly Rect[];
  right: readonly Rect[];
}

/**
 * The two brackets at `size` px. With `dpr`, every edge is snapped to whole device pixels,
 * so stems and arms are an exact number of pixels wide and both halves render identically
 * with `shape-rendering="crispEdges"` wherever they land on the page.
 */
export function bracketGeometry(size: number, dpr?: number): BracketGeometry {
  const q = dpr ? (v: number, min = 0) => Math.max(min, Math.round(v * dpr)) / dpr : (v: number) => v;
  const h = q(size, 1);
  const s = q(size * BRACKET_STROKE, 1);
  const w = Math.max(q(size * BRACKET_ARM, 1), s);
  const yl = q(size * LEFT_ARM_Y);
  const yr = q(size * RIGHT_ARM_Y);
  const gap = q(size * MARK_GAP, 1);
  return {
    h,
    w,
    gap,
    left: [
      [0, yl, w, s], // arm
      [w - s, 0, s, h], // stem
    ],
    right: [
      [0, 0, s, h], // stem
      [0, yr, w, s], // arm
    ],
  };
}

function subscribeDpr(onChange: () => void) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  let query: MediaQueryList;
  const listen = () => {
    query = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    query.addEventListener("change", handle, { once: true });
  };
  const handle = () => {
    listen();
    onChange();
  };
  listen();
  return () => query.removeEventListener("change", handle);
}

/** The device pixel ratio, updated on zoom and when a window moves between screens. 1 on the server. */
export function useDevicePixelRatio() {
  return useSyncExternalStore(
    subscribeDpr,
    () => window.devicePixelRatio || 1,
    () => 1,
  );
}
