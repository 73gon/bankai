import { Drawer as BaseDrawer } from "@base-ui/react/drawer";
import { XIcon } from "lucide-react";
import * as React from "react";
import { cn, sx, triggerSlot } from "@/zon/lib/cn";
import {
  overlayCard,
  overlayCloseButton,
  overlayDescription,
  overlayFooter,
  overlayHeader,
  overlayTitle,
} from "@/zon/ui/_overlay";

export type DrawerSide = "bottom" | "right" | "left";

const swipeDirection = { bottom: "down", right: "right", left: "left" } as const;

const DrawerSideContext = React.createContext<DrawerSide>("bottom");

export interface DrawerProps extends BaseDrawer.Root.Props {
  /**
   * Where the drawer lives. `bottom` is a sheet (mobile; grab handle, top corners at 20px);
   * `right` / `left` are side panels with a square edge against the viewport.
   * Sets the swipe-to-dismiss direction unless `swipeDirection` is passed.
   */
  side?: DrawerSide;
}

/**
 * Drawer — a panel that slides in from an edge and can be swiped away. Bottom sheets for mobile
 * tasks, side panels for filters, settings or detail views next to the page.
 *
 * Supports Base UI snap points (`snapPoints`, `snapPoint`, `onSnapPointChange`) on bottom sheets.
 * The rule people break: reaching for a drawer when nothing needs a gesture. A centred task with
 * no swipe is a `Dialog`.
 */
export function Drawer({ side = "bottom", ...props }: DrawerProps) {
  return (
    <DrawerSideContext.Provider value={side}>
      <BaseDrawer.Root swipeDirection={swipeDirection[side]} {...props} />
    </DrawerSideContext.Provider>
  );
}

/** Opens the drawer. Compose with a button: `render={<Button variant="secondary" />}`. */
export function DrawerTrigger(props: BaseDrawer.Trigger.Props) {
  return <BaseDrawer.Trigger {...triggerSlot("drawer-trigger", props.render)} {...props} />;
}

const viewportBySide = {
  bottom: "items-end justify-center",
  right: "items-stretch justify-end",
  left: "items-stretch justify-start",
} as const;

const popupBySide = {
  // A bottom sheet never covers the top 24px (plus the safe area), so the backdrop stays reachable.
  bottom:
    "w-full max-w-2xl max-h-[calc(100dvh-1.5rem-env(safe-area-inset-top,0px))] rounded-t-card border-b-0",
  right: "h-full w-[min(26rem,calc(100vw-3rem))] rounded-l-card border-y-0 border-r-0",
  left: "h-full w-[min(26rem,calc(100vw-3rem))] rounded-r-card border-y-0 border-l-0",
} as const;

export type DrawerSize = "default" | "full";

export interface DrawerContentProps extends BaseDrawer.Popup.Props {
  /**
   * Bottom sheets only. `default` is a centred sheet up to 672px wide; `full` spans the whole
   * width of the viewport, edge to edge. Both grow with their content up to the viewport minus a
   * 24px top gap.
   */
  size?: DrawerSize;
  /**
   * Bottom sheets only: always take the full available height and scroll the body. Needed for
   * `snapPoints`, which snap within a fixed height.
   */
  fullHeight?: boolean;
  /** Render the circular close button in the top-right corner. */
  showCloseButton?: boolean;
  /** Accessible name of the close button. */
  closeLabel?: string;
  /** Show the grab handle. Defaults to `true` on bottom sheets. */
  showHandle?: boolean;
  /** Props for the portal (e.g. `container`, `keepMounted`). */
  portal?: Omit<BaseDrawer.Portal.Props, "children">;
}

/**
 * Portal + Backdrop + Viewport + Popup + body. The popup follows the pointer 1:1 while swiping
 * (Base UI writes the drag into an inline transform and `--drawer-swipe-movement-*`), rests on
 * `--drawer-snap-point-offset`, and the backdrop fades with `--drawer-swipe-progress`. With touch
 * the whole sheet drags; with a mouse, everything except controls and a scrolling body does, so
 * text there stays selectable. Motion lives in `styles/parts/overlays.css`.
 */
export function DrawerContent({
  size = "default",
  fullHeight = false,
  showCloseButton = true,
  closeLabel = "Close",
  showHandle,
  portal,
  className,
  children,
  ...props
}: DrawerContentProps) {
  const side = React.useContext(DrawerSideContext);
  const handle = showHandle ?? side === "bottom";
  return (
    <BaseDrawer.Portal {...portal}>
      <BaseDrawer.Backdrop
        data-slot="drawer-backdrop"
        className="fixed inset-0 z-50 min-h-dvh bg-black/40 supports-[-webkit-touch-callout:none]:absolute dark:bg-black/60"
      />
      <BaseDrawer.Viewport
        data-slot="drawer-viewport"
        className={cn("fixed inset-0 z-50 flex", viewportBySide[side])}
      >
        <BaseDrawer.Popup
          data-slot="drawer-content"
          data-side={side}
          data-size={side === "bottom" ? size : undefined}
          data-full-height={side === "bottom" && fullHeight ? "" : undefined}
          className={sx(
            [
              "relative flex flex-col font-body outline-none",
              overlayCard,
              popupBySide[side],
              side === "bottom" && size === "full" && "max-w-none border-x-0",
              side === "bottom" && fullHeight && "h-[calc(100dvh-1.5rem-env(safe-area-inset-top,0px))]",
            ],
            className,
          )}
          {...props}
        >
          {handle && (
            // The grab band: a 28px strip that always starts a drag, also over a scrolling body.
            <div
              data-slot="drawer-handle-area"
              className="flex h-7 shrink-0 cursor-grab justify-center pt-3 in-data-swiping:cursor-grabbing"
            >
              <div data-slot="drawer-handle" aria-hidden className="h-1 w-10 rounded-full bg-field-edge" />
            </div>
          )}
          <div
            data-slot="drawer-body"
            className={cn(
              "flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto overscroll-contain p-6 sm:p-8",
              side === "bottom" && "pb-[calc(1.5rem+env(safe-area-inset-bottom,0px))]",
              handle && "pt-2 sm:pt-3",
            )}
          >
            {children}
          </div>
          {showCloseButton && (
            <BaseDrawer.Close
              data-slot="drawer-close-button"
              aria-label={closeLabel}
              className={overlayCloseButton}
            >
              <XIcon aria-hidden />
            </BaseDrawer.Close>
          )}
        </BaseDrawer.Popup>
      </BaseDrawer.Viewport>
    </BaseDrawer.Portal>
  );
}

/** Title + description block. Leaves room on the right for the close button. */
export function DrawerHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="drawer-header" className={cn(overlayHeader, "pr-10", className)} {...props} />;
}

export function DrawerTitle({ className, ...props }: BaseDrawer.Title.Props) {
  return <BaseDrawer.Title data-slot="drawer-title" className={sx(overlayTitle, className)} {...props} />;
}

export function DrawerDescription({ className, ...props }: BaseDrawer.Description.Props) {
  return (
    <BaseDrawer.Description
      data-slot="drawer-description"
      className={sx(overlayDescription, className)}
      {...props}
    />
  );
}

/** Actions. Pinned to the bottom of a side panel; stacked full-width on mobile. */
export function DrawerFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="drawer-footer" className={cn(overlayFooter, "mt-auto", className)} {...props} />;
}

/** Closes the drawer. Compose with a button: `render={<Button variant="secondary" />}`. */
export function DrawerClose(props: BaseDrawer.Close.Props) {
  return <BaseDrawer.Close data-slot="drawer-close" {...props} />;
}
