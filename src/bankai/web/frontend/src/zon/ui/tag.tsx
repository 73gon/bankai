import type * as React from "react";
import { cn } from "@/zon/lib/cn";

const raised = "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-key";

const tagVariants = {
  /** Raised, foreground ink. */
  default: `${raised} text-foreground`,
  /** Raised, muted ink — for secondary metadata. */
  muted: `${raised} text-muted-foreground`,
  /** Border only, no fill — for tags sitting on a raised surface already. */
  outline: "border border-field-edge bg-transparent text-foreground-soft",
  /** Status only: an error or a failed state. Never decoration. */
  destructive: `${raised} text-destructive`,
} as const;

export type TagVariant = keyof typeof tagVariants;

export interface TagProps extends React.ComponentProps<"span"> {
  variant?: TagVariant;
  /** A leading 6px dot in the current ink, for status ("Live", "Draft"). */
  dot?: boolean;
}

/**
 * Tag — a small mono label for metadata: a technology, a status, a category.
 *
 * 22px tall, Licht Mono 11px with 0.02em tracking, 7px radius, on a raised key surface.
 * Tags are labels, not controls — they do not respond to hover. The rule people break:
 * colour-coding categories. The palette is monochrome; `destructive` is for status only.
 */
export function Tag({ variant = "default", dot = false, className, children, ...props }: TagProps) {
  return (
    <span
      data-slot="tag"
      data-variant={variant}
      className={cn(
        "inline-flex min-h-[22px] w-fit shrink-0 items-center gap-1.5 rounded-item px-2 font-mono text-[11px] leading-none tracking-[0.02em] whitespace-nowrap",
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3",
        tagVariants[variant],
        className,
      )}
      {...props}
    >
      {dot && <span aria-hidden data-slot="tag-dot" className="size-1.5 shrink-0 rounded-full bg-current" />}
      {children}
    </span>
  );
}
