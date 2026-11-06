import { ChevronRightIcon } from "lucide-react";
import * as React from "react";
import { flushSync } from "react-dom";
import { cn } from "@/zon/lib/cn";
import { mergeRefs } from "@/zon/lib/merge-refs";
import {
  Flip,
  drawOrder,
  fitStep,
  layoutFor,
  measureMetrics,
  reducedMotion,
  removalOrder,
  type FitColumn,
  type FitMetrics,
  type FitMode,
} from "@/zon/lib/table-fit";

type DivProps = React.ComponentProps<"div">;

const TableDenseContext = React.createContext(false);

/* ── Fit: shared state ─────────────────────────────────────────────────────── */

/** What a `TableHead` tells a fitting table about its column. */
interface FitColumnOptions {
  priority?: number;
  inline?: boolean;
  minWidth?: number;
  width?: string;
  title?: boolean;
  end: boolean;
}

interface TableFitValue {
  register: (index: number, options: FitColumnOptions) => () => void;
  /** Columns, as registered by the header row. */
  count: number;
  /** The title column's index. */
  title: number;
  /** Column indices in the order they are drawn. */
  order: number[];
  modes: FitMode[];
  align: ("start" | "end")[];
  /** Each column's grid track while it has its own column. */
  track: Map<number, number>;
  inlined: boolean;
  final: boolean;
}

const TableFitContext = React.createContext<TableFitValue | null>(null);
const TableSectionContext = React.createContext<"head" | "body">("body");

/** Where a cell sits, given to it by its row in a fitting table. */
interface TableCellSlot {
  index: number;
  rowKey: string;
  head: boolean;
  /** The row has one cell per column, so its cells follow the columns. */
  mapped: boolean;
}

const TableCellSlotContext = React.createContext<TableCellSlot | null>(null);

const SUBGRID = "col-span-full grid grid-cols-subgrid";

/* ── Table ─────────────────────────────────────────────────────────────────── */

export interface TableProps extends React.ComponentProps<"table"> {
  /** 30px rows for data-heavy screens (admin, dashboards). Default rows are 46px. */
  dense?: boolean;
  /**
   * `card` (default): the table as an object, a 20px card. `plain`: the same table without the
   * card — no edge, corners, shadow or ground — across the full width of its column. Always
   * `plain` inside a `Card`, a `Dialog` or a `Drawer`: never a card in a card.
   */
  variant?: "card" | "plain";
  /** Props for the container that wraps and scrolls the table (the card, unless `plain`). */
  containerProps?: React.ComponentProps<"div">;
  /**
   * Fit the columns to the container's width. When the widest title no longer fits on one line,
   * columns step out one at a time by their `TableHead`'s `priority` — or, with `inline`, move
   * into the title cell, right after the title — and every cell glides to its new place.
   */
  fit?: boolean;
}

/**
 * Table — tabular data as an **object**: a 20px card in the `surface-card` material that
 * scrolls horizontally when the columns do not fit. Rows are separated by hairlines and take
 * a subtle `--control-hover` on hover; headers are eyebrows; numbers are mono and tabular.
 *
 * The card's padding is carried by the cells so rules and hover run edge to edge — do not
 * pad the table at the call site. `variant="plain"` is the same table without the card, for
 * tables that are part of the page rather than an object on it. The rule people break: zebra
 * stripes, vertical rules or a second border around the table. Hairlines between rows are the whole grid.
 *
 * With `fit`, the columns follow the **container's** width instead of scrolling: see `fit`.
 */
export function Table({ fit = false, ...props }: TableProps) {
  return fit ? <FitTable {...props} /> : <StaticTable {...props} />;
}

function StaticTable({ dense = false, variant = "card", containerProps, className, ...props }: TableProps) {
  return (
    <TableDenseContext.Provider value={dense}>
      <div
        data-slot="table-container"
        data-variant={variant}
        {...containerProps}
        className={cn(
          "relative w-full overflow-x-auto",
          variant === "card" && "surface-card [--card-padding:0] [--scrollbar-inset:var(--radius-card)]",
          containerProps?.className,
        )}
      >
        <table
          data-slot="table"
          data-dense={dense ? "" : undefined}
          data-variant={variant}
          className={cn("w-full caption-bottom border-collapse text-left font-body text-sm", className)}
          {...props}
        />
      </div>
    </TableDenseContext.Provider>
  );
}

