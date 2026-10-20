import { Popover as BasePopover } from "@base-ui/react/popover";
import { sx, triggerSlot } from "@/zon/lib/cn";
import { overlayArrow, overlayPopup } from "@/zon/ui/_overlay";

/**
 * Popover — a small panel anchored to a control: a hint with a link, a short form, a few
 * settings. Menu material at 12px.
 *
 * The rule people break: putting a whole task in it. If it needs a heading, a scroll and a
 * footer, it is a `Dialog`. If it only lists actions, it is a `Menu`.
 */
export const Popover = BasePopover.Root;

/**
 * Opens the popover. Compose with a button: `render={<Button variant="secondary" />}`.
 * Pass `openOnHover` (with `delay`) for hover-intent popovers.
 */
export function PopoverTrigger(props: BasePopover.Trigger.Props) {
  return <BasePopover.Trigger {...triggerSlot("popover-trigger", props.render)} {...props} />;
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
  return (
    <BasePopover.Portal {...portal}>
      <BasePopover.Positioner
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        arrowPadding={14}
        collisionPadding={16}
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
