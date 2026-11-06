/**
 * The `--duration` token in milliseconds, as an element sees it — 0 under reduced motion. Reads
 * either unit: a build's CSS minifier writes `150ms` as `.15s`, so a bare `parseFloat` would take
 * a production duration for 0.15ms and skip every scripted animation.
 */
export function cssDuration(el: Element = document.documentElement): number {
  const value = getComputedStyle(el).getPropertyValue("--duration").trim();
  const number = parseFloat(value);
  if (!Number.isFinite(number)) return 0;
  return value.endsWith("ms") ? number : value.endsWith("s") ? number * 1000 : number;
}