/** Card padding, carried by the rows in a fitting table: 16px, 24px from a 40rem container. */
const FIT_PAD = "[--table-pad:--spacing(4)] @min-[40rem]:[--table-pad:--spacing(6)]";

/**
 * A table that fits its columns to its container. It is one table at every width — `role="table"`
 * on a CSS grid whose groups and rows are subgrids — so a column is as wide as its widest cell
 * in every group, and cells keep their DOM order whatever is drawn where.
 */
function FitTable({
  dense = false,
  variant = "card",
  containerProps,
  className,
  style,
  ref,
  children,
  ...props
}: Omit<TableProps, "fit">) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const bodyRef = React.useRef<HTMLDivElement | null>(null);
  const layerRef = React.useRef<HTMLDivElement | null>(null);
  const flipRef = React.useRef<Flip | null>(null);
  const remeasureRef = React.useRef<((from: "effect" | "observer") => void) | null>(null);
  // bankai: the last measurements, kept across a hide and show (a page kept in an Activity):
  // shown again at the same width with the same rows, the table is not measured again.
  const keptRef = React.useRef<{ metrics: FitMetrics; rows: number; width: number } | null>(null);

  // The header row's `TableHead`s register their columns as they mount and change.
  const registered = React.useRef(new Map<number, FitColumnOptions>());
  const [version, setVersion] = React.useState(0);
  const register = React.useCallback((index: number, options: FitColumnOptions) => {
    registered.current.set(index, options);
    setVersion((v) => v + 1);
    return () => {
      if (registered.current.get(index) !== options) return;
      registered.current.delete(index);
      setVersion((v) => v + 1);
    };
  }, []);

  const { columns, title, align } = React.useMemo(() => {
    const map = registered.current;
    const count = map.size ? Math.max(...map.keys()) + 1 : 0;
    const all = Array.from({ length: count }, (_, i) => map.get(i));
    // The title is the column marked `titleColumn`, or the first. It never steps out, and
    // columns before it step out but never move inline.
    const at = Math.max(
      0,
      all.findIndex((o) => o?.title),
    );
    const columns: FitColumn[] = all.map((o, i) => ({
      id: String(i),
      priority: i === at ? undefined : o?.priority,
      inline: i > at && !!o?.inline,
      minWidth: i === at ? undefined : o?.minWidth,
      width: o?.width,
    }));
    return {
      columns,
      title: String(at),
      align: all.map((o) => (o?.end ? "end" : "start") as "start" | "end"),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `version` stands for the registered columns.
  }, [version]);

  const order = React.useMemo(() => removalOrder(columns, title), [columns, title]);
  const drawn = React.useMemo(() => drawOrder(columns, title), [columns, title]);
  const [step, setStep] = React.useState(0);
  const layout = React.useMemo(
    () => layoutFor(Math.min(step, order.length), order, columns),
    [step, order, columns],
  );

  const latest = React.useRef({ step, order, columns, drawn, title });
  latest.current = { step, order, columns, drawn, title };

  React.useLayoutEffect(() => {
    const container = containerRef.current;
    const body = bodyRef.current;
    const layer = layerRef.current;
    if (!container || !body || !layer) return;
    const flip = new Flip(container, body, layer);
    flipRef.current = flip;
    let metrics: FitMetrics | null = null;
    const rem = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;

    /**
     * Places the current width. From a layout effect the new layout commits before paint and
     * must not (and need not) be flushed; from a resize it is flushed so the glide can measure
     * it; from an observer (new rows, fonts) it lands at once, without a glide.
     */
    const evaluate = (from: "effect" | "observer" | "resize") => {
      if (!metrics) return;
      const { step, order, columns, drawn, title } = latest.current;
      const row = body.querySelector("[data-slot=table-row]");
      const edge = row ? parseFloat(getComputedStyle(row).paddingLeft) : 16;
      const gap = parseFloat(getComputedStyle(body).columnGap) || 0;
      const next = fitStep({
        available: body.clientWidth,
        current: step,
        order,
        columns,
        drawn,
        title,
        metrics,
        edge,
        gap,
        widthRem: container.clientWidth / rem(),
      });
      if (next === step) {
        if (from === "resize" && !reducedMotion()) flip.reflow();
        return;
      }
      if (from === "effect") return setStep(next);
      const commit = () => flushSync(() => setStep(next));
      if (from === "resize" && !reducedMotion()) flip.tier(layoutFor(next, order, columns), commit);
      else commit();
    };
    const rowCount = () => body.querySelectorAll("[data-slot=table-row]").length;
    const remeasure = (from: "effect" | "observer") => {
      metrics = measureMetrics(container, body, latest.current.columns);
      keptRef.current = { metrics, rows: rowCount(), width: container.clientWidth };
      evaluate(from);
    };
    remeasureRef.current = remeasure;
    // Before the first paint: measure, and start in the layout that fits, without a glide.
    const kept = keptRef.current;
    if (kept && kept.rows === rowCount() && kept.width === container.clientWidth) {
      metrics = kept.metrics;
      evaluate("effect");
    } else {
      remeasure("effect");
    }

    const ro = new ResizeObserver(() => evaluate("resize"));
    ro.observe(container);

    // New, sorted or filtered rows and changed cell content are measured again. The cells a
    // layout change moves (between a row and its title) are not content: their records target
    // the row or the title's wrapper, not the inside of a cell.
    const isRow = (n: Node) =>
      n instanceof Element &&
      (n.matches("[data-slot=table-row]") || !!n.querySelector("[data-slot=table-row]"));
    const changed = (r: MutationRecord) => {
      if (r.type === "characterData") return true;
      if ([...r.addedNodes, ...r.removedNodes].some(isRow)) return true;
      const target = r.target instanceof Element ? r.target : r.target.parentElement;
      return !!target?.closest("[data-slot=table-cell],[data-slot=table-head]");
    };
    const mo = new MutationObserver((records) => {
      // A group that opens or closes moves the rows under it: the next resize starts afresh.
      flip.invalidate();
      if (records.some((r) => r.type !== "attributes" && changed(r))) remeasure("observer");
    });
    mo.observe(body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["data-closed"],
    });
    // Titles are measured in the real fonts once they load.
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    let alive = true;
    fonts?.ready.then(() => alive && remeasure("observer"));
    const onFonts = () => remeasure("observer");
    fonts?.addEventListener("loadingdone", onFonts);
    const onScroll = () => flip.invalidate();
    body.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      alive = false;
      ro.disconnect();
      mo.disconnect();
      fonts?.removeEventListener("loadingdone", onFonts);
      body.removeEventListener("scroll", onScroll);
      flip.cancel();
      flipRef.current = null;
      remeasureRef.current = null;
    };
  }, []);

  // New columns: measure again before the frame paints.
  React.useLayoutEffect(() => {
    flipRef.current?.invalidate();
    remeasureRef.current?.("effect");
  }, [columns]);

  // Grid tracks: every column that has its own column, in drawing order. Until the final layout
  // the title's track never gets narrower than its longest one-line title.
  const { track, template } = React.useMemo(() => {
    const own = drawn.filter((c) => layout.modes[c.id] === "column");
    return {
      track: new Map(own.map((c, i) => [Number(c.id), i + 1])),
      template: own.length
        ? own
            .map((c) =>
              c.id === title
                ? layout.final
                  ? "minmax(min(12rem,100%),1fr)"
                  : "minmax(max-content,1fr)"
                : (c.width ?? "auto"),
            )
            .join(" ")
        : undefined,
    };
  }, [drawn, layout, title]);

  const value = React.useMemo<TableFitValue>(
    () => ({
      register,
      count: columns.length,
      title: Number(title),
      order: drawn.map((c) => Number(c.id)),
      modes: columns.map((c) => layout.modes[c.id] ?? "column"),
      align,
      track,
      inlined: layout.inlined,
      final: layout.final,
    }),
    [register, columns, title, drawn, layout, align, track],
  );

  const containerRefs = React.useMemo(
    () => mergeRefs(containerRef, containerProps?.ref),
    [containerProps?.ref],
  );
  const bodyRefs = React.useMemo(() => mergeRefs(bodyRef, ref as React.Ref<HTMLDivElement>), [ref]);

  return (
    <TableFitContext.Provider value={value}>
      <TableDenseContext.Provider value={dense}>
        <div
          data-slot="table-container"
          data-variant={variant}
          data-fit=""
          data-tier={order.length - layout.step}
          data-final={layout.final ? "" : undefined}
          {...containerProps}
          ref={containerRefs}
          className={cn(
            "@container relative flex w-full max-w-full min-w-0 flex-col overflow-hidden",
            variant === "card" && "surface-card [--card-padding:0] [--scrollbar-inset:var(--radius-card)]",
            containerProps?.className,
          )}
        >
          <div
            {...(props as DivProps)}
            ref={bodyRefs}
            role="table"
            data-slot="table"
            data-fit=""
            data-dense={dense ? "" : undefined}
            data-variant={variant}
            className={cn(
              "relative grid min-h-0 flex-1 content-start gap-x-[clamp(0.75rem,calc(4.6cqi_-_0.45rem),1.75rem)] overflow-auto text-left font-body text-sm",
              FIT_PAD,
              className,
            )}
            style={{ gridTemplateColumns: template, ...style }}
          >
            {children}
          </div>
          {/* Cells that step out fade here, where they stood, while their neighbours glide over. */}
          <div
            ref={layerRef}
            aria-hidden
            data-slot="table-ghosts"
            className="pointer-events-none absolute inset-0 z-3 overflow-hidden font-body text-sm"
          />
        </div>
      </TableDenseContext.Provider>
    </TableFitContext.Provider>
  );
}

