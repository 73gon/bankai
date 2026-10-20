import { Slider as BaseSlider } from "@base-ui/react/slider";
import type * as React from "react";
import { cn, sx } from "@/zon/lib/cn";

export interface SliderProps<
  Value extends number | readonly number[] = number | readonly number[],
> extends BaseSlider.Root.Props<Value> {
  /** A visible label above the track, associated with every thumb. */
  label?: React.ReactNode;
  /** Show the current value (or "min – max" for a range) in mono beside the label. */
  showValue?: boolean;
  /** Accessible names per thumb — required for range sliders ("Minimum budget", "Maximum budget"). */
  thumbLabels?: string[];
}

/**
 * Slider — a recessed 6px track with a soft indicator fill and raised 18px thumbs.
 *
 * Pass an array as `value`/`defaultValue` for a range; one thumb is rendered per value. Use a
 * slider when the rough position matters more than the exact number — for exact values use
 * `NumberField`. The rule people break: a range slider without `thumbLabels`, so both thumbs
 * announce the same name.
 */
export function Slider<Value extends number | readonly number[] = number | readonly number[]>({
  label,
  showValue = false,
  thumbLabels,
  className,
  children,
  ...props
}: SliderProps<Value>) {
  const source = props.value ?? props.defaultValue;
  const count = Array.isArray(source) ? source.length : 1;
  const hasHeader = label != null || showValue;

  return (
    <BaseSlider.Root<Value>
      data-slot="slider"
      className={sx(
        "group/slider flex w-full flex-col gap-2 data-[orientation=vertical]:h-full data-[orientation=vertical]:w-auto data-disabled:[&>[data-slot=slider-control]]:opacity-55",
        className,
      )}
      {...props}
    >
      {hasHeader && (
        <div data-slot="slider-header" className="flex items-baseline justify-between gap-4">
          {label != null && (
            <BaseSlider.Label
              data-slot="slider-label"
              className="text-sm leading-snug font-medium text-foreground select-none group-data-disabled/slider:text-foreground-soft"
            >
              {label}
            </BaseSlider.Label>
          )}
          {showValue && (
            <BaseSlider.Value
              data-slot="slider-value"
              className="ml-auto font-mono text-xs text-muted-foreground tabular-nums group-data-disabled/slider:opacity-55"
            >
              {(formatted) => formatted.join(" – ")}
            </BaseSlider.Value>
          )}
        </div>
      )}
      <BaseSlider.Control
        data-slot="slider-control"
        className="flex w-full touch-none items-center py-2 select-none data-disabled:cursor-not-allowed data-[orientation=vertical]:h-full data-[orientation=vertical]:min-h-32 data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col data-[orientation=vertical]:px-2 data-[orientation=vertical]:py-0"
      >
        <BaseSlider.Track
          data-slot="slider-track"
          className="relative h-1.5 w-full material-field rounded-full select-none data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5"
        >
          <BaseSlider.Indicator
            data-slot="slider-indicator"
            className="rounded-full bg-foreground-soft select-none"
          />
          {Array.from({ length: count }, (_, index) => (
            <BaseSlider.Thumb
              key={index}
              index={index}
              aria-label={thumbLabels?.[index]}
              data-slot="slider-thumb"
              className={cn(
                "size-[18px] cursor-grab rounded-full bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent select-none",
                "shadow-thumb",
                "transition-[background-color,box-shadow] duration-(--duration) ease-brand",
                "hover:bg-control-hover data-dragging:cursor-grabbing",
                "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-0 has-[:focus-visible]:outline-control-focus",
                "data-disabled:cursor-not-allowed",
              )}
            />
          ))}
          {children}
        </BaseSlider.Track>
      </BaseSlider.Control>
    </BaseSlider.Root>
  );
}
