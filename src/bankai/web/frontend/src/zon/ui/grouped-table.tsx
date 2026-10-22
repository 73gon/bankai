import { Collapsible as BaseCollapsible } from "@base-ui/react/collapsible";
import { ChevronRightIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { flushSync } from "react-dom";
import { cn } from "@/zon/lib/cn";
import { Badge } from "@/zon/ui/badge";
import { Button } from "@/zon/ui/button";

/* ── Types ─────────────────────────────────────────────────────────────────── */

/** Where a column's cell is drawn at the current width. */
export type GroupedTableCellMode = "column" | "inline" | "hidden";

export interface GroupedTableColumn<T> {
  /** Stable id, unique among the columns. */
  id: string;
  /** The column's accessible name ("Due date"). Screen readers hear it at every width. */
  label: string;
  /**
   * What the group header shows above the column while it has its own column: a 14px lucide
   * icon or a short mono word. Defaults to `label` as an eyebrow. Decorative — `label` names it.
   */
  header?: React.ReactNode;
  /**
   * The cell. `mode` says whether it is drawn in its own column, inline or only for screen
   * readers. Keep it the same width in every mode: the fit is measured from what is drawn.
   */
  cell: (item: T, context: { mode: GroupedTableCellMode }) => React.ReactNode;
  /**
   * When the titles no longer fit on one line, columns step out one at a time, lowest
   * `priority` first (on a tie, the later column first). A column with no `priority`, `inline`
   * or `minWidth` never steps out.
   */
  priority?: number;
  /** When the column steps out, show its cell inline right after the title instead of hiding it. */
  inline?: boolean;
  /**
   * Optional floor, in rem of container width: below it the column steps out even if it would
   * still fit. The fit decides everything above it.
   */
  minWidth?: number;
  /** The grid track while the column has its own column. Defaults to `auto` (its widest cell). */
  width?: string;
  /** `end` for numbers, dates and people. Defaults to `start`. */
  align?: "start" | "end";
}

export interface GroupedTableGroup<T> {
  /** Stable id, unique among the groups. Passed to `onAdd` and used for open state. */
  id: string;
  /** The group's name ("In review"). */
  label: React.ReactNode;
  /** Plain-text name, when `label` is not a string. Used for the add button's accessible name. */
  name?: string;
  /** A 16px lucide icon before the label. */
  icon?: React.ReactNode;
  items: T[];
}

export interface GroupedTableProps<T> extends Omit<
  React.ComponentProps<"div">,
  "title" | "children" | "onChange"
> {
  /** The groups, in order. Each opens with a header band and holds its rows. */
  groups: GroupedTableGroup<T>[];
  /** The columns after the title, in order. Inline columns are drawn first, right after the title. */
  columns: GroupedTableColumn<T>[];
  /** The row's title — the one cell that is always shown and names the row. */
  title: (item: T) => React.ReactNode;
  /** Accessible name of the title column. Defaults to `"Title"`. */
  titleLabel?: string;
  /** A small glyph before the title (priority, type), up to 16px. Give it its own accessible name. */
  leading?: (item: T) => React.ReactNode;
  /** A stable key for each item. */
  getKey: (item: T) => React.Key;
  /** Content for a bar above the table: tabs, a filter, a view menu. */
  toolbar?: React.ReactNode;
  /** Shows a + button in every group header. Called with the group's id. */
  onAdd?: (groupId: string) => void;
  /** Accessible name of a group's + button. Defaults to `Add to <group name>`. */
  addLabel?: (group: GroupedTableGroup<T>) => string;
  /** Glide cells into their new places when the layout changes, and animate collapsing. Default `true`. */
  animated?: boolean;
  /** Keep each group's header at the top of the scroll area while its rows scroll under it. Default `true`. */
  stickyHeaders?: boolean;
  /** Ids of the groups open at first. Defaults to every group. */
  defaultOpen?: string[];
  /** Controlled: ids of the open groups. */
  open?: string[];
  /** Called with the ids of the open groups when a group opens or closes. */
  onOpenChange?: (open: string[]) => void;
  /** `card` (default): the table in a 20px card. `plain`: the same table without the card. */
  variant?: "card" | "plain";
}

/* ── Fit ───────────────────────────────────────────────────────────────────── */

interface Layout {
  /** How many columns have stepped out, in removal order. */
  step: number;
  /** Every removable column is out or inline: the only layout in which titles may wrap. */
  final: boolean;
  modes: Record<string, GroupedTableCellMode>;
  /** True while at least one column is drawn inside the title cell. */
  inlined: boolean;
}

type AnyColumn = GroupedTableColumn<unknown>;

/** The columns that can step out, in the order they do. */
function removalOrder(columns: AnyColumn[]) {
  return columns
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => c.priority != null || c.inline || (c.minWidth ?? 0) > 0)
    .sort((a, b) => (a.c.priority ?? 0) - (b.c.priority ?? 0) || b.i - a.i)
    .map(({ c }) => c);
}

/** Inline columns first, in the order they step out; then the others, as given. */
function drawOrder(columns: AnyColumn[]) {
  const order = removalOrder(columns);
  const inline = order.filter((c) => c.inline);
  return [...inline, ...columns.filter((c) => !c.inline)];
}

function layoutFor(step: number, order: AnyColumn[], columns: AnyColumn[]): Layout {
  const out = new Set(order.slice(0, step).map((c) => c.id));
  const modes: Record<string, GroupedTableCellMode> = {};
  for (const c of columns) modes[c.id] = !out.has(c.id) ? "column" : c.inline ? "inline" : "hidden";
  return {
    step,
    final: step >= order.length,
    modes,
    inlined: Object.values(modes).includes("inline"),
  };
}

