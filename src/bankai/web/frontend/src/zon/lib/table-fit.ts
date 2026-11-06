/*
 * The engine behind `<Table fit>`: which columns keep their own column at the container's width
 * (the fit), and how cells glide to their new places when that changes (FLIP).
 *
 * Columns step out one at a time, lowest `priority` first, the moment the widest title no longer
 * fits on one line beside them; an `inline` column moves into the title cell instead. Only once
 * every column that can step out has done so may titles wrap.
 */

/* ── Types ─────────────────────────────────────────────────────────────────── */

/** Where a column's cell is drawn at the current width. */
export type FitMode = "column" | "inline" | "hidden";

export interface FitColumn {
  /** Stable id, unique among the columns (the column's index in the table). */
  id: string;
  /** Lower steps out first. A column with no `priority`, `inline` or `minWidth` never steps out. */
  priority?: number;
  /** When the column steps out, show its cell right after the title instead of hiding it. */
  inline?: boolean;
  /** Optional floor, in rem of container width: below it the column steps out regardless. */
  minWidth?: number;
  /** The grid track while the column has its own column. Defaults to `auto`. */
  width?: string;
}

export interface FitLayout {
  /** How many columns have stepped out, in removal order. */
  step: number;
  /** Every removable column is out or inline: the only layout in which titles may wrap. */
  final: boolean;
  modes: Record<string, FitMode>;
  /** True while at least one column is drawn inside the title cell. */
  inlined: boolean;
}

/* ── Fit ───────────────────────────────────────────────────────────────────── */

/** The columns that can step out, in the order they do. The title never does. */
export function removalOrder(columns: FitColumn[], title: string) {
  return columns
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => c.id !== title && (c.priority != null || c.inline || (c.minWidth ?? 0) > 0))
    .sort((a, b) => (a.c.priority ?? 0) - (b.c.priority ?? 0) || b.i - a.i)
    .map(({ c }) => c);
}

/**
 * The order columns are drawn in: those before the title as given, the title, then its inline
 * columns in the order they step out, then the others as given. Rows keep this DOM order at
 * every width, so what is read matches what is seen.
 */
export function drawOrder(columns: FitColumn[], title: string) {
  const at = columns.findIndex((c) => c.id === title);
  const before = columns.slice(0, Math.max(at, 0));
  const after = columns.slice(at + 1);
  const inline = removalOrder(after, title).filter((c) => c.inline);
  return [...before, ...columns.slice(at, at + 1), ...inline, ...after.filter((c) => !c.inline)];
}

export function layoutFor(step: number, order: FitColumn[], columns: FitColumn[]): FitLayout {
  const out = new Set(order.slice(0, step).map((c) => c.id));
  const modes: Record<string, FitMode> = {};
  for (const c of columns) modes[c.id] = !out.has(c.id) ? "column" : c.inline ? "inline" : "hidden";
  return {
    step,
    final: step >= order.length,
    modes,
    inlined: Object.values(modes).includes("inline"),
  };
}

/** Natural, single-line widths, measured once per data or font change. */
export interface FitMetrics {
  /** Each row: its title on one line and each cell's width. Header rows carry no inline cells. */
  rows: { title: number; cells: Record<string, number>; head: boolean }[];
  /** Each column's track while it has its own column, header included. */
  tracks: Record<string, number>;
  /** The en space between the title and an inline cell. */
  space: number;
}

/** Restoring a column needs this much more room than keeping it: no flicker at the boundary. */
const HYSTERESIS_PX = 8;

const UNCLIP =
  "position:static;display:inline-flex;width:max-content;min-width:0;height:auto;overflow:visible;" +
  "clip:auto;clip-path:none;margin:0;padding:0;white-space:nowrap;";

/**
 * Clones every title and cell into a hidden probe with `white-space: nowrap`, reads all widths
 * in one pass and removes the probe — before anything paints. Rows of closed groups count too,
 * so opening a group never changes the layout.
 */
/**
 * bankai: how many body rows are measured at most. A long table's widest cells are found among
 * the rows whose text is longest, column by column, and the first few drawn: measuring every
 * row of a 243-row library (a clone of each cell, laid out) took seconds on every change.
 */
