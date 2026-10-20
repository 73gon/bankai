import { Menu as BaseMenu } from "@base-ui/react/menu";
import type * as React from "react";
import { sx } from "@/zon/lib/cn";

/*
 * The parts every zon overlay shares. The registry lists this file as a dependency of the
 * dialog, drawer, popover, tooltip and menu items but does not publish it; it is rebuilt here
 * from the classes the components render on zon.sh/ui.
 */

/** The dimmed page behind a modal. Fades with the popup. */
export const overlayBackdrop =
  "fixed inset-0 z-50 min-h-dvh bg-black/40 transition-opacity duration-(--duration) ease-brand data-ending-style:opacity-0 data-starting-style:opacity-0 supports-[-webkit-touch-callout:none]:absolute dark:bg-black/60";

/** A modal's surface: the card material, an edge and the overlay shadow. */
export const overlayCard =
  "border border-field-edge bg-[linear-gradient(160deg,var(--control-surface),var(--field-surface)_55%)] text-foreground shadow-overlay";

/** The small anchored surfaces: popovers and menus, in the menu material. */
export const overlayPopup = "rounded-button material-menu popup-motion font-body outline-none";

export const overlayHeader = "flex flex-col gap-2";

export const overlayTitle =
  "font-heading text-[26px] leading-[1.1] font-light tracking-[-0.02em] text-balance text-foreground";

export const overlayDescription = "text-[15px] leading-relaxed text-foreground-soft";

export const overlayFooter = "flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end";

export const overlayCloseButton =
  "absolute top-4 right-4 z-10 inline-flex size-(--control-h-sm) cursor-pointer items-center justify-center rounded-full material-row text-muted-foreground focus-ring sm:top-6 sm:right-6 [&_svg]:size-4";

/** The pointer of a popover or tooltip, turned by the side it opens on. */
export const overlayArrow =
  "size-2.5 rotate-45 border border-field-edge bg-(--control-surface) data-[side=bottom]:-top-[5px] data-[side=top]:-bottom-[5px] data-[side=left]:-right-[5px] data-[side=right]:-left-[5px]";

export interface MenuFrameProps extends BaseMenu.Popup.Props {
  /** The popup's data-slot. */
  slot: string;
  side?: BaseMenu.Positioner.Props["side"];
  align?: BaseMenu.Positioner.Props["align"];
  sideOffset?: number;
  alignOffset?: number;
  portal?: BaseMenu.Portal.Props;
}

/** Portal + Positioner + Popup of a menu or submenu, in the menu material. */
export function MenuFrame({
  slot,
  side,
  align,
  sideOffset,
  alignOffset,
  portal,
  className,
  children,
  ...props
}: MenuFrameProps): React.ReactElement {
  return (
    <BaseMenu.Portal {...portal}>
      <BaseMenu.Positioner
        className="z-50 outline-none"
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
      >
        <BaseMenu.Popup
          data-slot={slot}
          className={sx(
            [
              "min-w-48 max-w-(--available-width) max-h-(--available-height) overflow-y-auto overscroll-contain p-1",
              overlayPopup,
            ],
            className,
          )}
          {...props}
        >
          {children}
        </BaseMenu.Popup>
      </BaseMenu.Positioner>
    </BaseMenu.Portal>
  );
}