/** Natural, single-line widths, measured once per data or font change. */
interface Metrics {
  /** Each drawn row: its title on one line (leading slot included) and each cell's width. */
  rows: { title: number; cells: Record<string, number> }[];
  /** Each column's track while it has its own column. */
  tracks: Record<string, number>;
  /** The en space between the title and an inline cell. */
  space: number;
}

/** The leading slot: a 24px column for a glyph of up to 16px. */
const LEAD_PX = 24;
/** Restoring a column needs this much more room than keeping it: no flicker at the boundary. */
const HYSTERESIS_PX = 8;

const UNCLIP =
  "position:static;display:inline-flex;width:max-content;min-width:0;height:auto;overflow:visible;" +
  "clip:auto;clip-path:none;margin:0;padding:0;white-space:nowrap;";

/**
 * Clones every drawn title and cell into a hidden probe with `white-space: nowrap`, reads all
 * widths in one pass and removes the probe — before anything paints.
 */
function measureMetrics(card: HTMLElement, body: HTMLElement, columns: AnyColumn[]): Metrics {
  const probe = document.createElement("div");
  probe.setAttribute("aria-hidden", "true");
  probe.className = "font-body text-sm";
  probe.style.cssText =
    "position:absolute;top:0;left:0;visibility:hidden;pointer-events:none;white-space:nowrap;width:max-content;contain:layout style;";
  const space = probe.appendChild(document.createElement("span"));
  space.textContent = " ";
  const grids = new Map<string, HTMLElement>();
  for (const c of columns) {
    const grid = probe.appendChild(document.createElement("div"));
    grid.style.cssText = `display:grid;width:max-content;grid-template-columns:${c.width ?? "auto"};`;
    grids.set(c.id, grid);
  }
  const rows = [...body.querySelectorAll<HTMLElement>("[data-slot=grouped-table-row]")].filter(
    (row) => !row.closest("[data-ending-style]"),
  );
  const probes = rows.map((row) => {
    const text = row.querySelector("[data-slot=grouped-table-title-text]");
    const title = text ? (text.cloneNode(true) as HTMLElement) : null;
    if (title) {
      title.style.cssText = UNCLIP;
      probe.append(title);
    }
    const cells: [string, HTMLElement][] = [];
    for (const cell of row.querySelectorAll<HTMLElement>("[data-column]")) {
      const id = cell.getAttribute("data-column")!;
      const clone = cell.cloneNode(true) as HTMLElement;
      clone.style.cssText = UNCLIP;
      grids.get(id)?.append(clone);
      cells.push([id, clone]);
    }
    return { title, lead: !!row.querySelector("[data-slot=grouped-table-leading]"), cells };
  });
  card.append(probe);
  const metrics: Metrics = {
    space: space.getBoundingClientRect().width,
    tracks: Object.fromEntries([...grids].map(([id, g]) => [id, g.getBoundingClientRect().width])),
    rows: probes.map((p) => ({
      title: (p.title?.getBoundingClientRect().width ?? 0) + (p.lead ? LEAD_PX : 0),
      cells: Object.fromEntries(p.cells.map(([id, el]) => [id, el.getBoundingClientRect().width])),
    })),
  };
  probe.remove();
  return metrics;
}

/** The width a layout needs with every title on one line. */
function widthNeeded(layout: Layout, columns: AnyColumn[], m: Metrics, edge: number, gap: number) {
  const drawn = drawOrder(columns);
  const inline = drawn.filter((c) => layout.modes[c.id] === "inline");
  const own = drawn.filter((c) => layout.modes[c.id] === "column");
  let title = 0;
  for (const row of m.rows) {
    let w = row.title;
    for (const c of inline) w += m.space + (row.cells[c.id] ?? 0);
    title = Math.max(title, w);
  }
  // The rows' padding sits inside the edge tracks: the title's on the left, and on the right
  // the last column's — which grows an auto track, but not a fixed one.
  const need = edge + Math.ceil(title) + own.reduce((sum, c) => sum + (m.tracks[c.id] ?? 0) + gap, 0);
  const last = own.at(-1);
  return need + (!last || (last.width ?? "auto") === "auto" ? edge : 0);
}

/**
 * The fewest columns to step out so the widest title stays on one line. Stepping out happens
 * the moment it no longer fits; stepping back needs `HYSTERESIS_PX` to spare.
 */
function fitStep(
  available: number,
  current: number,
  order: AnyColumn[],
  columns: AnyColumn[],
  m: Metrics,
  edge: number,
  gap: number,
  widthRem: number,
) {
  let floor = 0;
  order.forEach((c, i) => {
    if ((c.minWidth ?? 0) > widthRem) floor = i + 1;
  });
  for (let step = floor; step < order.length; step++) {
    const need = widthNeeded(layoutFor(step, order, columns), columns, m, edge, gap);
    if (need + (step < current ? HYSTERESIS_PX : 0) <= available) return step;
  }
  return order.length;
}

/* ── FLIP ──────────────────────────────────────────────────────────────────── */

/**
 * One beat for the whole row: a leaving cell fades over exactly the time its neighbours take to
 * glide into the space it frees, on the same curve and from the same start, so the gap closes as
 * the cell goes. Entering cells start a moment later and land with them.
 */
