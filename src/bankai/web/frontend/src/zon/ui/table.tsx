import { ChevronRightIcon } from "lucide-react";
import * as React from "react";
import { cn } from "@/zon/lib/cn";

const TableDenseContext = React.createContext(false);

export interface TableProps extends React.ComponentProps<"table"> {
  /** 30px rows for data-heavy screens (admin, dashboards). Default rows are 46px. */
  dense?: boolean;
  /**
   * `card` (default): the table as an object, a 20px card. `plain`: the same table without the
   * card — no edge, corners, shadow or ground — across the full width of its column.
   */
  variant?: "card" | "plain";
  /** Props for the container that wraps and scrolls the table (the card, unless `plain`). */
  containerProps?: React.ComponentProps<"div">;
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
 */
export function Table({ dense = false, variant = "card", containerProps, className, ...props }: TableProps) {
  return (
    <TableDenseContext.Provider value={dense}>
      <div
        data-slot="table-container"
        data-variant={variant}
        {...containerProps}
        className={cn(
          "relative w-full overflow-x-auto",
          variant === "card" && "surface-card [--card-padding:0]",
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

export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b [&_tr]:border-border/60 [&_tr]:hover:bg-transparent", className)}
      {...props}
    />
  );
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-b-0", className)} {...props} />
  );
}

export function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t border-border/60 font-medium text-foreground [&_tr]:hover:bg-transparent",
        className,
      )}
      {...props}
    />
  );
}

/** A row. Mark a selected row with `aria-selected` / `data-selected`. */
export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-border/60 transition-colors duration-(--duration) ease-(--ease)",
        "hover:bg-control-hover/50 aria-selected:bg-control-hover data-selected:bg-control-hover",
        className,
      )}
      {...props}
    />
  );
}

export interface TableHeadProps extends React.ComponentProps<"th"> {
  /** Right-align a numeric column's header. */
  numeric?: boolean;
}

/** A column header, set as an eyebrow: 12px mono, uppercase, 0.18em tracking, muted. */
export function TableHead({ numeric = false, className, ...props }: TableHeadProps) {
  const dense = React.useContext(TableDenseContext);
  return (
    <th
      data-slot="table-head"
      scope="col"
      className={cn(
        "px-4 text-left align-middle eyebrow font-normal whitespace-nowrap first:pl-6 last:pr-6",
        dense ? "h-(--control-h-sm)" : "h-(--control-h)",
        numeric && "text-right",
        className,
      )}
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
 * Groups answer "which rows belong together" (by status, by client, by month). The rule people
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
  return (
    <TableGroupContext.Provider value={value}>
      <tbody
        data-slot="table-group"
        data-open={open ? "" : undefined}
        data-closed={open ? undefined : ""}
        className={className}
        {...props}
      />
    </TableGroupContext.Provider>
  );
}

export interface TableGroupHeaderProps extends Omit<React.ComponentProps<"tr">, "children"> {
  /** The group's label: the shared value ("Running"), or a `Tag` for a status. */
  children: React.ReactNode;
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
  count,
  summary,
  colSpan = 100,
  sticky = false,
  className,
  children,
  ...props
}: TableGroupHeaderProps) {
  const dense = React.useContext(TableDenseContext);
  const group = React.useContext(TableGroupContext);
  const collapsible = group?.collapsible ?? false;
  const open = group?.open ?? true;

  const label = (
    <>
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

  return (
    <tr data-slot="table-group-header" data-sticky={sticky ? "" : undefined} className={className} {...props}>
      <th
        scope="rowgroup"
        colSpan={colSpan}
        className={cn(
          "relative bg-control-surface px-6 text-left align-middle font-normal",
          "after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border/60",
          dense ? "h-(--control-h-sm)" : "h-(--control-h)",
          sticky && "sticky top-0 z-1",
        )}
      >
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
      </th>
    </tr>
  );
}

/** A short description of the table, under it. Prefer it over a heading inside the card. */
export function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn(
        "border-t border-border/60 px-6 py-4 text-left text-[13px] text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