/* ── Sections ──────────────────────────────────────────────────────────────── */

export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  const fit = React.useContext(TableFitContext);
  return (
    <TableSectionContext.Provider value="head">
      {fit ? (
        <div
          role="rowgroup"
          data-slot="table-header"
          className={cn(SUBGRID, "[&_[data-slot=table-row]]:hover:bg-transparent", className)}
          {...(props as DivProps)}
        />
      ) : (
        <thead
          data-slot="table-header"
          className={cn("[&_tr]:border-b [&_tr]:border-border/60 [&_tr]:hover:bg-transparent", className)}
          {...props}
        />
      )}
    </TableSectionContext.Provider>
  );
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  const fit = React.useContext(TableFitContext);
  if (fit)
    return (
      <div
        role="rowgroup"
        data-slot="table-body"
        className={cn(SUBGRID, "[&>[data-slot=table-row]:last-child]:border-b-0", className)}
        {...(props as DivProps)}
      />
    );
  return (
    <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-b-0", className)} {...props} />
  );
}

export function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  const fit = React.useContext(TableFitContext);
  const classes = "border-t border-border/60 font-medium text-foreground";
  if (fit)
    return (
      <div
        role="rowgroup"
        data-slot="table-footer"
        className={cn(SUBGRID, classes, "[&_[data-slot=table-row]]:hover:bg-transparent", className)}
        {...(props as DivProps)}
      />
    );
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(classes, "[&_tr]:hover:bg-transparent", className)}
      {...props}
    />
  );
}