const MOVE_MS = 280;
const ENTER_DELAY_MS = 40;
const ENTER_MS = MOVE_MS - ENTER_DELAY_MS;
/**
 * Fades run on a gentle standard curve, not the brand's front-loaded ease-out: on that curve a
 * fade is ~85% done in the first frames, so a returning column seemed to pop straight in.
 */
const FADE_EASING = "cubic-bezier(0.4, 0, 0.2, 1)";
const EXIT_MS = MOVE_MS;
/** How far a leaving cell drifts towards its own side as it fades. */
const EXIT_DRIFT_PX = 8;
/** Delay between one row (or band) and the next, top to bottom — exits and glides alike. */
const STAGGER_MS = 30;
/** The last row in view starts by then, so a long table still settles within ~520ms. */
const SPREAD_MS = 240;

type Pos = { x: number; y: number };
const ZERO: Pos = { x: 0, y: 0 };

/**
 * Pieces are matched across a change by `data-flip-key` when they have one: a cell that moves
 * into the title is a new element in a new parent, and still glides from where it was.
 */
type Key = Element | string;
const keyOf = (el: Element): Key => el.getAttribute("data-flip-key") ?? el;

const isDrawn = (el: Element) => !el.closest("[data-mode=hidden]");
const sideOf = (el: Element) => (el.getAttribute("data-align") === "end" ? "right center" : "left center");
/** The band or row a piece belongs to: its place in the top-to-bottom cascade. */
const seqOf = (el: Element) => el.closest("[data-flip=row],[data-flip=band]");
/** Where a piece is anchored: the side it scales from, and its vertical middle. */
const anchorOf = (rect: DOMRect, side: string) => ({
  x: side === "right center" ? rect.right : rect.left,
  y: rect.top + rect.height / 2,
});

/**
 * The glide. Tracked pieces carry `data-flip`. Each is measured relative to its nearest tracked
 * parent (bands and rows relative to the scroll content), so a cell glides inside its row while
 * the row glides inside the table, and a stuck band stays stuck.
 *
 * - `tier()` wraps a layout change: measure where everything is drawn, commit the new layout,
 *   measure again, animate the difference. The bands and rows in view go top to bottom, up to
 *   30ms apart; every piece of a row starts with its row, its leaving cells included. Entering
 *   cells fade and grow in; leaving cells are cloned where they stood and fade out as their
 *   neighbours glide over. Interrupted, it starts again from
 *   what is drawn: positions mid-glide, cells mid-fade, ghosts still fading out.
 * - `reflow()` runs on every other resize. In the final layout, where titles wrap, a piece that
 *   changed line glides there instead of jumping; pieces that only follow the width keep
 *   following the pointer.
 *
 * Each pass reads every rect first and writes after, so there is one forced layout per change.
 */
class Flip {
  private anims = new Map<Element, Animation>();
  private ghosts = new Map<string, { clone: HTMLElement; anim: Animation }>();
  private cache = new Map<Key, Pos>();
  private active = 0;

  constructor(
    private root: HTMLElement,
    private body: HTMLElement,
    private layer: HTMLElement,
  ) {}

  private easing() {
    return getComputedStyle(this.root).getPropertyValue("--ease").trim() || "cubic-bezier(0.16, 1, 0.3, 1)";
  }

  /** Open panels overflow while anything glides, so rows can travel past a panel's edge. */
  private track(a: Animation) {
    if (this.active++ === 0) this.root.setAttribute("data-gliding", "");
    const done = () => {
      if (--this.active === 0) this.root.removeAttribute("data-gliding");
    };
    a.finished.then(done, done);
    return a;
  }

  private run(el: Element, keyframes: Keyframe[], options: KeyframeAnimationOptions) {
    this.anims.get(el)?.cancel();
    const a = this.track(el.animate(keyframes, { fill: "backwards", ...options }));
    this.anims.set(el, a);
    a.finished.then(
      () => this.anims.get(el) === a && this.anims.delete(el),
      () => undefined,
    );
  }

  private dropGhost(key: string) {
    const ghost = this.ghosts.get(key);
    if (!ghost) return;
    this.ghosts.delete(key);
    ghost.anim.cancel();
    ghost.clone.remove();
  }

  /**
   * Where a piece's box starts without the scale it is drawn at. A piece fading in is scaled from
   * its side's vertical middle, which moves its drawn top; read as is, that looked like a change
   * of line, and the next resize replaced the fade-in with a glide, so the cell popped in.
   */
  private unscaled(el: Element, r: DOMRect) {
    const a = this.anims.get(el);
    const s = a?.playState === "running" ? parseFloat(getComputedStyle(el).scale) : NaN;
    if (!s || s === 1) return { x: r.left, y: r.top };
    const { offsetWidth: w, offsetHeight: h } = el as HTMLElement;
    return {
      x: sideOf(el) === "right center" ? r.right - w : r.left,
      y: r.top + r.height / 2 - h / 2,
    };
  }

  /** Every drawn piece, its rect, and its position in its parent — less `offsets`, a glide in progress. */
  private measure(offsets?: Map<Element, Pos>) {
    const box = this.body.getBoundingClientRect();
    const content = { x: box.left - this.body.scrollLeft, y: box.top - this.body.scrollTop };
    const els = [...this.root.querySelectorAll("[data-flip]")].filter(isDrawn);
    const rects = new Map(els.map((el) => [el, el.getBoundingClientRect()] as const));
    const rel = new Map<Key, Pos>();
    for (const el of els) {
      const r = this.unscaled(el, rects.get(el)!);
      const own = offsets?.get(el) ?? ZERO;
      const parent = el.parentElement?.closest("[data-flip]");
      const p = parent ? rects.get(parent) : undefined;
      const base = p ? { x: p.left, y: p.top } : content;
      rel.set(keyOf(el), { x: r.x - own.x - base.x, y: r.y - own.y - base.y });
    }
    return { els, rects, rel, view: box };
  }

