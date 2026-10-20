import type * as React from "react";
import { cn } from "@/zon/lib/cn";

const badgeVariants = {
  /** The primary fill: dark in light mode, light in dark mode. Counts that need attention. */
  default: "bg-(--primary-fill) text-(--primary-text) shadow-checked",
  /** The raised key surface with foreground ink. Quiet counts and "New" markers. */
  secondary:
    "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent text-foreground shadow-key",
  /** Status only: done, connected, online. Never decoration. */
  success: "bg-success text-background shadow-checked",
  /** Status only: failed, offline, an error count. Never decoration. */
  destructive: "bg-destructive text-background shadow-checked",
} as const;

const badgeSizes = {
  /** 16px — on 30px controls, 24px avatars and inline in dense rows. */
  sm: "h-4 min-w-4 px-1 text-[10px]",
  /** 20px — on 36–46px controls and 32–40px avatars. */
  default: "h-5 min-w-5 px-1.5 text-[11px]",
} as const;

const dotSizes = {
  sm: "size-2",
  default: "size-2.5",
} as const;

export type BadgeVariant = keyof typeof badgeVariants;
export type BadgeSize = keyof typeof badgeSizes;

export interface BadgeProps extends React.ComponentProps<"span"> {
  variant?: BadgeVariant;
  /** `sm` 16px (dot 8px) · `default` 20px (dot 10px). */
  size?: BadgeSize;
  /**
   * A number to show. Above `max` it reads "99+"; zero or less renders nothing, so an empty
   * inbox shows no badge. Use `children` instead for a word ("New").
   */
  count?: number;
  /** The largest count shown in full. Default 99. */
  max?: number;
  /** Render a status dot with no text. Give it a `label`. */
  dot?: boolean;
  /**
   * Text for screen readers. Required for a `dot` ("Online"). For a count on an icon button,
   * put the count in the button's `aria-label` instead and leave this out.
   */
  label?: string;
}

/**
 * Badge — a small count or status mark: a notification count on an icon button or avatar,
 * a "New" marker beside a nav item, or a status dot. Mono, tabular numerals; 16 or 20px tall,
 * fully round. Monochrome by default; `success` and `destructive` are status only.
 *
 * Wrap the thing it marks in `BadgeAnchor` to pin it to the corner. Not the same as `Tag`:
 * a tag is metadata that names something ("TypeScript", "Draft"); a badge counts or flags
 * something about the object it sits on. The rule people break: colour as decoration — a
 * green "New" or a red count that is not an error.
 */
export function Badge({
  variant = "default",
  size = "default",
  count,
  max = 99,
  dot = false,
  label,
  className,
  children,
  ...props
}: BadgeProps) {
  if (count !== undefined && count <= 0 && !dot) return null;
  const content = count !== undefined ? (count > max ? `${max}+` : String(count)) : children;

  return (
    <span
      data-slot="badge"
      data-variant={variant}
      data-size={size}
      data-dot={dot || undefined}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full leading-none whitespace-nowrap select-none",
        badgeVariants[variant],
        dot
          ? dotSizes[size]
          : [
              "font-mono font-medium tracking-[0.02em] tabular-nums",
              "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3",
              badgeSizes[size],
            ],
        className,
      )}
      {...props}
    >
      {!dot && content}
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}

export interface BadgeAnchorProps extends React.ComponentProps<"span"> {
  /**
   * The outline of what is marked. `circle` (default) for avatars and icon buttons: the badge
   * sits on the circle's 45° point. `rect` for buttons and cards: it sits on the corner.
   */
  shape?: "circle" | "rect";
  /** `top-end` (default) for counts and markers; `bottom-end` for a presence dot on an avatar. */
  placement?: "top-end" | "bottom-end";
}

/**
 * BadgeAnchor — wraps an icon button, avatar or other object and pins a direct child `Badge`
 * to its corner, with a 2px cut-out in the page colour (`--badge-cutout`, default
 * `--background`; set it on a card). The badge ignores the pointer, so the control underneath
 * still receives every click.
 */
export function BadgeAnchor({
  shape = "circle",
  placement = "top-end",
  className,
  ...props
}: BadgeAnchorProps) {
  return (
    <span
      data-slot="badge-anchor"
      data-shape={shape}
      data-placement={placement}
      className={cn("relative inline-flex w-fit shrink-0 align-middle", className)}
      {...props}
    />
  );
}
