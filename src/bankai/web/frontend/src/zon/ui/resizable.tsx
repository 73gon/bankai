import * as React from "react";
import {
  Group,
  Panel,
  Separator,
  type GroupProps,
  type PanelProps,
  type SeparatorProps,
} from "react-resizable-panels";
import { cn } from "@/zon/lib/cn";

const OrientationContext = React.createContext<"horizontal" | "vertical">("horizontal");

export interface ResizablePanelGroupProps extends GroupProps {
  /** `horizontal` lays panels side by side (a vertical handle); `vertical` stacks them. */
  orientation?: "horizontal" | "vertical";
}

/**
 * ResizablePanelGroup — split panes the user can resize: an editor beside its preview, a list
 * beside its detail, a console under a canvas.
 *
 * Panels are divided by a 1px hairline that turns `--control-focus` on hover, drag and focus,
 * with a hit area of at least 10px (20px on touch). Every handle is a keyboard separator: Tab to
 * it, then arrow keys resize, Home/End jump to the limits and Enter collapses a collapsible panel.
 *
 * The rule people break: boxing every pane. The group is one region — put the material on the
 * group (`surface-container`, or `surface-card` when it is an object) and let hairlines divide it.
 * The group fills its parent, so give the parent (or the group) a height.
 */
export function ResizablePanelGroup({
  orientation = "horizontal",
  className,
  ...props
}: ResizablePanelGroupProps) {
  return (
    <OrientationContext.Provider value={orientation}>
      <Group
        data-slot="resizable-panel-group"
        data-orientation={orientation}
        orientation={orientation}
        className={cn("h-full w-full", className)}
        {...props}
      />
    </OrientationContext.Provider>
  );
}

export interface ResizablePanelProps extends PanelProps {}

/**
 * One pane. Sizes take numbers (pixels) or strings: `"30%"`, `"30"` (percent), `"240px"`, `"16rem"`.
 * Give each panel an `id` when the layout is persisted with `defaultLayout`.
 */
export function ResizablePanel({ className, ...props }: ResizablePanelProps) {
  return <Panel data-slot="resizable-panel" className={cn("min-h-0 min-w-0", className)} {...props} />;
}

export interface ResizableHandleProps extends SeparatorProps {
  /** Show a small raised grip with mono dots in the middle of the hairline. */
  withHandle?: boolean;
  /** Accessible name of the separator. Defaults to "Resize". */
  "aria-label"?: string;
}

/**
 * The divider between two panels: a 1px hairline in `--border` that becomes `--control-focus`
 * while hovered, dragged or focused. `withHandle` adds a tiny raised pill with mono dots — use it
 * when the split is not obviously draggable (a narrow pane, a touch layout).
 */
export function ResizableHandle({ withHandle = false, className, children, ...props }: ResizableHandleProps) {
  const orientation = React.useContext(OrientationContext);
  return (
    <Separator
      data-slot="resizable-handle"
      data-orientation={orientation}
      aria-label="Resize"
      className={cn(
        "group/handle relative z-10 flex shrink-0 items-center justify-center bg-border outline-none",
        "transition-[background-color] duration-(--duration) ease-brand",
        orientation === "horizontal" ? "w-px self-stretch" : "h-px w-full",
        "data-[separator=active]:bg-control-focus data-[separator=focus]:bg-control-focus data-[separator=hover]:bg-control-focus",
        // The visible line is 1px; the focus outline is drawn on a 3px band so it stays legible.
        "focus-visible:outline-none focus-visible:after:absolute focus-visible:after:bg-control-focus focus-visible:after:content-['']",
        orientation === "horizontal"
          ? "focus-visible:after:inset-y-0 focus-visible:after:left-1/2 focus-visible:after:w-[2px] focus-visible:after:-translate-x-1/2"
          : "focus-visible:after:inset-x-0 focus-visible:after:top-1/2 focus-visible:after:h-[2px] focus-visible:after:-translate-y-1/2",
        "data-[separator=disabled]:opacity-50",
        className,
      )}
      {...props}
    >
      {withHandle ? (
        <span
          data-slot="resizable-grip"
          aria-hidden
          className={cn(
            "pointer-events-none z-10 flex shrink-0 items-center justify-center gap-[3px] rounded-full bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-key",
            "transition-[background-color] duration-(--duration) ease-brand group-data-[separator=active]/handle:bg-control-hover group-data-[separator=hover]/handle:bg-control-hover",
            orientation === "horizontal" ? "h-6 w-3 flex-col" : "h-3 w-6 flex-row",
          )}
        >
          <span className="size-[3px] rounded-full bg-muted-foreground" />
          <span className="size-[3px] rounded-full bg-muted-foreground" />
          <span className="size-[3px] rounded-full bg-muted-foreground" />
        </span>
      ) : null}
      {children}
    </Separator>
  );
}