  /**
   * The cascade: bands and rows in view count 0, 1, 2… from the top; those above the view start
   * at once, those below with the last one in view. Rows of closed groups are not drawn.
   */
  private cascade(rects: Map<Element, DOMRect>, view: DOMRect) {
    const index = new Map<Element, number>();
    const below: Element[] = [];
    let count = 0;
    for (const el of this.root.querySelectorAll("[data-flip=band],[data-flip=row]")) {
      const r = rects.get(el);
      if (!r || el.closest("[data-ending-style]")) continue;
      if (r.bottom <= view.top) index.set(el, 0);
      else if (r.top >= view.bottom) below.push(el);
      else index.set(el, count++);
    }
    for (const el of below) index.set(el, Math.max(0, count - 1));
    const gap = count > 1 ? Math.min(STAGGER_MS, SPREAD_MS / (count - 1)) : 0;
    return (seq: Element | null) => (seq ? (index.get(seq) ?? 0) * gap : 0);
  }

  /** Forget the last positions: something other than the width moved things (scroll, data, a group). */
  invalidate() {
    this.cache.clear();
  }

  cancel() {
    for (const a of this.anims.values()) a.cancel();
    this.anims.clear();
    for (const key of [...this.ghosts.keys()]) this.dropGhost(key);
  }

  /** Glides from what is drawn now to the layout that `commit` applies (synchronously) for `next`. */
  tier(next: Layout, commit: () => void) {
    const easing = this.easing();

    // Read: positions (mid-glide included), the fade of anything mid-entrance, the ghosts still
    // fading out, and clones of the pieces about to step out.
    const first = this.measure();
    const faded = new Map<Key, { opacity: number; scale: number }>();
    for (const [el, a] of this.anims) {
      if (a.playState !== "running" || !first.rects.has(el)) continue;
      const cs = getComputedStyle(el);
      faded.set(keyOf(el), { opacity: parseFloat(cs.opacity), scale: parseFloat(cs.scale) || 1 });
    }
    const fading = new Map<string, { rect: DOMRect; opacity: number; scale: number }>();
    for (const [key, { clone }] of this.ghosts) {
      const cs = getComputedStyle(clone);
      fading.set(key, {
        rect: clone.getBoundingClientRect(),
        opacity: parseFloat(cs.opacity),
        scale: parseFloat(cs.scale) || 1,
      });
    }
    const leaving = first.els
      .filter((el) => {
        const column = el.getAttribute("data-column");
        const header = el.getAttribute("data-column-header");
        // A column's header icon leaves with it, or when the headers move into the titles.
        return (
          (column && next.modes[column] === "hidden") ||
          (header && (next.inlined || next.modes[header] !== "column"))
        );
      })
      .map((el) => ({
        el,
        key: el.getAttribute("data-flip-key"),
        seq: seqOf(el),
        side: sideOf(el),
        rect: first.rects.get(el)!,
        size: { w: (el as HTMLElement).offsetWidth, h: (el as HTMLElement).offsetHeight },
        from: faded.get(keyOf(el)) ?? { opacity: 1, scale: 1 },
        clone: el.cloneNode(true) as HTMLElement,
      }));

    for (const a of this.anims.values()) a.cancel();
    this.anims.clear();
    commit();

    // Read the new layout once, then write every animation.
    const last = this.measure();
    const delay = this.cascade(last.rects, last.view);
    const origin = this.layer.getBoundingClientRect();

    for (const el of last.els) {
      const wait = delay(seqOf(el));
      const side = sideOf(el);
      const key = el.getAttribute("data-flip-key");
      let from = first.rel.get(keyOf(el));
      let start = faded.get(keyOf(el));
      const ghost = !from && key ? fading.get(key) : undefined;

      if (!from && ghost) {
        // Back before its ghost faded out: carry on from the ghost, where and as it is drawn.
        const parent = el.parentElement?.closest("[data-flip]");
        const pFirst = parent && first.rects.get(parent);
        const pLast = parent && last.rects.get(parent);
        const g = anchorOf(ghost.rect, side);
        const now = anchorOf(last.rects.get(el)!, side);
        const shift = pFirst && pLast ? { x: pFirst.left - pLast.left, y: pFirst.top - pLast.top } : ZERO;
        const to = last.rel.get(keyOf(el))!;
        from = { x: to.x + g.x - now.x - shift.x, y: to.y + g.y - now.y - shift.y };
        start = { opacity: ghost.opacity, scale: ghost.scale };
        this.dropGhost(key!);
      }

      if (!from) {
        this.run(
          el,
          [
            { opacity: 0, scale: "0.6", transformOrigin: side },
            { opacity: 1, scale: "1", transformOrigin: side },
          ],
          { duration: ENTER_MS, delay: wait + ENTER_DELAY_MS, easing: FADE_EASING },
        );
        continue;
      }
      const to = last.rel.get(keyOf(el))!;
      const dx = from.x - to.x;
      const dy = from.y - to.y;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && !start) continue;
      const fade = start
        ? [
            { opacity: start.opacity, scale: String(start.scale), transformOrigin: side },
            { opacity: 1, scale: "1", transformOrigin: side },
          ]
        : [{}, {}];
      this.run(
        el,
        [
          { translate: `${dx}px ${dy}px`, ...fade[0] },
          { translate: "0px 0px", ...fade[1] },
        ],
        { duration: MOVE_MS, delay: wait, easing },
      );
    }

