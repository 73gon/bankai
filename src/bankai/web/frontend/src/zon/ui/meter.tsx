import { Meter as BaseMeter } from "@base-ui/react/meter";
import type * as React from "react";
import { sx } from "@/zon/lib/cn";

export interface MeterProps extends BaseMeter.Root.Props {
  /** A visible mono label above the track. Without one, pass `aria-label`. */
  label?: React.ReactNode;
  /** Show the formatted value (e.g. `64%`) at the end of the label row. */
  showValue?: boolean;
}

/**
 * Meter — a static quantity within a known range: capacity used, a completeness score.
 *
 * The same recessed 6px track and `--foreground` indicator as `Progress`, with an optional
 * mono label and value. The rule people break: using it for work in progress. Something that
 * will finish is a `Progress`. Red is not a "high" colour — the meter stays monochrome.
 */
export function Meter({ label, showValue = false, className, children, ...props }: MeterProps) {
  return (
    <BaseMeter.Root
      data-slot="meter"
      className={sx("grid w-full grid-cols-[1fr_auto] items-baseline gap-x-3 gap-y-2", className)}
      {...props}
    >
      {label != null && (
        <BaseMeter.Label
          data-slot="meter-label"
          className="col-start-1 font-mono text-[12px] text-foreground-soft"
        >
          {label}
        </BaseMeter.Label>
      )}
      {showValue && (
        <BaseMeter.Value
          data-slot="meter-value"
          className="col-start-2 text-right font-mono text-[12px] text-muted-foreground tabular-nums"
        />
      )}
      <BaseMeter.Track
        data-slot="meter-track"
        className="relative col-span-2 h-1.5 overflow-hidden rounded-full border border-field-edge bg-field-surface shadow-field"
      >
        <BaseMeter.Indicator
          data-slot="meter-indicator"
          className="block h-full rounded-full bg-foreground transition-[width] duration-(--duration) ease-(--ease)"
        />
      </BaseMeter.Track>
      {children}
    </BaseMeter.Root>
  );
}
