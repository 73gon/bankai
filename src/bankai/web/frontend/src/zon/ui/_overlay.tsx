/*
 * Private building blocks shared by the overlay components (dialog, alert-dialog, drawer,
 * popover, tooltip, menu, context-menu, preview-card, command). The leading underscore keeps
 * this file out of the generated index: nothing here is public API.
 */
import { Menu as BaseMenu } from "@base-ui/react/menu";
import * as React from "react";
import { cn, sx } from "@/zon/lib/cn";
import { roomBounds, TriggerRoomContext } from "@/zon/lib/room";

/**
 * The card material at object geometry, lifted to menu level: `--field-edge` border, the 160°
 * gradient, the inset top highlight and the float shadow popups share. Dialogs, drawers and the
 * command palette wear this.
 */
export const overlayCard =
  "border border-field-edge bg-[linear-gradient(160deg,var(--control-surface),var(--field-surface)_55%)] text-foreground shadow-overlay";

/** The dim behind modal objects. No blur: the page stays legible, it just recedes. */
export const overlayBackdrop =
  "fixed inset-0 z-50 min-h-dvh bg-black/40 transition-opacity duration-(--duration) ease-brand data-ending-style:opacity-0 data-starting-style:opacity-0 supports-[-webkit-touch-callout:none]:absolute dark:bg-black/60";

/** Dialog-family typography, shared so a dialog, an alert dialog and a drawer read alike. */
export const overlayTitle =
  "font-heading text-[26px] leading-[1.1] font-light tracking-[-0.02em] text-balance text-foreground";
export const overlayDescription = "text-[15px] leading-relaxed text-foreground-soft";
export const overlayHeader = "flex flex-col gap-2";
export const overlayFooter = "flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end";

/** The circular close button in the corner of a dialog or drawer (ghost, 30px). */
export const overlayCloseButton =
  "absolute top-4 right-4 z-10 inline-flex size-(--control-h-sm) cursor-pointer items-center justify-center rounded-full material-row text-muted-foreground focus-ring sm:top-6 sm:right-6 [&_svg]:size-4";

/** Floating popup surface for popovers and preview cards. */
export const overlayPopup = "rounded-button material-menu popup-motion font-body outline-none";

/**
 * Arrow for anchored popups: a rotated square clipped to a 12×6 notch. Its fill covers the
 * popup's border under the notch so the edge reads as one continuous line.
 */
export const overlayArrow = cn(
  "relative block h-1.5 w-3 overflow-clip",
  "data-[side=bottom]:-top-1.5 data-[side=top]:-bottom-1.5 data-[side=top]:rotate-180",
  "data-[side=left]:-right-[9px] data-[side=left]:rotate-90 data-[side=right]:-left-[9px] data-[side=right]:-rotate-90",
  "before:absolute before:bottom-0 before:left-1/2 before:size-[calc(6px*sqrt(2))] before:[transform:translate(-50%,50%)_rotate(45deg)] before:border before:border-field-edge before:bg-control-menu before:content-['']",
);

/* ── Menu frame (shared by Menu, its submenus and ContextMenu) ─────────────── */

export const menuPopup =
  "min-w-48 max-w-(--available-width) max-h-(--available-height) overflow-y-auto overscroll-contain p-1 rounded-button material-menu popup-motion font-body outline-none";

export interface MenuFrameProps extends BaseMenu.Popup.Props {
  slot: string;
  side?: BaseMenu.Positioner.Props["side"];
  align?: BaseMenu.Positioner.Props["align"];
  sideOffset?: BaseMenu.Positioner.Props["sideOffset"];
  alignOffset?: BaseMenu.Positioner.Props["alignOffset"];
  positioner?: Omit<BaseMenu.Positioner.Props, "children">;
  portal?: Omit<BaseMenu.Portal.Props, "children">;
}

/** Portal + Positioner + Popup. Undefined positioning props fall through to Base UI defaults. */
export function MenuFrame({
  slot,
  side,
  align,
  sideOffset,
  alignOffset,
  positioner,
  portal,
  className,
  ...props
}: MenuFrameProps) {
  // Across, keep inside the room of the menu's trigger (a narrow panel) — submenus too.
  const trigger = React.useContext(TriggerRoomContext)?.trigger ?? null;
  return (
    <BaseMenu.Portal {...portal}>
      <BaseMenu.Positioner
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        collisionBoundary={roomBounds(trigger)}
        {...positioner}
        className="z-50 outline-none"
      >
        <BaseMenu.Popup data-slot={slot} className={sx(menuPopup, className)} {...props} />
      </BaseMenu.Positioner>
    </BaseMenu.Portal>
  );
}