    for (const l of leaving) {
      if (l.key) this.dropGhost(l.key);
      const { clone, rect, size, side } = l;
      clone.removeAttribute("data-flip");
      clone.removeAttribute("id");
      Object.assign(clone.style, {
        position: "absolute",
        margin: "0",
        left: `${(side === "right center" ? rect.right - size.w : rect.left) - origin.left}px`,
        top: `${rect.top + rect.height / 2 - size.h / 2 - origin.top}px`,
        width: `${size.w}px`,
        height: `${size.h}px`,
      });
      this.layer.append(clone);
      const anim = this.track(
        clone.animate(
          [
            {
              opacity: l.from.opacity,
              scale: String(l.from.scale),
              translate: "0px 0px",
              transformOrigin: side,
              offset: 0,
            },
            // Shrink a little and drift towards its own side as it fades, in step with the glide.
            {
              opacity: 0,
              scale: "0.9",
              translate: `${side === "right center" ? EXIT_DRIFT_PX : -EXIT_DRIFT_PX}px 0px`,
              transformOrigin: side,
              offset: 1,
            },
          ],
          { duration: EXIT_MS, delay: delay(l.seq), easing, fill: "both" },
        ),
      );
      const ghost = { clone, anim };
      if (l.key) this.ghosts.set(l.key, ghost);
      anim.finished.then(
        () => {
          clone.remove();
          if (l.key && this.ghosts.get(l.key) === ghost) this.ghosts.delete(l.key);
        },
        () => undefined,
      );
    }
    this.cache = last.rel;
  }

  /** A resize within a layout: glide what changed line, from where it is drawn now. */
  reflow() {
    const offsets = new Map<Element, Pos>();
    const faded = new Map<Element, { opacity: number; scale: number }>();
    for (const [el, a] of this.anims) {
      if (a.playState !== "running") continue;
      const cs = getComputedStyle(el);
      const [x = "0", y = "0"] = cs.translate.split(" ");
      offsets.set(el, { x: parseFloat(x) || 0, y: parseFloat(y) || 0 });
      const opacity = parseFloat(cs.opacity);
      const scale = parseFloat(cs.scale) || 1;
      if (opacity < 1 || scale !== 1) faded.set(el, { opacity, scale });
    }
    const now = this.measure(offsets);
    if (this.cache.size) {
      const easing = this.easing();
      for (const el of now.els) {
        const prev = this.cache.get(keyOf(el));
        if (!prev) continue;
        const to = now.rel.get(keyOf(el))!;
        // A changed line moves a piece; so does any shift of an inline cell, which only moves when
        // a word wraps. Columns shift smoothly with the width and keep following it.
        const inline = el.getAttribute("data-mode") === "inline";
        if (Math.abs(prev.y - to.y) < 1 && !(inline && Math.abs(prev.x - to.x) >= 1)) continue;
        const own = offsets.get(el) ?? ZERO;
        const dx = prev.x - to.x + own.x;
        const dy = prev.y - to.y + own.y;
        // A piece still fading in carries on fading as it glides.
        const start = faded.get(el);
        const side = sideOf(el);
        const fade = start
          ? [
              { opacity: start.opacity, scale: String(start.scale), transformOrigin: side },
              { opacity: 1, scale: "1", transformOrigin: side },
            ]
          : [{}, {}];
        this.run(
          el,
          [
            { translate: `${dx}px ${dy}px`, ...fade[0] },
            { translate: "0px 0px", ...fade[1] },
          ],
          { duration: MOVE_MS, easing },
        );
      }
    }
    this.cache = now.rel;
  }
}

/* ── Component ─────────────────────────────────────────────────────────────── */

/** Card padding, carried by the rows: 16px, 20px from a 40rem container. */
const pad = "[--gt-pad:--spacing(4)] @min-[40rem]:[--gt-pad:--spacing(5)]";

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * GroupedTable — rows grouped under collapsible, sticky header bands, in a table whose columns
 * glide into each row as it narrows. Every row is a grid. Titles stay on one line: the moment
 * the widest title no longer fits beside the columns, the next column (by `priority`) steps
 * out — or, when it is `inline`, moves inside the title cell, right after the title. Only once
 * every column that can step out has done so do titles wrap. Each piece glides from its old
 * place to its new one, row by row from the top (FLIP on the Web Animations API).
 *
 * The fit follows the **container's** width, not the viewport's, so the same table restructures
 * in a sidebar, a split pane or a resizable frame. Screen readers get one table at every width:
 * the cells keep their DOM order, and columns that step out stay in the accessibility tree.
 *
 * Use it for work items people scan by status (tasks, runs, tickets). For data people sort and
 * compare across every column, use `DataTable`. The rule people break: a dozen columns. Give
 * the table four or five, and decide which ones matter when it is narrow.
 */