const MEASURED_ROWS = 48;

function rowsToMeasure(body: HTMLElement): HTMLElement[] {
  const all = [...body.querySelectorAll<HTMLElement>("[data-slot=table-row]")];
  if (all.length <= MEASURED_ROWS) return all;
  const picked = new Set<HTMLElement>();
  const longest = new Map<string, Array<[number, HTMLElement]>>();
  const note = (key: string, length: number, row: HTMLElement) => {
    const list = longest.get(key) ?? [];
    list.push([length, row]);
    longest.set(key, list);
  };
  const body_rows: HTMLElement[] = [];
  for (const row of all) {
    if (row.closest("[data-slot=table-header]")) {
      picked.add(row);
      continue;
    }
    body_rows.push(row);
    note("title", row.querySelector("[data-slot=table-title-text]")?.textContent?.length ?? 0, row);
    for (const cell of row.querySelectorAll<HTMLElement>("[data-column]")) {
      note(cell.getAttribute("data-column")!, cell.textContent?.length ?? 0, row);
    }
  }
  const each = Math.max(2, Math.ceil(MEASURED_ROWS / Math.max(1, longest.size)));
  for (const list of longest.values()) {
    list.sort((a, b) => b[0] - a[0]);
    for (const [, row] of list.slice(0, each)) picked.add(row);
  }
  for (const row of body_rows.slice(0, 8)) picked.add(row);
  return all.filter((row) => picked.has(row));
}

export function measureMetrics(root: HTMLElement, body: HTMLElement, columns: FitColumn[]): FitMetrics {
  const probe = document.createElement("div");
  probe.setAttribute("aria-hidden", "true");
  probe.className = "font-body text-sm";
  probe.style.cssText =
    "position:absolute;top:0;left:0;visibility:hidden;pointer-events:none;white-space:nowrap;width:max-content;contain:layout style;";
  const space = probe.appendChild(document.createElement("span"));
  space.textContent = " ";
  const grids = new Map<string, HTMLElement>();
  for (const c of columns) {
    const grid = probe.appendChild(document.createElement("div"));
    grid.style.cssText = `display:grid;width:max-content;grid-template-columns:${c.width ?? "auto"};`;
    grids.set(c.id, grid);
  }
  // A title takes its type from its cell (an eyebrow in the header, 13px in a dense row).
  const fonts = new Map<string, string>();
  const fontOf = (cell: HTMLElement) => {
    let font = fonts.get(cell.className);
    if (font == null) {
      const cs = getComputedStyle(cell);
      font = `font:${cs.font};letter-spacing:${cs.letterSpacing};text-transform:${cs.textTransform};`;
      fonts.set(cell.className, font);
    }
    return font;
  };
  const rows = rowsToMeasure(body);
  const probes = rows.map((row) => {
    const text = row.querySelector<HTMLElement>("[data-slot=table-title-text]");
    const cell = text?.closest<HTMLElement>("[data-title]");
    const title = text ? (text.cloneNode(true) as HTMLElement) : null;
    if (title) {
      title.style.cssText = UNCLIP + (cell ? fontOf(cell) : "");
      probe.append(title);
    }
    const cells: [string, HTMLElement][] = [];
    for (const el of row.querySelectorAll<HTMLElement>("[data-column]")) {
      const id = el.getAttribute("data-column")!;
      const clone = el.cloneNode(true) as HTMLElement;
      clone.style.cssText = UNCLIP;
      grids.get(id)?.append(clone);
      cells.push([id, clone]);
    }
    return { title, cells, head: !!row.closest("[data-slot=table-header]") };
  });
  root.append(probe);
  const metrics: FitMetrics = {
    space: space.getBoundingClientRect().width,
    tracks: Object.fromEntries([...grids].map(([id, g]) => [id, g.getBoundingClientRect().width])),
    rows: probes.map((p) => ({
      title: p.title?.getBoundingClientRect().width ?? 0,
      cells: Object.fromEntries(p.cells.map(([id, el]) => [id, el.getBoundingClientRect().width])),
      head: p.head,
    })),
  };
  probe.remove();
  return metrics;
}