/* ── Rows and cells ────────────────────────────────────────────────────────── */

/**
 * A row. Mark a selected row with `aria-selected` / `data-selected`.
 *
 * In a fitting table its cells follow the header's columns by position — give it one cell per
 * column, as direct children — and it draws them where the layout says: in their own column,
 * inline after the title, or only for screen readers.
 */
export function TableRow({ className, children, ...props }: React.ComponentProps<"tr">) {
  const fit = React.useContext(TableFitContext);
  const head = React.useContext(TableSectionContext) === "head";
  const dense = React.useContext(TableDenseContext);
  const rowKey = React.useId();
  const rowClasses = cn(
    "border-b border-border/60 transition-colors duration-(--duration) ease-(--ease)",
    "hover:bg-control-hover/50 aria-selected:bg-control-hover data-selected:bg-control-hover",
  );

  if (!fit)
    return (
      <tr data-slot="table-row" className={cn(rowClasses, className)} {...props}>
        {children}
      </tr>
    );

  const cells = React.Children.toArray(children).filter(React.isValidElement);
  const mapped = fit.count > 0 && cells.length === fit.count;
  const at = (i: number) => (
    <TableCellSlotContext.Provider key={cells[i]!.key ?? i} value={{ index: i, rowKey, head, mapped }}>
      {cells[i]}
    </TableCellSlotContext.Provider>
  );

  let content: React.ReactNode;
  if (!mapped) content = cells.map((_, i) => at(i));
  else if (head) content = fit.order.map(at);
  else {
    // Inline columns move into the title in the order they are drawn, so the cells inside the
    // wrapper are always a prefix and the DOM order never changes. The others sit outside it,
    // where they are grid items.
    const t = fit.order.indexOf(fit.title);
    const inside = fit.order.filter((i) => fit.modes[i] === "inline");
    const outside = fit.order.slice(t + 1).filter((i) => !inside.includes(i));
    content = (
      <>
        {fit.order.slice(0, t).map(at)}
        <div
          role="none"
          data-slot="table-lead"
          className={cn(
            fit.inlined ? "block min-w-0 py-1" : "contents",
            fit.inlined && (fit.final ? "text-pretty" : "whitespace-nowrap"),
          )}
          style={fit.inlined ? { gridColumn: fit.track.get(fit.title) } : undefined}
        >
          {at(fit.title)}
          {inside.map((i) => (
            <React.Fragment key={i}>
              {/* An en space, not a margin: it hangs at a line end, so a wrapped cell starts flush. */}
              {" "}
              {at(i)}
            </React.Fragment>
          ))}
        </div>
        {outside.map(at)}
      </>
    );
  }

  return (
    <div
      role="row"
      data-slot="table-row"
      data-flip="row"
      className={cn(
        SUBGRID,
        rowClasses,
        "relative px-(--table-pad) leading-5.5",
        head
          ? cn("items-center", dense ? "min-h-(--control-h-sm)" : "min-h-(--control-h)")
          : cn("items-start", !dense && "py-2"),
        className,
      )}
      {...(props as DivProps)}
    >
      {content}
    </div>
  );
}

