import { ScrollArea as BaseScrollArea } from "@base-ui/react/scroll-area";
import { cn, sx } from "@/zon/lib/cn";

export interface ScrollAreaProps extends BaseScrollArea.Root.Props {
  /** Which scrollbars to render. Defaults to `vertical`. */
  orientation?: "vertical" | "horizontal" | "both";
  /** Fade the content out at edges that have more to scroll. Defaults to `true`. */
  fade?: boolean;
  /**
   * Draw the scrollbar. Defaults to `true`. Sidebars pass `false`: they scroll without a bar,
   * and the edge fade says there is more.
   */
  scrollbar?: boolean;
  /** Class for the scrolling viewport (padding usually goes on the content instead). */
  viewportClassName?: string;
  /** Class for the content wrapper inside the viewport. */
  contentClassName?: string;
}

function ScrollAreaScrollbar({ orientation }: { orientation: "vertical" | "horizontal" }) {
  return (
    <BaseScrollArea.Scrollbar
      data-slot="scroll-area-scrollbar"
      orientation={orientation}
      className={cn(
        "m-1 flex touch-none rounded-full opacity-0 transition-opacity duration-(--duration) ease-(--ease) select-none",
        "pointer-events-none data-hovering:pointer-events-auto data-hovering:opacity-100 data-scrolling:pointer-events-auto data-scrolling:opacity-100",
        "data-[orientation=horizontal]:h-1.5 data-[orientation=vertical]:w-1.5",
      )}
    >
      <BaseScrollArea.Thumb
        data-slot="scroll-area-thumb"
        className="rounded-full bg-field-edge transition-colors duration-(--duration) ease-(--ease) hover:bg-control-focus data-[orientation=horizontal]:h-full data-[orientation=vertical]:w-full"
      />
    </BaseScrollArea.Scrollbar>
  );
}

/**
 * ScrollArea — a scrolling region with a thin, quiet scrollbar.
 *
 * The thumb is a 6px round bar in `--field-edge` that appears while the pointer is over the
 * area or while scrolling. Edges with more content fade out using Base UI's overflow CSS
 * variables, so the fade grows as you scroll away from an edge. The rule people break:
 * nesting scroll areas or putting the main page in one. Let the page scroll natively; use
 * this for bounded panels (a sidebar, a code listing, a long select). Sidebars never show a
 * scrollbar: pass `scrollbar={false}`.
 */
export function ScrollArea({
  orientation = "vertical",
  fade = true,
  scrollbar = true,
  viewportClassName,
  contentClassName,
  className,
  children,
  ...props
}: ScrollAreaProps) {
  const fadeAxis = !fade
    ? undefined
    : orientation === "both"
      ? "both"
      : orientation === "horizontal"
        ? "x"
        : "y";
  return (
    <BaseScrollArea.Root
      data-slot="scroll-area"
      className={sx("relative min-h-0 overflow-hidden", className)}
      {...props}
    >
      <BaseScrollArea.Viewport
        data-slot="scroll-area-viewport"
        data-fade={fadeAxis}
        className={cn("size-full max-h-[inherit] overscroll-contain focus-ring", viewportClassName)}
      >
        <BaseScrollArea.Content
          data-slot="scroll-area-content"
          className={cn(orientation !== "vertical" && "min-w-fit", contentClassName)}
        >
          {children}
        </BaseScrollArea.Content>
      </BaseScrollArea.Viewport>
      {scrollbar && orientation !== "horizontal" && <ScrollAreaScrollbar orientation="vertical" />}
      {scrollbar && orientation !== "vertical" && <ScrollAreaScrollbar orientation="horizontal" />}
      {scrollbar && orientation === "both" && <BaseScrollArea.Corner data-slot="scroll-area-corner" />}
    </BaseScrollArea.Root>
  );
}