export function GroupedTable<T>({
  groups,
  columns,
  title,
  titleLabel = "Title",
  leading,
  getKey,
  toolbar,
  onAdd,
  addLabel,
  animated = true,
  stickyHeaders = true,
  defaultOpen,
  open: openProp,
  onOpenChange,
  variant = "card",
  className,
  style,
  ref,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  ...props
}: GroupedTableProps<T>) {
  const cardRef = React.useRef<HTMLDivElement | null>(null);
  const bodyRef = React.useRef<HTMLDivElement | null>(null);
  const layerRef = React.useRef<HTMLDivElement | null>(null);
  const flipRef = React.useRef<Flip | null>(null);
  const remeasureRef = React.useRef<((glide?: boolean) => void) | null>(null);

  const order = React.useMemo(() => removalOrder(columns as AnyColumn[]), [columns]);
  // Inline columns are drawn first, in the order they move into the title, so the DOM order
  // matches what is seen at every width.
  const ordered = React.useMemo(
    () => drawOrder(columns as AnyColumn[]) as GroupedTableColumn<T>[],
    [columns],
  );
  const [step, setStep] = React.useState(0);
  const layout = React.useMemo(
    () => layoutFor(Math.min(step, order.length), order, columns as AnyColumn[]),
    [step, order, columns],
  );

  const latest = React.useRef({ step, order, columns, animated });
  latest.current = { step, order, columns, animated };

  React.useLayoutEffect(() => {
    const card = cardRef.current;
    const body = bodyRef.current;
    const layer = layerRef.current;
    if (!card || !body || !layer) return;
    const flip = new Flip(card, body, layer);
    flipRef.current = flip;
    let metrics: Metrics | null = null;
    const rem = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const motion = () => latest.current.animated && !reducedMotion();

    /** Places the current width; `glide` false for the first layout, which must not animate. */
    const evaluate = (resized: boolean, glide = true) => {
      if (!metrics) return;
      const { step, order, columns } = latest.current;
      const band = body.querySelector("[data-slot=grouped-table-band]");
      const bs = band ? getComputedStyle(band) : null;
      const edge = bs ? parseFloat(bs.paddingLeft) : 16;
      const gap = parseFloat(getComputedStyle(body).columnGap) || 0;
      const next = fitStep(
        body.clientWidth,
        step,
        order,
        columns as AnyColumn[],
        metrics,
        edge,
        gap,
        card.clientWidth / rem(),
      );
      if (next === step) {
        if (resized && motion()) flip.reflow();
        return;
      }
      // Inside a layout effect (the first layout, new data) flushSync is not allowed and not
      // needed: a state update there already commits before paint, so there is no glide. From
      // the observers it must land synchronously, for the glide to measure the new layout.
      const commit = () => (glide ? flushSync(() => setStep(next)) : setStep(next));
      if (glide && motion()) flip.tier(layoutFor(next, order, columns as AnyColumn[]), commit);
      else commit();
    };
    /** `glide` false when called from a layout effect (see `commit`). */
    const remeasure = (glide = true) => {
      metrics = measureMetrics(card, body, latest.current.columns as AnyColumn[]);

      evaluate(false, glide);
    };
    remeasureRef.current = remeasure;
    // Before the first paint: measure, and start in the layout that fits, without a glide.
    metrics = measureMetrics(card, body, latest.current.columns as AnyColumn[]);

    evaluate(false, false);

    const ro = new ResizeObserver(() => evaluate(true));
    ro.observe(card);
    // Rows mount when a group opens: measure them on the next frame.
    let frame = 0;
    const isRows = (n: Node) =>
      n instanceof Element &&
      (n.matches("[data-slot=grouped-table-row],[data-slot=grouped-table-panel]") ||
        !!n.querySelector("[data-slot=grouped-table-row]"));
    const mo = new MutationObserver((records) => {
      if (frame || !records.some((r) => [...r.addedNodes, ...r.removedNodes].some(isRows))) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        remeasure();
      });
    });
    mo.observe(body, { childList: true, subtree: true });
    // Titles are measured in the real fonts once they load.
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    let alive = true;
    fonts?.ready.then(() => alive && remeasure());
    const onFonts = () => remeasure();
    fonts?.addEventListener("loadingdone", onFonts);
    const onScroll = () => flip.invalidate();
    body.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      alive = false;
      ro.disconnect();
      mo.disconnect();
      cancelAnimationFrame(frame);
      fonts?.removeEventListener("loadingdone", onFonts);
      body.removeEventListener("scroll", onScroll);
      flip.cancel();
      flipRef.current = null;
      remeasureRef.current = null;
    };
  }, []);

  // Open state is kept as the set of closed groups, so groups added later start open.
  const [closed, setClosed] = React.useState<string[]>(() =>
    defaultOpen ? groups.map((g) => g.id).filter((id) => !defaultOpen.includes(id)) : [],
  );
  const openIds = openProp ?? groups.map((g) => g.id).filter((id) => !closed.includes(id));

  // What moves rows: the data (by group and item key, not array identity — a parent that
  // re-renders during a resize must not reset the glide), the columns and the open groups.
  const shape = groups.map((g) => `${g.id}:${g.items.map(getKey).join(",")}`).join("|");
  const opened = openIds.join("|");

  // New data or columns: measure again before the frame paints.
  React.useLayoutEffect(() => {
    remeasureRef.current?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `shape` stands for `groups`.
  }, [shape, columns]);

  // Rows also move when the data or an open group changes: the next resize starts afresh.
  React.useLayoutEffect(() => {
    flipRef.current?.invalidate();
  }, [shape, columns, opened]);
  const setGroupOpen = (id: string, next: boolean) => {
    const ids = next ? [...openIds.filter((x) => x !== id), id] : openIds.filter((x) => x !== id);
    if (openProp === undefined) setClosed(next ? closed.filter((x) => x !== id) : [...closed, id]);
    onOpenChange?.(groups.map((g) => g.id).filter((g) => ids.includes(g)));
  };

  // Grid tracks: the title, then every column that has its own column, in drawing order. Until
  // the final layout the title track never gets narrower than its longest one-line title.
  const own = ordered.filter((c) => layout.modes[c.id] === "column");
  const track = new Map(own.map((c, i) => [c.id, i + 2]));
  const template = [
    layout.final ? "minmax(min(12rem,100%),1fr)" : "minmax(max-content,1fr)",
    ...own.map((c) => c.width ?? "auto"),
  ].join(" ");

  return (
    <div
      ref={ref}
      data-slot="grouped-table"
      data-tier={order.length - layout.step}
      data-final={layout.final ? "" : undefined}
      className={cn("relative flex w-full max-w-full min-w-0 flex-col", className)}
      style={style}
      {...props}
    >
      <div
        ref={cardRef}
        data-slot="grouped-table-frame"
        data-variant={variant}
        className={cn(
          "@container relative flex min-h-0 flex-1 flex-col overflow-hidden",
          variant === "card" && "surface-card [--card-padding:0]",
        )}
      >
        {toolbar != null && (
          <div
            data-slot="grouped-table-toolbar"
            className={cn(
              "flex shrink-0 items-center gap-2 border-b border-border/60 px-(--gt-pad) py-2.5",
              pad,
            )}
          >
            {toolbar}
          </div>
        )}
        <div
          ref={bodyRef}
          role="table"
          aria-label={ariaLabel}
          aria-labelledby={ariaLabelledBy}
          data-slot="grouped-table-body"
          className={cn(
            "relative grid min-h-0 flex-1 [scrollbar-width:thin] [scrollbar-color:var(--field-edge)_transparent] content-start gap-x-[clamp(0.75rem,calc(4.6cqi_-_0.45rem),1.75rem)] overflow-auto font-body text-sm",
            pad,
          )}
          style={{ gridTemplateColumns: template }}
        >
          <div role="rowgroup" className="sr-only">
            <div role="row">
              <span role="columnheader">{titleLabel}</span>
              {ordered.map((c) => (
                <span role="columnheader" key={c.id}>
                  {c.label}
                </span>
              ))}
            </div>
          </div>
          {groups.map((group, index) => (
            <GroupedTableSection
              key={group.id}
              group={group}
              first={index === 0}
              open={openIds.includes(group.id)}
              onOpenChange={(next) => setGroupOpen(group.id, next)}
              ordered={ordered}
              layout={layout}
              track={track}
              title={title}
              leading={leading}
              getKey={getKey}
              onAdd={onAdd}
              addLabel={addLabel}
              sticky={stickyHeaders}
              animated={animated}
            />
          ))}
        </div>
        <div
          ref={layerRef}
          aria-hidden
          data-slot="grouped-table-ghosts"
          className="pointer-events-none absolute inset-0 z-3 overflow-hidden text-sm"
        />
      </div>
    </div>
  );
}