/**
 * A cell of a fitting table, drawn where the layout puts its column. The title is always in
 * its own column (and names the row, as its `rowheader`); a column that steps out stays in the
 * accessibility tree, visually hidden, and an inline one flows after the title's last word.
 */
function FitCell({
  place,
  fit,
  kind,
  className,
  style,
  children,
  colSpan,
  scope: _scope,
  ...props
}: DivProps & {
  place: TableCellSlot;
  fit: TableFitValue;
  kind: "head" | "cell";
  colSpan?: number;
  scope?: string;
}) {
  const { index, rowKey, head, mapped } = place;
  const role = head
    ? "columnheader"
    : kind === "head" || (mapped && index === fit.title)
      ? "rowheader"
      : "cell";

  if (!mapped)
    return (
      <div
        role={role}
        className={cn("min-w-0", (colSpan ?? 1) > 1 && "col-span-full", className)}
        style={style}
        {...props}
      >
        {children}
      </div>
    );

  if (index === fit.title) {
    const inline = fit.inlined && !head;
    return (
      <div
        role={role}
        data-title=""
        className={cn(
          inline ? "inline" : cn("flex min-w-0", head ? "items-center" : "min-h-7.5 items-start py-1"),
          className,
        )}
        style={inline ? style : { gridColumn: fit.track.get(index), ...style }}
        {...props}
      >
        <span
          data-slot="table-title-text"
          className={fit.final && !head ? "min-w-0 text-pretty break-words" : "whitespace-nowrap"}
        >
          {children}
        </span>
      </div>
    );
  }

  const end = fit.align[index] === "end";
  const column = fit.modes[index] ?? "column";
  // A header has its own column or is only read; it never rides in the title.
  const mode: FitMode = head && column !== "column" ? "hidden" : column;
  return (
    <div
      role={role}
      data-column={index}
      data-head={head ? "" : undefined}
      data-mode={mode}
      data-align={end ? "end" : "start"}
      data-flip="piece"
      data-flip-key={`${rowKey}:${index}`}
      className={cn(
        "min-w-0 items-center whitespace-nowrap",
        mode === "column" && "flex",
        // Cells are 30px, the height of a dense control, so a button sits in a 46px row; text
        // centres on the title's first line.
        mode === "column" && !head && "min-h-7.5",
        mode === "column" && (end ? "justify-self-end text-right" : "justify-self-start"),
        // Inline cells flow right after the title's last word, and wrap like words.
        mode === "inline" && "inline-flex min-h-5.5 align-top",
        mode === "hidden" && "sr-only",
        className,
      )}
      style={mode === "column" ? { gridColumn: fit.track.get(index), ...style } : style}
      {...props}
    >
      {children}
    </div>
  );
}

