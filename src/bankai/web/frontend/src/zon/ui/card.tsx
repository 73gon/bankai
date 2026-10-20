import { useRender } from "@base-ui/react/use-render";
import * as React from "react";
import { cn } from "@/zon/lib/cn";

/*
 * Region or object? Ask whether the thing could be picked up and moved somewhere else on
 * its own. A quote card, a pricing card, a data table could: that is an object → `Card`
 * (20px, padding, the one soft drop shadow). A section panel, a specimen sheet, a page cover
 * could not: that is a region → `Surface` (square, no shadow). When unsure, prefer the
 * region — and before either, ask whether a hairline rule and whitespace would do
 * (`FeatureCard`, `Separator`). Boxing every group flattens the hierarchy.
 */

export type CardProps = useRender.ComponentProps<"div">;

/**
 * Card — an **object**: a discrete thing sitting *on* the page (a quote, a pricing option,
 * a form panel). `surface-card` material, 20px radius, the system's only drop shadow, and
 * responsive 24/32/40px padding that belongs to the card — never pad it at the call site.
 *
 * Region vs object: could it be picked up and moved somewhere else on its own? Yes → `Card`.
 * No (it *is* that part of the page) → `Surface`. Unsure → `Surface`, or better, no box at
 * all. The rule people break: wrapping content in a card because it feels tidier.
 */
export function Card({ render, ref, className, ...props }: CardProps) {
  return useRender({
    defaultTagName: "div",
    render,
    ref,
    props: {
      "data-slot": "card",
      ...props,
      className: cn("flex flex-col gap-6 surface-card text-card-foreground", className),
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

type SurfaceTag = "div" | "section" | "aside" | "article" | "header" | "footer" | "figure" | "main";

export interface SurfaceProps extends useRender.ComponentProps<"div"> {
  /** The element to render. Defaults to `div`. Use `render` to compose another component. */
  as?: SurfaceTag;
  /** Apply the responsive 24/32/40px padding. Defaults to `true`. */
  padded?: boolean;
}

/**
 * Surface — a **region**: part of the page (a section panel, a specimen sheet, a cover,
 * a grid). The shared `surface-container` material — `--field-edge` border, 160° gradient,
 * inset highlight — with square corners and no shadow. Comes with 24/32/40px padding;
 * set `padded={false}` for full-bleed contents.
 *
 * Region vs object: could it be picked up and moved somewhere else on its own? No → `Surface`.
 * Yes → `Card`. The rule people break: rounding a region or giving it a shadow, which makes
 * it look detachable when it is not.
 */
export function Surface({ as = "div", padded = true, render, ref, className, ...props }: SurfaceProps) {
  return useRender({
    defaultTagName: as,
    render,
    ref,
    props: {
      "data-slot": "surface",
      "data-padded": padded ? "" : undefined,
      ...props,
      className: cn(
        padded && "p-6 sm:p-8 md:p-10",
        "rounded-none text-foreground surface-container",
        className,
      ),
    },
  });
}

export interface FeatureCardProps extends Omit<React.ComponentProps<"div">, "title"> {
  /** A mono index or eyebrow above the title, e.g. `"01"` or `"Automation"`. */
  index?: React.ReactNode;
  /** Optional 20px lucide icon, shown above the title. */
  icon?: React.ReactNode;
  /** The feature's name, set in Licht. */
  title?: React.ReactNode;
}

/**
 * FeatureCard — despite the name, **not a box**: a top hairline rule and padding, with no
 * background, no border around it and no radius. Lay several out in a grid to describe
 * services, process steps or principles.
 *
 * The rule people break: "upgrading" it to a `Card`. The rule and the whitespace carry the
 * structure; boxing each feature flattens the hierarchy instead of clarifying it.
 */
export function FeatureCard({ index, icon, title, className, children, ...props }: FeatureCardProps) {
  return (
    <div
      data-slot="feature-card"
      className={cn("flex flex-col gap-3 border-t border-border/60 pt-6 pb-8", className)}
      {...props}
    >
      {index != null && (
        <span data-slot="feature-card-index" className="eyebrow tabular-nums">
          {index}
        </span>
      )}
      {icon != null && (
        <span aria-hidden className="text-foreground [&_svg]:size-5">
          {icon}
        </span>
      )}
      {title != null && <h3 className="m-0 display text-[24px] leading-[1.1] text-foreground">{title}</h3>}
      {children != null && (
        <div className="max-w-[48ch] text-[15px] leading-relaxed text-foreground-soft">{children}</div>
      )}
    </div>
  );
}