/* ── Group ─────────────────────────────────────────────────────────────────── */

interface SectionProps<T> {
  group: GroupedTableGroup<T>;
  first: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ordered: GroupedTableColumn<T>[];
  layout: Layout;
  track: Map<string, number>;
  title: (item: T) => React.ReactNode;
  leading?: (item: T) => React.ReactNode;
  getKey: (item: T) => React.Key;
  onAdd?: (groupId: string) => void;
  addLabel?: (group: GroupedTableGroup<T>) => string;
  sticky: boolean;
  animated: boolean;
}

function GroupedTableSection<T>({
  group,
  first,
  open,
  onOpenChange,
  ordered,
  layout,
  track,
  title,
  leading,
  getKey,
  onAdd,
  addLabel,
  sticky,
  animated,
}: SectionProps<T>) {
  const name = group.name ?? (typeof group.label === "string" ? group.label : group.id);
  const headers = layout.inlined ? [] : ordered.filter((c) => track.has(c.id));

  return (
    <BaseCollapsible.Root
      open={open}
      onOpenChange={onOpenChange}
      role="rowgroup"
      data-slot="grouped-table-group"
      className="col-span-full grid grid-cols-subgrid"
    >
      <div
        role="row"
        data-slot="grouped-table-group-header"
        data-flip="band"
        className={cn(
          "col-span-full grid grid-cols-subgrid border-b border-border/60 bg-control-surface",
          !first && "border-t",
          sticky && "sticky top-0 z-2",
        )}
      >
        <div
          role="rowheader"
          aria-colspan={ordered.length + 1}
          data-slot="grouped-table-band"
          className="col-span-full grid min-h-(--control-h) grid-cols-subgrid items-center px-(--gt-pad) text-left font-normal"
        >
          <div className="col-start-1 flex min-w-0 items-center gap-1">
            <BaseCollapsible.Trigger
              data-slot="grouped-table-trigger"
              data-flip="piece"
              className="group/gt-trigger -ml-2 inline-flex min-h-(--control-h-sm) min-w-0 cursor-pointer items-center gap-2 rounded-item material-row px-2 focus-ring select-none"
            >
              <ChevronRightIcon
                aria-hidden
                className="size-3.5 shrink-0 text-muted-foreground transition-[rotate] duration-(--duration) ease-(--ease) group-data-panel-open/gt-trigger:rotate-90"
              />
              {group.icon != null && (
                <span
                  aria-hidden
                  className="flex shrink-0 text-foreground-soft [&_svg]:size-4 [&_svg]:shrink-0"
                >
                  {group.icon}
                </span>
              )}
              <span className="min-w-0 truncate text-[13px] font-medium text-foreground">{group.label}</span>
              <Badge variant="secondary" size="sm" data-slot="grouped-table-count">
                {group.items.length}
              </Badge>
            </BaseCollapsible.Trigger>
            {onAdd && (
              <Button
                variant="ghost"
                size="icon-sm"
                data-slot="grouped-table-add"
                aria-label={addLabel ? addLabel(group) : `Add to ${name}`}
                onClick={() => onAdd(group.id)}
                className="text-muted-foreground hover:text-foreground"
              >
                <PlusIcon aria-hidden className="size-3.5" />
              </Button>
            )}
          </div>
          {headers.map((c) => (
            <span
              key={c.id}
              aria-hidden
              title={c.label}
              data-flip="piece"
              data-flip-key={`${group.id}:${c.id}`}
              data-column-header={c.id}
              data-align={c.align ?? "start"}
              className={cn(
                "flex items-center text-muted-foreground [&_svg]:size-3.5 [&_svg]:shrink-0",
                c.align === "end" ? "justify-self-end" : "justify-self-start",
              )}
              style={{ gridColumn: track.get(c.id) }}
            >
              {c.header ?? <span className="eyebrow">{c.label}</span>}
            </span>
          ))}
        </div>
      </div>
      <BaseCollapsible.Panel
        role="none"
        data-slot="grouped-table-panel"
        className={cn(
          "col-span-full grid h-(--collapsible-panel-height) grid-cols-subgrid content-start overflow-hidden",
          "data-ending-style:h-0 in-data-gliding:data-open:overflow-visible data-starting-style:h-0",
          animated && "transition-[height] duration-(--duration) ease-(--ease)",
        )}
      >
        {group.items.map((item) => {
          const key = getKey(item);
          return (
            <GroupedTableRow
              key={key}
              rowKey={String(key)}
              item={item}
              ordered={ordered}
              layout={layout}
              track={track}
              title={title}
              leading={leading}
            />
          );
        })}
      </BaseCollapsible.Panel>
    </BaseCollapsible.Root>
  );
}