export interface TableHeadProps extends Omit<React.ComponentProps<"th">, "align" | "width"> {
  /** Right-align a numeric column's header (and, with `fit`, its cells). */
  numeric?: boolean;
  /** `end` right-aligns the column without setting it in mono — people, dates. Defaults by `numeric`. */
  align?: "start" | "end";
  /** The column's width: a CSS length, or with `fit` a grid track (`auto`, `6rem`, `minmax(…)`). */
  width?: string;
  /**
   * With `fit`: when the titles no longer fit on one line, columns step out one at a time,
   * lowest `priority` first (on a tie, the later column first). A column with no `priority`,
   * `inline` or `minWidth` never steps out.
   */
  priority?: number;
  /** With `fit`: when the column steps out, show its cells inline after the title instead of hiding them. */
  inline?: boolean;
  /** With `fit`: below this container width (rem) the column steps out even if it would still fit. */
  minWidth?: number;
  /** With `fit`: the column that names each row and never steps out. Defaults to the first column. */
  titleColumn?: boolean;
}

/** A column header, set as an eyebrow: 12px mono, uppercase, 0.18em tracking, muted. */
export function TableHead({
  numeric = false,
  align,
  width,
  priority,
  inline,
  minWidth,
  titleColumn,
  className,
  style,
  ...props
}: TableHeadProps) {
  const dense = React.useContext(TableDenseContext);
  const fit = React.useContext(TableFitContext);
  const slot = React.useContext(TableCellSlotContext);
  const end = (align ?? (numeric ? "end" : "start")) === "end";
  const register = fit?.register;
  const index = slot?.head ? slot.index : -1;

  React.useLayoutEffect(() => {
    if (!register || index < 0) return;
    return register(index, { priority, inline, minWidth, width, title: titleColumn, end });
  }, [register, index, priority, inline, minWidth, width, titleColumn, end]);

  if (fit && slot)
    return (
      <FitCell
        place={slot}
        fit={fit}
        kind="head"
        data-slot="table-head"
        className={cn(slot.head ? "eyebrow font-normal" : "text-left font-normal text-foreground", className)}
        style={style}
        {...(props as DivProps & { colSpan?: number; scope?: string })}
      />
    );

  return (
    <th
      data-slot="table-head"
      scope="col"
      className={cn(
        "px-4 text-left align-middle eyebrow font-normal whitespace-nowrap first:pl-6 last:pr-6",
        dense ? "h-(--control-h-sm)" : "h-(--control-h)",
        end && "text-right",
        className,
      )}
      style={width ? { width, ...style } : style}
      {...props}
    />
  );
}

export interface TableCellProps extends React.ComponentProps<"td"> {
  /** Numbers, amounts, dates and IDs: Licht Mono, tabular figures, right-aligned. */
  numeric?: boolean;
}

