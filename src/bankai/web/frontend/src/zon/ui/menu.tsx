import { Menu as BaseMenu } from "@base-ui/react/menu";
import { CheckIcon, ChevronRightIcon } from "lucide-react";
import type * as React from "react";
import { cn, sx, triggerSlot } from "@/zon/lib/cn";
import { MenuFrame, type MenuFrameProps } from "@/zon/ui/_overlay";

/**
 * Menu — a list of actions opened from a button: "Rename", "Duplicate", "Delete project".
 * Menu material at 12px, items at 7px and 38px tall, the highlighted item raised like `SelectItem`.
 *
 * The rule people break: using a menu to pick a value. A menu *does* things; to choose a value
 * use `Select` (or `MenuRadioGroup` when the choice is a view setting next to other actions).
 */
export const Menu = BaseMenu.Root;

/** Opens the menu. Compose with a button: `render={<Button variant="secondary" />}`. */
export function MenuTrigger(props: BaseMenu.Trigger.Props) {
  return <BaseMenu.Trigger {...triggerSlot("menu-trigger", props.render)} {...props} />;
}

export type MenuContentProps = Omit<MenuFrameProps, "slot">;

/** Portal + Positioner + Popup. Opens below the trigger, aligned to its start edge. */
export function MenuContent({ align = "start", sideOffset = 6, ...props }: MenuContentProps) {
  return <MenuFrame slot="menu-content" align={align} sideOffset={sideOffset} {...props} />;
}

/* ── Items ─────────────────────────────────────────────────────────────────── */

/** Shared item geometry: 38px min height, 7px radius, raised fill + inner edge when highlighted. */
const menuItemClass = cn(
  "relative flex min-h-(--control-h) cursor-pointer items-center gap-2.5 rounded-item border border-transparent px-2.5 text-sm text-foreground-soft outline-none select-none",
  "data-highlighted:border-control-inner-edge data-highlighted:bg-control-hover data-highlighted:text-foreground data-highlighted:shadow-highlight",
  "data-disabled:pointer-events-none data-disabled:opacity-50",
  "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&>svg]:text-muted-foreground data-highlighted:[&>svg]:text-foreground",
);

const itemVariants = {
  default: "",
  /** Destructive ink for irreversible actions. Stays monochrome on the surface. */
  destructive:
    "text-destructive data-highlighted:text-destructive [&>svg]:text-destructive data-highlighted:[&>svg]:text-destructive",
} as const;

export type MenuItemVariant = keyof typeof itemVariants;

export interface MenuItemProps extends BaseMenu.Item.Props {
  variant?: MenuItemVariant;
}

/** One action. Put a 16px lucide icon first and a `MenuShortcut` last if it has one. */
export function MenuItem({ variant = "default", className, ...props }: MenuItemProps) {
  return (
    <BaseMenu.Item
      data-slot="menu-item"
      data-variant={variant}
      className={sx([menuItemClass, itemVariants[variant]], className)}
      {...props}
    />
  );
}

/** A menu item that is a link. Use for navigation so middle-click and "open in new tab" work. */
export function MenuLinkItem({ className, ...props }: BaseMenu.LinkItem.Props) {
  return <BaseMenu.LinkItem data-slot="menu-link-item" className={sx(menuItemClass, className)} {...props} />;
}

/** A toggle inside a menu ("Show grid"). The check sits at the trailing edge, like `SelectItem`. */
export function MenuCheckboxItem({ className, children, ...props }: BaseMenu.CheckboxItem.Props) {
  return (
    <BaseMenu.CheckboxItem
      data-slot="menu-checkbox-item"
      className={sx([menuItemClass, "pr-2"], className)}
      {...props}
    >
      <span className="flex min-w-0 flex-1 items-center gap-2.5">{children}</span>
      <BaseMenu.CheckboxItemIndicator className="flex text-foreground">
        <CheckIcon className="size-4" />
      </BaseMenu.CheckboxItemIndicator>
    </BaseMenu.CheckboxItem>
  );
}

/** Groups `MenuRadioItem`s that share one value (`value` / `onValueChange`). */
export function MenuRadioGroup(props: BaseMenu.RadioGroup.Props) {
  return <BaseMenu.RadioGroup data-slot="menu-radio-group" {...props} />;
}

/** One option of a `MenuRadioGroup`. The current option shows a small dot at the trailing edge. */
export function MenuRadioItem({ className, children, ...props }: BaseMenu.RadioItem.Props) {
  return (
    <BaseMenu.RadioItem
      data-slot="menu-radio-item"
      className={sx([menuItemClass, "pr-2"], className)}
      {...props}
    >
      <span className="flex min-w-0 flex-1 items-center gap-2.5">{children}</span>
      <BaseMenu.RadioItemIndicator className="flex size-4 items-center justify-center">
        <span className="size-1.5 rounded-full bg-foreground" />
      </BaseMenu.RadioItemIndicator>
    </BaseMenu.RadioItem>
  );
}

/** Groups related items so assistive tech announces them together. Label it with `MenuGroupLabel`. */
export function MenuGroup(props: BaseMenu.Group.Props) {
  return <BaseMenu.Group data-slot="menu-group" {...props} />;
}

/** An eyebrow above a group: 11px mono, uppercase, tracked. */
export function MenuGroupLabel({ className, ...props }: BaseMenu.GroupLabel.Props) {
  return (
    <BaseMenu.GroupLabel
      data-slot="menu-group-label"
      className={sx("px-2.5 pt-2.5 pb-1.5 eyebrow text-[11px] select-none", className)}
      {...props}
    />
  );
}

/** A hairline between groups of items. */
export function MenuSeparator({ className, ...props }: BaseMenu.Separator.Props) {
  return (
    <BaseMenu.Separator
      data-slot="menu-separator"
      className={sx("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  );
}

/** A keyboard hint at the trailing edge of an item: mono, muted. Decorative — wire the keys yourself. */
export function MenuShortcut({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="menu-shortcut"
      aria-hidden
      className={cn("ml-auto pl-4 font-mono text-xs tracking-normal text-muted-foreground", className)}
      {...props}
    />
  );
}

/* ── Submenus ──────────────────────────────────────────────────────────────── */

/** A nested menu. Holds a `MenuSubTrigger` and a `MenuSubContent`. */
export const MenuSub = BaseMenu.SubmenuRoot;

/** The item that opens a submenu; shows a chevron and stays raised while its submenu is open. */
export function MenuSubTrigger({ className, children, ...props }: BaseMenu.SubmenuTrigger.Props) {
  return (
    <BaseMenu.SubmenuTrigger
      data-slot="menu-sub-trigger"
      className={sx(
        [
          menuItemClass,
          "pr-2 data-popup-open:border-control-inner-edge data-popup-open:bg-control-hover data-popup-open:text-foreground",
        ],
        className,
      )}
      {...props}
    >
      {children}
      <ChevronRightIcon aria-hidden className="ml-auto size-4 text-muted-foreground" />
    </BaseMenu.SubmenuTrigger>
  );
}

/** The submenu popup. Opens to the inline end, its first item level with the trigger. */
export function MenuSubContent({ sideOffset = 2, alignOffset = -5, ...props }: MenuContentProps) {
  return <MenuFrame slot="menu-sub-content" sideOffset={sideOffset} alignOffset={alignOffset} {...props} />;
}
