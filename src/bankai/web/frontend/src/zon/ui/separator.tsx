import { Separator as BaseSeparator } from "@base-ui/react/separator";
import type * as React from "react";
import { sx } from "@/zon/lib/cn";

export interface SeparatorProps extends BaseSeparator.Props {
  /** A short mono label centred on a horizontal rule, e.g. `"or"` or `"02 — Process"`. */
  label?: React.ReactNode;
}

/**
 * Separator — the hairline rule, which is the default way to group content here.
 *
 * `border/60`, 1px, horizontal or vertical. An optional mono label sits in the middle of a
 * horizontal rule. The rule people break: reaching for a box (a `Card` or `Surface`) where
 * a rule and whitespace would do. Use this first.
 */
export function Separator({ orientation = "horizontal", label, className, ...props }: SeparatorProps) {
  if (label != null && orientation === "horizontal") {
    return (
      <BaseSeparator
        data-slot="separator"
        data-labelled=""
        orientation={orientation}
        className={sx(
          "flex w-full items-center gap-4 eyebrow text-[11px] before:h-px before:flex-1 before:bg-border/60 before:content-[''] after:h-px after:flex-1 after:bg-border/60 after:content-['']",
          className,
        )}
        {...props}
      >
        <span className="shrink-0">{label}</span>
      </BaseSeparator>
    );
  }
  return (
    <BaseSeparator
      data-slot="separator"
      orientation={orientation}
      className={sx(
        "shrink-0 bg-border/60 data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-full data-[orientation=vertical]:min-h-4 data-[orientation=vertical]:w-px data-[orientation=vertical]:self-stretch",
        className,
      )}
      {...props}
    />
  );
}
