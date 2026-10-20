import { Progress as BaseProgress } from "@base-ui/react/progress";
import type * as React from "react";
import { sx } from "@/zon/lib/cn";

export interface ProgressProps extends BaseProgress.Root.Props {
  /** A visible mono label above the track. Without one, pass `aria-label`. */
  label?: React.ReactNode;
  /** Show the formatted value (e.g. `64%`) at the end of the label row. */
  showValue?: boolean;
}

/**
 * Progress — how far a task has got: an upload, an import, a multi-step setup.
 *
 * A recessed 6px track with a `--foreground` indicator, plus an optional mono label and
 * value. Pass `value={null}` for indeterminate work. The rule people break: using it for a
 * static quantity (storage used, a score). That is a `Meter`.
 */
export function Progress({ label, showValue = false, className, children, ...props }: ProgressProps) {
  return (
    <BaseProgress.Root
      data-slot="progress"
      className={sx("grid w-full grid-cols-[1fr_auto] items-baseline gap-x-3 gap-y-2", className)}
      {...props}
    >
      {label != null && (
        <BaseProgress.Label
          data-slot="progress-label"
          className="col-start-1 font-mono text-[12px] text-foreground-soft"
        >
          {label}
        </BaseProgress.Label>
      )}
      {showValue && (
        <BaseProgress.Value
          data-slot="progress-value"
          className="col-start-2 text-right font-mono text-[12px] text-muted-foreground tabular-nums"
        />
      )}
      <BaseProgress.Track
        data-slot="progress-track"
        className="relative col-span-2 h-1.5 overflow-hidden rounded-full border border-field-edge bg-field-surface shadow-field"
      >
        <BaseProgress.Indicator
          data-slot="progress-indicator"
          className="block h-full rounded-full bg-foreground transition-[width] duration-(--duration) ease-(--ease)"
        />
      </BaseProgress.Track>
      {children}
    </BaseProgress.Root>
  );
}
