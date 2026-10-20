import { useRender } from "@base-ui/react/use-render";
import * as React from "react";
import { cn } from "@/zon/lib/cn";
import { Separator, type SeparatorProps } from "@/zon/ui/separator";

/** The static raised key surface, shared by the `raised` variant and the `icon` media well. */
const raised = "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-raised";

/*
 * Interactive items — rendered as a link or a button — lift on hover like a nav row. The
 * arbitrary variant keeps a static item (a div, an li, a label) from reacting to the pointer.
 */
const interactive = [
  "[&:is(a[href],button,[role=button],[role=link])]:cursor-pointer",
  "[&:is(a[href],button,[role=button],[role=link])]:hover:bg-control-hover",
  "[&:is(a[href],button,[role=button],[role=link])]:hover:bg-linear-to-b",
  "[&:is(a[href],button,[role=button],[role=link])]:hover:from-control-inner-edge",
  "[&:is(a[href],button,[role=button],[role=link])]:hover:to-transparent",
  "aria-disabled:pointer-events-none aria-disabled:opacity-55 data-disabled:pointer-events-none data-disabled:opacity-55",
].join(" ");

const itemVariants = {
  /** Flat, no edge. Group with `ItemSeparator` hairlines. Lifts into the raised material on hover when it is a link. */
  default:
    "bg-transparent [&:is(a[href],button,[role=button],[role=link])]:hover:text-foreground [&:is(a[href],button,[role=button],[role=link])]:hover:shadow-raised",
  /** A `--field-edge` border at 12px — a row that stands on its own. */
  outline:
    "border-field-edge bg-transparent [&:is(a[href],button,[role=button],[role=link])]:hover:shadow-highlight",
  /** The raised key surface — a row that reads as a thing you can act on. Presses inward. */
  raised: `${raised} [&:is(a[href],button,[role=button],[role=link])]:active:shadow-raised-pressed`,
} as const;

const itemSizes = {
  /** 46px minimum: title + description. */
  default: "min-h-(--control-h-lg) gap-4 px-4 py-3",
  /** 38px minimum: dense lists, settings rows. */
  sm: "min-h-(--control-h) gap-3 px-3 py-2",
} as const;

export type ItemVariant = keyof typeof itemVariants;
export type ItemSize = keyof typeof itemSizes;

export interface ItemProps extends useRender.ComponentProps<"div"> {
  variant?: ItemVariant;
  size?: ItemSize;
}

/**
 * Item — a flexible row: media, a title and description, and actions. For people, files,
 * integrations, settings and search results.
 *
 * `default` is flat and grouped by hairlines; `outline` takes a `--field-edge` border at 12px;
 * `raised` sits on the key surface. Rendered as a link or button (`render={<a href="…" />}`),
 * any variant lifts on hover like a nav row; a static item never responds to the pointer.
 * The rule people break: putting a button inside an item that is itself a link — make the
 * row the link, or keep the row static and give it actions.
 */
export function Item({ variant = "default", size = "default", render, ref, className, ...props }: ItemProps) {
  return useRender({
    defaultTagName: "div",
    render,
    ref,
    props: {
      "data-slot": "item",
      "data-variant": variant,
      "data-size": size,
      ...props,
      className: cn(
        "group/item flex w-full flex-wrap items-center rounded-button border border-transparent text-start text-sm text-foreground no-underline focus-ring sm:flex-nowrap",
        "transition-[background-color,color,box-shadow] duration-(--duration) ease-(--ease)",
        interactive,
        itemSizes[size],
        itemVariants[variant],
        className,
      ),
    },
  });
}

const itemMediaVariants = {
  /** The child as is — an `Avatar`, a lucide icon at 20px. */
  default: "[&_svg:not([class*='size-'])]:size-5",
  /** A raised 32px key with 7px corners holding a 16px icon. */
  icon: `size-8 rounded-item text-foreground [&_svg:not([class*='size-'])]:size-4 ${raised}`,
  /** A 40px thumbnail with 7px corners; the image covers it. */
  image: "size-10 overflow-hidden rounded-item ring-1 ring-field-edge [&_img]:size-full [&_img]:object-cover",
} as const;

export type ItemMediaVariant = keyof typeof itemMediaVariants;

export interface ItemMediaProps extends React.ComponentProps<"div"> {
  variant?: ItemMediaVariant;
}

/** The leading visual: an avatar or icon (`default`), a raised icon key (`icon`) or a thumbnail (`image`). */
export function ItemMedia({ variant = "default", className, ...props }: ItemMediaProps) {
  return (
    <div
      data-slot="item-media"
      data-variant={variant}
      className={cn(
        "flex shrink-0 items-center justify-center self-center text-foreground-soft [&_svg]:pointer-events-none [&_svg]:shrink-0",
        "group-has-data-[slot=item-description]/item:translate-y-0.5 group-has-data-[slot=item-description]/item:self-start",
        itemMediaVariants[variant],
        className,
      )}
      {...props}
    />
  );
}

/** The text column: title and description. Takes the remaining width. */
export function ItemContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="item-content"
      className={cn("flex min-w-0 flex-1 flex-col gap-1", className)}
      {...props}
    />
  );
}

/** The row's name. Licht Sans 14px medium; a `Tag` can sit beside it. */
export function ItemTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="item-title"
      className={cn("flex w-fit items-center gap-2 leading-snug font-medium text-foreground", className)}
      {...props}
    />
  );
}

/** One or two lines of muted detail; clamped at two. */
export function ItemDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="item-description"
      className={cn(
        "m-0 line-clamp-2 text-[13px] leading-normal text-muted-foreground [&_a]:link",
        className,
      )}
      {...props}
    />
  );
}

/** Trailing actions or meta: small buttons, a `Switch`, a `Tag`, a mono value. */
export function ItemActions({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="item-actions" className={cn("flex shrink-0 items-center gap-2", className)} {...props} />
  );
}

/**
 * A stack of items. For a semantic list, render it as a list and each item as a list item:
 * `<ItemGroup render={<ul />}>` with `<Item render={<li />}>`.
 */
const ItemListContext = React.createContext(false);

export function ItemGroup({ render, ref, className, ...props }: useRender.ComponentProps<"div">) {
  // Rendered as <ul>/<ol>, separators become <li role="separator"> so the list stays valid.
  const isList = React.isValidElement(render) && (render.type === "ul" || render.type === "ol");
  const element = useRender({
    defaultTagName: "div",
    render,
    ref,
    props: {
      "data-slot": "item-group",
      ...props,
      className: cn("m-0 flex w-full list-none flex-col gap-1 p-0", className),
    },
  });
  return <ItemListContext.Provider value={isList}>{element}</ItemListContext.Provider>;
}

/**
 * The hairline between items in a group — `Separator`, with the group's rhythm.
 * In a list group (`render={<ul />}`) it becomes an `aria-hidden` `<li>` wrapping the rule: a
 * `<ul>` may only hold list items, and a divider between them is noise to a screen reader.
 */
export function ItemSeparator({ className, ...props }: SeparatorProps) {
  const inList = React.useContext(ItemListContext);
  const rule = <Separator data-slot="item-separator" className={className} {...props} />;
  return inList ? (
    <li aria-hidden="true" data-slot="item-separator-item" className="list-none">
      {rule}
    </li>
  ) : (
    rule
  );
}
