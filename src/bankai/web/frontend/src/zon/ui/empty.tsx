import { useRender } from "@base-ui/react/use-render";
import type * as React from "react";
import { cn } from "@/zon/lib/cn";

/** The static raised key surface (no hover — the well is not a control). */
const raisedWell = "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-raised";

/**
 * Empty — the empty state for a list, a search with no results or a dashboard before its
 * first data. Centred, generous and calm: a media well, a Licht title, one or two sentences
 * that say what goes here and how to add it, and the actions that fix it.
 *
 * Empty has no box of its own; it sits inside whatever was going to hold the content (a
 * `Card`, a `Surface`, a table, a panel). The rule people break: a cheerful headline with no
 * next step. Say what is missing and offer the one action that fills it.
 */
export function Empty({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="empty"
      className={cn(
        "flex w-full min-w-0 flex-col items-center justify-center gap-3 px-6 py-12 text-center text-balance md:py-16",
        className,
      )}
      {...props}
    />
  );
}

const emptyMediaVariants = {
  /** A raised 46px circular well holding a 20px lucide icon. */
  icon: `mb-3 inline-flex size-(--control-h-lg) shrink-0 items-center justify-center rounded-full text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-5 ${raisedWell}`,
  /** A bare slot for an `<Illustration>` from the library: a drawing or an editorial image. */
  illustration: "mb-4 flex w-full max-w-[220px] items-center justify-center text-foreground [&>*]:max-w-full",
} as const;

export type EmptyMediaVariant = keyof typeof emptyMediaVariants;

export interface EmptyMediaProps extends React.ComponentProps<"div"> {
  variant?: EmptyMediaVariant;
}

/** The picture at the top: an icon well (`icon`, default) or an illustration slot. Decorative. */
export function EmptyMedia({ variant = "icon", className, ...props }: EmptyMediaProps) {
  return (
    <div
      aria-hidden
      data-slot="empty-media"
      data-variant={variant}
      className={cn(emptyMediaVariants[variant], className)}
      {...props}
    />
  );
}

/** The empty state's heading in Licht. Renders an `<h3>`; use `render` for another level. */
export function EmptyTitle({ render, ref, className, ...props }: useRender.ComponentProps<"h3">) {
  return useRender({
    defaultTagName: "h3",
    render,
    ref,
    props: {
      "data-slot": "empty-title",
      ...props,
      className: cn("m-0 display text-[24px] leading-[1.1] text-foreground sm:text-[28px]", className),
    },
  });
}

/** One or two sentences: what belongs here and how to add it. Links take the brand underline. */
export function EmptyDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="empty-description"
      className={cn(
        "max-w-[46ch] text-[15px] leading-relaxed text-muted-foreground [&_a]:link [&_p]:m-0 [&_p+p]:mt-2",
        className,
      )}
      {...props}
    />
  );
}

/** The actions that fill the empty state — at most one primary, the rest secondary or ghost. */
export function EmptyActions({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="empty-actions"
      className={cn("mt-4 flex flex-wrap items-center justify-center gap-3", className)}
      {...props}
    />
  );
}
