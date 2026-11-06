import { useRender } from "@base-ui/react/use-render";
import * as React from "react";
import { cn } from "@/zon/lib/cn";

/*
 * Which kind? Ask first whether a hairline rule and whitespace would do: that is `plain`. Then
 * whether the thing could be picked up and moved somewhere else on its own — a quote, a pricing
 * option, a form panel: that is a card, `default` (20px, the one soft drop shadow). A section
 * panel, a specimen sheet, a page cover could not: it is part of the page, `square` (no radius,
 * no shadow). When unsure, prefer square — and before either, plain. Boxing every group
 * flattens the hierarchy.
 */

const cardVariants = {
  /** An object on the page: the card material, 20px corners, the system's only drop shadow. */
  default: "surface-card",
  /** A part of the page: the same material, square, no shadow, the card's padding. */
  square:
    "rounded-none surface-container p-[var(--card-padding,24px)] sm:p-[var(--card-padding,32px)] md:p-[var(--card-padding,40px)]",
  /** No box: no border, no ground, no shadow — a top hairline and whitespace. */
  plain: "border-t border-border/60 pt-6 pb-8",
} as const;

export type CardVariant = keyof typeof cardVariants;

export interface CardProps extends useRender.ComponentProps<"div"> {
  /**
   * `default`: an object, a 20px card with the soft shadow. `square`: a part of the page, the same
   * material with square corners and no shadow. `plain`: no box at all, a top hairline and space.
   */
  variant?: CardVariant;
}

/**
 * Card — a group of content in one of three kinds. `default` is an **object** sitting *on* the
 * page (a quote, a pricing option, a form panel): `surface-card` material, 20px radius, the
 * system's only drop shadow, and responsive 24/32/40px padding that belongs to the card — never
 * pad it at the call site. `square` is a **part of** the page (a section panel, a specimen sheet,
 * a cover): the same material, square, no shadow. `plain` is **no box**: a top hairline and
 * whitespace, for features, steps and principles laid out in a grid.
 *
 * Could it be picked up and moved somewhere else on its own? Yes → `default`. No → `square`.
 * Would a rule and whitespace do? → `plain`, before either. The rule people break: wrapping
 * content in a card because it feels tidier, or rounding a part of the page.
 */
export function Card({ variant = "default", render, ref, className, ...props }: CardProps) {
  return useRender({
    defaultTagName: "div",
    render,
    ref,
    props: {
      "data-slot": "card",
      "data-variant": variant,
      ...props,
      className: cn("flex flex-col gap-6 text-card-foreground", cardVariants[variant], className),
    },
  });
}

/** Title + description stack at the top of a card. */
export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-header" className={cn("flex flex-col gap-2", className)} {...props} />;
}

/** The card's heading, in Licht. Renders an `<h3>`; use `render` for another level. */
export function CardTitle({ render, ref, className, ...props }: useRender.ComponentProps<"h3">) {
  return useRender({
    defaultTagName: "h3",
    render,
    ref,
    props: {
      "data-slot": "card-title",
      ...props,
      className: cn("m-0 display text-[24px] leading-[1.1] text-foreground sm:text-[28px]", className),
    },
  });
}

/** Supporting copy under the title. */
export function CardDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="card-description"
      className={cn("m-0 text-[15px] leading-relaxed text-muted-foreground", className)}
      {...props}
    />
  );
}

/** The card's body. */
export function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("text-[15px] leading-relaxed text-foreground-soft", className)}
      {...props}
    />
  );
}

/** Actions or meta at the bottom of a card. One primary at most. */
export function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("mt-auto flex flex-wrap items-center gap-3", className)}
      {...props}
    />
  );
}
