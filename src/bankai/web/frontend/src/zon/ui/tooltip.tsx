import { Tooltip as BaseTooltip } from "@base-ui/react/tooltip";
import * as React from "react";
import { sx, triggerSlot } from "@/zon/lib/cn";
import { overlayArrow } from "@/zon/ui/_overlay";

/** The quick open delay (ms): long enough to pass over a control without a flash, short enough to feel immediate. */
const TOOLTIP_DELAY = 150;

/** True below a TooltipProvider, whose delay a trigger then inherits. */
const InProviderContext = React.createContext(false);

/**
 * TooltipProvider — shares one quick delay across every tooltip below it, so moving from one
 * icon button to the next opens the next tooltip instantly. Wrap the app (or a toolbar) once.
 */
export function TooltipProvider({
  delay = TOOLTIP_DELAY,
  closeDelay = 0,
  ...props
}: BaseTooltip.Provider.Props) {
  return (
    <InProviderContext.Provider value>
      <BaseTooltip.Provider delay={delay} closeDelay={closeDelay} {...props} />
    </InProviderContext.Provider>
  );
}

/**
 * Tooltip — a short label for a control that has no visible text, shown on hover and focus.
 *
 * The rule people break: putting essential information or interactive content in it. Tooltips
 * are invisible on touch; anything people must read or click belongs in a `Popover` or on the page.
 */
export const Tooltip = BaseTooltip.Root;

/**
 * The element the tooltip describes. Compose with a control: `render={<Button size="icon" … />}`.
 * It opens after the provider's delay, or the same quick 150ms without one; pass `delay` for a
 * tooltip that should wait longer.
 */
export function TooltipTrigger({ delay, ...props }: BaseTooltip.Trigger.Props) {
  const inProvider = React.useContext(InProviderContext);
  return (
    <BaseTooltip.Trigger
      {...triggerSlot("tooltip-trigger", props.render)}
      delay={delay ?? (inProvider ? undefined : TOOLTIP_DELAY)}
      {...props}
    />
  );
}

const tooltipVariants = {
  /** 12px Licht Mono — labels, shortcuts, values. */
  mono: "font-mono text-xs leading-4 tracking-normal",
  /** 13px Licht Sans — a short sentence. */
  text: "font-body text-[13px] leading-snug",
} as const;

export interface TooltipContentProps extends BaseTooltip.Popup.Props {
  variant?: keyof typeof tooltipVariants;
  side?: BaseTooltip.Positioner.Props["side"];
  align?: BaseTooltip.Positioner.Props["align"];
  sideOffset?: BaseTooltip.Positioner.Props["sideOffset"];
  alignOffset?: BaseTooltip.Positioner.Props["alignOffset"];
  /** Any other positioner props: `collisionPadding`, `sticky`, `anchor` … */
  positioner?: Omit<BaseTooltip.Positioner.Props, "children">;
  /** Props for the portal (e.g. `container`). */
  portal?: Omit<BaseTooltip.Portal.Props, "children">;
  /** Draw a small notch pointing at the trigger. */
  arrow?: boolean;
}

/** Portal + Positioner + Popup. Menu material at the 7px item radius, `popup-motion`. */
export function TooltipContent({
  variant = "mono",
  side = "top",
  align = "center",
  sideOffset = 6,
  alignOffset = 0,
  positioner,
  portal,
  arrow = false,
  className,
  children,
  ...props
}: TooltipContentProps) {
  return (
    <BaseTooltip.Portal {...portal}>
      <BaseTooltip.Positioner
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        arrowPadding={8}
        collisionPadding={8}
        {...positioner}
        className="z-50 outline-none"
      >
        <BaseTooltip.Popup
          data-slot="tooltip-content"
          data-variant={variant}
          className={sx(
            [
              "relative max-w-64 popup-motion rounded-item material-menu px-2 py-1 text-foreground outline-none select-none",
              tooltipVariants[variant],
            ],
            className,
          )}
          {...props}
        >
          {arrow && <BaseTooltip.Arrow data-slot="tooltip-arrow" className={overlayArrow} />}
          {children}
        </BaseTooltip.Popup>
      </BaseTooltip.Positioner>
    </BaseTooltip.Portal>
  );
}