/* ── Row ───────────────────────────────────────────────────────────────────── */

interface RowProps<T> {
  item: T;
  rowKey: string;
  ordered: GroupedTableColumn<T>[];
  layout: Layout;
  track: Map<string, number>;
  title: (item: T) => React.ReactNode;
  leading?: (item: T) => React.ReactNode;
}

function GroupedTableRow<T>({ item, rowKey, ordered, layout, track, title, leading }: RowProps<T>) {
  // Inline columns move into the title in the order they are drawn, so the cells inside the
  // wrapper are always a prefix and the DOM order never changes. The ones still in their own
  // column sit outside the wrapper, where they are grid items.
  const inside = ordered.filter((c) => c.inline && layout.modes[c.id] === "inline");
  const outside = ordered.filter((c) => !inside.includes(c));
  // Titles keep to one line until the final layout; only there may they wrap.
  const wrap = layout.final;

  const cell = (c: GroupedTableColumn<T>) => {
    const mode = layout.modes[c.id] ?? "hidden";
    return (
      <div
        key={c.id}
        role="cell"
        data-slot="grouped-table-cell"
        data-column={c.id}
        data-mode={mode}
        data-align={c.align ?? "start"}
        data-flip="piece"
        data-flip-key={`${rowKey}:${c.id}`}
        className={cn(
          "min-h-5.5 min-w-0 items-center text-[13px] whitespace-nowrap text-foreground-soft",
          mode === "column" && "flex",
          mode === "column" && (c.align === "end" ? "justify-self-end text-right" : "justify-self-start"),
          // Inline cells flow right after the title's last word, and wrap like words.
          mode === "inline" && "inline-flex align-top",
          mode === "hidden" && "sr-only",
        )}
        style={mode === "column" ? { gridColumn: track.get(c.id) } : undefined}
      >
        {c.cell(item, { mode })}
      </div>
    );
  };

  return (
    <div
      role="row"
      data-slot="grouped-table-row"
      data-flip="row"
      className={cn(
        "relative col-span-full grid grid-cols-subgrid items-start border-t border-border/60 px-(--gt-pad) py-3 first:border-t-0",
        "transition-colors duration-(--duration) ease-(--ease) hover:bg-control-hover/50",
      )}
    >
      <div
        role="none"
        data-slot="grouped-table-lead"
        className={cn(
          layout.inlined ? "col-start-1 block text-sm leading-5.5" : "contents",
          layout.inlined && (wrap ? "min-w-0 text-pretty" : "whitespace-nowrap"),
          layout.inlined && leading && "ps-6",
        )}
      >
        {/* The title does not glide: it starts at the same place in every layout and moves with its row. */}
        <div
          role="rowheader"
          data-slot="grouped-table-title"
          className={cn(
            "text-sm leading-5.5 font-normal text-foreground",
            layout.inlined ? "inline" : "col-start-1 flex items-start",
            wrap && "min-w-0",
          )}
        >
          {leading && (
            <span
              data-slot="grouped-table-leading"
              className={cn(
                "h-5.5 w-6 shrink-0 items-center align-top text-muted-foreground [&_svg]:size-3.5",
                layout.inlined ? "-ms-6 inline-flex" : "flex",
              )}
            >
              {leading(item)}
            </span>
          )}
          <span
            data-slot="grouped-table-title-text"
            className={wrap ? "min-w-0 text-pretty break-words" : "whitespace-nowrap"}
          >
            {title(item)}
          </span>
        </div>
        {inside.map((c) => (
          <React.Fragment key={c.id}>
            {/* An en space, not a margin: it hangs at a line end, so a wrapped cell starts flush. */}
            {"\u2002"}
            {cell(c)}
          </React.Fragment>
        ))}
      </div>
      {outside.map(cell)}
    </div>
  );
}
