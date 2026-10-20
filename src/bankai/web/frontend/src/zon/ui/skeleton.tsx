import type * as React from "react";
import { cn } from "@/zon/lib/cn";

const skeletonShapes = {
  /** A line of text: square ends, 1em tall by default. Set the width per line. */
  text: "h-[1em] rounded-none",
  /** Stands in for an input, select trigger or textarea: 10px corners, 38px tall. */
  field: "min-h-(--control-h) w-full rounded-control",
  /** Stands in for a button: 12px corners, 38px tall. */
  button: "min-h-(--control-h) w-28 rounded-button",
  /** Stands in for a tag, a menu item or a thumbnail: 7px corners. */
  item: "rounded-item",
  /** Stands in for an avatar or an icon button. Set the size. */
  circle: "aspect-square rounded-full",
  /** Stands in for a whole card: 20px corners. */
  card: "rounded-card",
} as const;

export type SkeletonShape = keyof typeof skeletonShapes;

export interface SkeletonProps extends React.ComponentProps<"div"> {
  /** Match the radius (and default height) of what the skeleton stands in for. */
  shape?: SkeletonShape;
}

/**
 * Skeleton — a placeholder in `--control-surface` that holds the place of content while it
 * loads, with one soft highlight sweeping across it every 1.6s (static under reduced motion).
 *
 * The radius follows what it stands in for: `text` lines are square, `field` is 10px, `button`
 * is 12px, `circle` is round, `card` is 20px. Skeletons are `aria-hidden`; announce loading on
 * the container (`aria-busy`) instead. The rule people break: skeletons that do not match the
 * real layout, so the page jumps when the content arrives.
 */
export function Skeleton({ shape = "text", className, ...props }: SkeletonProps) {
  return (
    <div
      aria-hidden
      data-slot="skeleton"
      data-shape={shape}
      className={cn("block shrink-0", skeletonShapes[shape], className)}
      {...props}
    />
  );
}
