import type * as React from "react";
import { cn } from "@/zon/lib/cn";

const raised = "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-key";

/* A coloured tag is a tint, not a key: the colour's ink on a 10% wash inside a 30% edge, so the
   text keeps 4.5:1 in both themes. Spelled out per colour so Tailwind sees every class. */
const tints = {
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/30 bg-warning/10 text-warning",
  info: "border-info/30 bg-info/10 text-info",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  "tone-blue": "border-tone-blue/30 bg-tone-blue/10 text-tone-blue",
  "tone-aqua": "border-tone-aqua/30 bg-tone-aqua/10 text-tone-aqua",
  "tone-violet": "border-tone-violet/30 bg-tone-violet/10 text-tone-violet",
  "tone-magenta": "border-tone-magenta/30 bg-tone-magenta/10 text-tone-magenta",
  "tone-orange": "border-tone-orange/30 bg-tone-orange/10 text-tone-orange",
  "tone-lime": "border-tone-lime/30 bg-tone-lime/10 text-tone-lime",
} as const;
const tint = (colour: keyof typeof tints) => `border ${tints[colour]}`;

const tagVariants = {
  /** Raised, foreground ink. */
  default: `${raised} text-foreground`,
  /** Raised, muted ink — for secondary metadata. */
  muted: `${raised} text-muted-foreground`,
  /** Border only, no fill — for tags sitting on a raised surface already. */
  outline: "border border-field-edge bg-transparent text-foreground-soft",
  /** Status: done, saved, connected. */
  success: tint("success"),
  /** Status: needs attention soon — expiring, partial, nearly full. */
  warning: tint("warning"),
  /** Status: neutral news — new, in progress, unsaved. */
  info: tint("info"),
  /** Status: an error or a failed state. */
  destructive: tint("destructive"),
  /** Category tones: no fixed meaning — pick one per kind and keep it everywhere. */
  blue: tint("tone-blue"),
  aqua: tint("tone-aqua"),
  violet: tint("tone-violet"),
  magenta: tint("tone-magenta"),
  orange: tint("tone-orange"),
  lime: tint("tone-lime"),
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
 * Tags are labels, not controls — they do not respond to hover. Coloured tags are tints: the
 * four status colours for a state, the six category tones for a kind people need to tell apart
 * (a file type, a phase). The rule people break: colour as decoration — a tone with no meaning,
 * or the same tone meaning two things in one app.
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
