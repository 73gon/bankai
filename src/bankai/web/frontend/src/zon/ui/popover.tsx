import { Popover as BasePopover } from "@base-ui/react/popover";
import * as React from "react";
import { sx, triggerSlot } from "@/zon/lib/cn";
import { mergeRefs } from "@/zon/lib/merge-refs";
import { roomBounds, TriggerRoomContext, useTriggerRoom } from "@/zon/lib/room";
import { overlayArrow, overlayPopup } from "@/zon/ui/_overlay";

/**
 * Popover — a small panel anchored to a control: a hint with a link, a short form, a few
 * settings. Menu material at 12px.
 *
 * The rule people break: putting a whole task in it. If it needs a heading, a scroll and a
 * footer, it is a `Dialog`. If it only lists actions, it is a `Menu`.
 */
export function Popover(props: BasePopover.Root.Props) {
  // The trigger registers here, so the content can keep inside the trigger's room.
  const room = useTriggerRoom();
  return (
    <TriggerRoomContext.Provider value={room}>
      <BasePopover.Root {...props} />
    </TriggerRoomContext.Provider>
  );
}

/**
 * Opens the popover. Compose with a button: `render={<Button variant="secondary" />}`.
 * Pass `openOnHover` (with `delay`) for hover-intent popovers.
 */
export function PopoverTrigger({ ref, ...props }: BasePopover.Trigger.Props) {
  const setTrigger = React.useContext(TriggerRoomContext)?.setTrigger;
  const refs = React.useMemo(() => mergeRefs(setTrigger, ref), [setTrigger, ref]);
  return <BasePopover.Trigger ref={refs} {...triggerSlot("popover-trigger", props.render)} {...props} />;
}

export interface PopoverContentProps extends BasePopover.Popup.Props {
  side?: BasePopover.Positioner.Props["side"];
  align?: BasePopover.Positioner.Props["align"];
  sideOffset?: BasePopover.Positioner.Props["sideOffset"];
  alignOffset?: BasePopover.Positioner.Props["alignOffset"];
  /** Any other positioner props: `collisionPadding`, `anchor`, `sticky` … */
  positioner?: Omit<BasePopover.Positioner.Props, "children">;
  /** Props for the portal (e.g. `container`, `keepMounted`). */
  portal?: Omit<BasePopover.Portal.Props, "children">;
  /** Draw a small notch pointing at the trigger. */
  arrow?: boolean;
}

/** Portal + Positioner + Popup. Fades and scales from the trigger (`popup-motion`). */
export function PopoverContent({
  side = "bottom",
  align = "center",
  sideOffset = 8,
  alignOffset = 0,
  positioner,
  portal,
  arrow = false,
  className,
  children,
  ...props
}: PopoverContentProps) {
  // Across, keep inside the trigger's room (a narrow panel), not only the window.
  const trigger = React.useContext(TriggerRoomContext)?.trigger ?? null;
  return (
    <BasePopover.Portal {...portal}>
      <BasePopover.Positioner
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        arrowPadding={14}
        collisionPadding={16}
        collisionBoundary={roomBounds(trigger)}
        {...positioner}
        className="z-50 outline-none"
      >
        <BasePopover.Popup
          data-slot="popover-content"
          className={sx(
            ["relative flex w-72 max-w-(--available-width) flex-col gap-1.5 p-4 text-sm", overlayPopup],
            className,
          )}
          {...props}
        >
          {arrow && <BasePopover.Arrow data-slot="popover-arrow" className={overlayArrow} />}
          {children}
        </BasePopover.Popup>
      </BasePopover.Positioner>
    </BasePopover.Portal>
  );
}

export function PopoverTitle({ className, ...props }: BasePopover.Title.Props) {
  return (
    <BasePopover.Title
      data-slot="popover-title"
      className={sx(
        "font-body text-[15px] leading-snug font-medium tracking-normal text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function PopoverDescription({ className, ...props }: BasePopover.Description.Props) {
  return (
    <BasePopover.Description
      data-slot="popover-description"
      className={sx("text-[13px] leading-normal text-muted-foreground", className)}
      {...props}
    />
  );
}

/** Closes the popover. Compose with a button: `render={<Button variant="ghost" size="sm" />}`. */
export function PopoverClose(props: BasePopover.Close.Props) {
  return <BasePopover.Close data-slot="popover-close" {...props} />;
}