/** The width a layout needs with every title on one line. */
function widthNeeded(
  layout: FitLayout,
  drawn: FitColumn[],
  title: string,
  m: FitMetrics,
  edge: number,
  gap: number,
) {
  const inline = drawn.filter((c) => layout.modes[c.id] === "inline");
  const own = drawn.filter((c) => layout.modes[c.id] === "column");
  let widest = 0;
  for (const row of m.rows) {
    let w = row.title;
    if (!row.head) for (const c of inline) w += m.space + (row.cells[c.id] ?? 0);
    widest = Math.max(widest, w);
  }
  // The rows' padding sits inside the edge tracks, which it grows when they size to their
  // content (the title's, an `auto` one) and squeezes into when they are fixed.
  const grows = (c: FitColumn | undefined) => !c || c.id === title || (c.width ?? "auto") === "auto";
  const tracks = own.reduce(
    (sum, c) => sum + (c.id === title ? Math.ceil(widest) : (m.tracks[c.id] ?? 0)),
    0,
  );
  return (
    tracks + gap * Math.max(own.length - 1, 0) + (grows(own[0]) ? edge : 0) + (grows(own.at(-1)) ? edge : 0)
  );
}

/**
 * The fewest columns to step out so the widest title stays on one line. Stepping out happens
 * the moment it no longer fits; stepping back needs `HYSTERESIS_PX` to spare.
 */
export function fitStep(options: {
  available: number;
  current: number;
  order: FitColumn[];
  columns: FitColumn[];
  drawn: FitColumn[];
  title: string;
  metrics: FitMetrics;
  edge: number;
  gap: number;
  widthRem: number;
}) {
  const { available, current, order, columns, drawn, title, metrics, edge, gap, widthRem } = options;
  let floor = 0;
  order.forEach((c, i) => {
    if ((c.minWidth ?? 0) > widthRem) floor = i + 1;
  });
  for (let step = floor; step < order.length; step++) {
    const need = widthNeeded(layoutFor(step, order, columns), drawn, title, metrics, edge, gap);
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

/** Rows of a closed group are not drawn. */
const CLOSED = "[data-slot=table-group][data-closed] > [data-slot=table-row]";
const isDrawn = (el: Element) => !el.closest("[data-mode=hidden]") && !el.closest(CLOSED);
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
 *   neighbours glide over. Interrupted, it starts again from what is drawn: positions
 *   mid-glide, cells mid-fade, ghosts still fading out.
 * - `reflow()` runs on every other resize. In the final layout, where titles wrap, a piece that
 *   changed line glides there instead of jumping; pieces that only follow the width keep
 *   following the pointer.
 *
 * Each pass reads every rect first and writes after, so there is one forced layout per change.
 */
export class Flip {
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
    // bankai: only what is within a view's height of the view glides; the rest of a long table
    // moves at once, unseen (hundreds of rows animated made every step of a drag stutter). Read
    // by the row each piece is in, its own box only for those near: a row out of view may skip
    // its layout (content-visibility), and reading inside it would lay it out.
    const near = (r: DOMRect) => r.bottom >= box.top - box.height && r.top <= box.bottom + box.height;
    const rows = new Map<Element, boolean>();
    const els = [...this.root.querySelectorAll("[data-flip]")].filter(isDrawn).filter((el) => {
      const row = el.closest("[data-flip=row],[data-flip=band]") ?? el;
      let ok = rows.get(row);
      if (ok === undefined) {
        ok = near(row.getBoundingClientRect());
        rows.set(row, ok);
      }
      return ok;
    });
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
   * at once, those below with the last one in view.
   */
  private cascade(rects: Map<Element, DOMRect>, view: DOMRect) {
    const index = new Map<Element, number>();
    const below: Element[] = [];
    let count = 0;
    for (const el of this.root.querySelectorAll("[data-flip=band],[data-flip=row]")) {
      const r = rects.get(el);
      if (!r) continue;
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
  tier(next: FitLayout, commit: () => void) {
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
        if (!column) return false;
        // A header leaves with its column, and when the column moves into the titles.
        const mode = next.modes[column];
        return el.hasAttribute("data-head") ? mode !== "column" : mode === "hidden";
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
      clone.removeAttribute("role");
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

export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