export function TableCell({ numeric = false, className, ...props }: TableCellProps) {
  const dense = React.useContext(TableDenseContext);
  const fit = React.useContext(TableFitContext);
  const slot = React.useContext(TableCellSlotContext);

  if (fit && slot)
    return (
      <FitCell
        place={slot}
        fit={fit}
        kind="cell"
        data-slot="table-cell"
        className={cn(
          // The title names the row.
          slot.mapped && !slot.head && slot.index === fit.title ? "text-foreground" : "text-foreground-soft",
          dense && "text-[13px]",
          numeric && "font-mono text-[13px] tabular-nums",
          className,
        )}
        {...(props as DivProps & { colSpan?: number })}
      />
    );

  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-4 align-middle text-foreground-soft first:pl-6 last:pr-6",
        dense ? "h-(--control-h-sm) py-1 text-[13px]" : "h-(--control-h-lg) py-2",
        numeric && "text-right font-mono text-[13px] whitespace-nowrap tabular-nums",
        className,
      )}
      {...props}
    />
  );
}

/* ── Grouped rows ──────────────────────────────────────────────────────────── */

interface TableGroupContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  collapsible: boolean;
}

const TableGroupContext = React.createContext<TableGroupContextValue | null>(null);

export interface TableGroupProps extends React.ComponentProps<"tbody"> {
  /** Controlled open state. */
  open?: boolean;
  /** Open at first. Defaults to `true`. */
  defaultOpen?: boolean;
  /** Called when the group header's trigger opens or closes the group. */
  onOpenChange?: (open: boolean) => void;
  /** Let the header open and close the group. Defaults to `true`. */
  collapsible?: boolean;
}

/**
 * TableGroup — one group of rows: a `<tbody>` that starts with a `TableGroupHeader` and holds
 * ordinary `TableRow`s. Use several in place of a single `TableBody`.
 *
 * Groups answer "which rows belong together" (by status, by owner, by month). The rule people
 * break: grouping by something every row shares, or into groups of one. If most groups would
 * hold a single row, sort the column instead.
 */
export function TableGroup({
  open: openProp,
  defaultOpen = true,
  onOpenChange,
  collapsible = true,
  className,
  ...props
}: TableGroupProps) {
  const fit = React.useContext(TableFitContext);
  const [uncontrolled, setUncontrolled] = React.useState(defaultOpen);
  const open = openProp ?? uncontrolled;
  const onOpenChangeRef = React.useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  const setOpen = React.useCallback(
    (next: boolean) => {
      if (openProp === undefined) setUncontrolled(next);
      onOpenChangeRef.current?.(next);
    },
    [openProp],
  );
  const value = React.useMemo(() => ({ open, setOpen, collapsible }), [open, setOpen, collapsible]);
  const state = { "data-open": open ? "" : undefined, "data-closed": open ? undefined : "" };
  return (
    <TableGroupContext.Provider value={value}>
      {fit ? (
        <div
          role="rowgroup"
          data-slot="table-group"
          {...state}
          className={cn(SUBGRID, className)}
          {...(props as DivProps)}
        />
      ) : (
        <tbody data-slot="table-group" {...state} className={className} {...props} />
      )}
    </TableGroupContext.Provider>
  );
}

export interface TableGroupHeaderProps extends Omit<React.ComponentProps<"tr">, "children"> {
  /** The group's label: the shared value ("Running"), or a `Tag` for a status. */
  children: React.ReactNode;
  /** A 16px lucide icon before the label. */
  icon?: React.ReactNode;
  /** How many rows the group holds, shown in mono after the label. */
  count?: React.ReactNode;
  /** A short mono summary at the end of the row, e.g. a total. */
  summary?: React.ReactNode;
  /** Columns the header spans. Pass the table's column count. Defaults to 100. */
  colSpan?: number;
  /** Stick to the top of the table's scroll area while the group's rows scroll under it. */
  sticky?: boolean;
}

/**
 * The first row of a `TableGroup`: a full-width band with the label, a mono count and an
 * optional summary. When the group is collapsible the label is a button with a chevron that
 * opens and closes the group (`aria-expanded`).
 *
 * `sticky` pins the header to the top of the table card while its rows scroll — give the
 * card a height (`containerProps={{ className: "max-h-96" }}`) so the card itself scrolls.
 */
