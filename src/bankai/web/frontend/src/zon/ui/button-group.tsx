import type * as React from "react";
import { cn } from "@/zon/lib/cn";

/*
 * First and last are counted among the joined controls only. While a menu or popover is open,
 * Base UI renders invisible helper spans (focus guards, an aria-owns span) beside its trigger;
 * with plain :first-child / :last-child the trigger would stop being last and lose its outer
 * corners — the "blocky" open split button. How neighbours meet (merged field borders, one seam
 * between buttons) lives in parts/controls.css.
 */
const orientations = {
  horizontal: [
    "flex-row",
    // Inner corners collapse; the outer corners keep each control's own radius.
    "[&>*:not(:nth-child(1_of_:not([data-base-ui-focus-guard],span[aria-owns])))]:rounded-l-none",
    "[&>*:not(:nth-last-child(1_of_:not([data-base-ui-focus-guard],span[aria-owns])))]:rounded-r-none",
  ],
  vertical: [
    "flex-col",
    "[&>*:not(:nth-child(1_of_:not([data-base-ui-focus-guard],span[aria-owns])))]:rounded-t-none",
    "[&>*:not(:nth-last-child(1_of_:not([data-base-ui-focus-guard],span[aria-owns])))]:rounded-b-none",
  ],
} as const;

export interface ButtonGroupProps extends React.ComponentProps<"div"> {
  /** Lay the controls out in a row (default) or a column. */
  orientation?: keyof typeof orientations;
}

/**
 * ButtonGroup — joins related controls edge to edge: the inner corners collapse to 0, the
 * outer corners keep 12px (10px for fields), and neighbouring edges merge into one line.
 * Works with `Button` variants, `SelectTrigger`, `Input` and `InputGroup` side by side.
 *
 * Use it for a split button, a pager, or a field with the action that belongs to it. The rule
 * people break: joining unrelated actions to save space — separate actions stay separate
 * buttons with a gap. Give the group an `aria-label` that names what the controls act on.
 */
export function ButtonGroup({ orientation = "horizontal", className, ...props }: ButtonGroupProps) {
  return (
    <div
      role="group"
      data-slot="button-group"
      data-orientation={orientation}
      className={cn(
        "flex w-fit items-stretch",
        // The control being used rises above its neighbours so its focus outline is never covered.
        "[&>*]:relative [&>*:focus-visible]:z-10 [&>*:has(:focus-visible)]:z-10 [&>*[data-popup-open]]:z-10",
        orientations[orientation],
        className,
      )}
      {...props}
    />
  );
}

export interface ButtonGroupSeparatorProps extends React.ComponentProps<"div"> {
  /** Match the group: a vertical rule in a horizontal group (default), and vice versa. */
  orientation?: "vertical" | "horizontal";
}

/**
 * No longer needed: joined buttons draw one thin seam between them themselves (a soft line on a
 * split primary, the edge colour on raised buttons). Kept so existing markup keeps working; it
 * renders nothing visible.
 */
export function ButtonGroupSeparator({
  orientation = "vertical",
  className,
  ...props
}: ButtonGroupSeparatorProps) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      data-slot="button-group-separator"
      data-orientation={orientation}
      className={cn("hidden", className)}
      {...props}
    />
  );
}