export function TableGroupHeader({
  icon,
  count,
  summary,
  colSpan = 100,
  sticky = false,
  className,
  children,
  ...props
}: TableGroupHeaderProps) {
  const dense = React.useContext(TableDenseContext);
  const fit = React.useContext(TableFitContext);
  const group = React.useContext(TableGroupContext);
  const collapsible = group?.collapsible ?? false;
  const open = group?.open ?? true;

  const label = (
    <>
      {icon != null && (
        <span aria-hidden className="flex shrink-0 text-foreground-soft [&_svg]:size-4 [&_svg]:shrink-0">
          {icon}
        </span>
      )}
      {/* A pixel of padding inside the overflow, so a Tag's 1px edge is not clipped. */}
      <span
        data-slot="table-group-label"
        className="-m-px min-w-0 truncate p-px text-[13px] font-medium text-foreground"
      >
        {children}
      </span>
      {count != null && (
        <span
          data-slot="table-group-count"
          className="font-mono text-[12px] font-normal text-muted-foreground tabular-nums"
        >
          {count}
        </span>
      )}
    </>
  );

  const content = (
    <div className="flex min-w-0 items-center gap-4">
      {collapsible ? (
        <button
          type="button"
          data-slot="table-group-trigger"
          aria-expanded={open}
          onClick={() => group?.setOpen(!open)}
          className={cn(
            "group/table-group -ml-2 inline-flex min-w-0 cursor-pointer items-center gap-2 rounded-item material-row px-2 focus-ring select-none",
            dense ? "min-h-(--control-inset-sm)" : "min-h-(--control-h-sm)",
          )}
        >
          <ChevronRightIcon
            aria-hidden
            className="size-3.5 shrink-0 text-muted-foreground transition-[rotate] duration-(--duration) ease-(--ease) group-aria-expanded/table-group:rotate-90"
          />
          {label}
        </button>
      ) : (
        <span className="inline-flex min-w-0 items-center gap-2">{label}</span>
      )}
      {summary != null && (
        <span
          data-slot="table-group-summary"
          className="ml-auto shrink-0 font-mono text-[12px] whitespace-nowrap text-muted-foreground tabular-nums"
        >
          {summary}
        </span>
      )}
    </div>
  );

  const band = cn(
    "relative bg-control-surface text-left align-middle font-normal",
    "after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border/60",
    dense ? "h-(--control-h-sm)" : "h-(--control-h)",
  );

  if (fit)
    return (
      <div
        role="row"
        data-slot="table-group-header"
        data-flip="band"
        data-sticky={sticky ? "" : undefined}
        className={cn(SUBGRID, sticky && "sticky top-0 z-2", className)}
        {...(props as DivProps)}
      >
        <div
          role="rowheader"
          aria-colspan={fit.count || undefined}
          data-slot="table-group-band"
          className={cn(band, "col-span-full flex items-center px-(--table-pad)")}
        >
          {content}
        </div>
      </div>
    );

  return (
    <tr data-slot="table-group-header" data-sticky={sticky ? "" : undefined} className={className} {...props}>
      <th
        scope="rowgroup"
        colSpan={colSpan}
        data-slot="table-group-band"
        // Sticks 1px above the top: a sticky cell in a collapsed-border table leaves its top pixel
        // uncovered, and the rows scrolling under showed through it; the scroller clips the extra.
        className={cn(band, "px-6", sticky && "sticky -top-px z-1")}
      >
        {content}
      </th>
    </tr>
  );
}

/** A short description of the table, under it. Prefer it over a heading inside the card. */
export function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  const fit = React.useContext(TableFitContext);
  const classes = "border-t border-border/60 py-4 text-left text-[13px] text-muted-foreground";
  if (fit)
    return (
      <div
        role="caption"
        data-slot="table-caption"
        className={cn(classes, "order-last col-span-full px-(--table-pad)", className)}
        {...(props as DivProps)}
      />
    );
  return <caption data-slot="table-caption" className={cn(classes, "px-6", className)} {...props} />;
}
