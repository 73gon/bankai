import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import * as React from "react";
import { sx } from "@/zon/lib/cn";

export type TabsVariant = "segmented" | "underline";

const TabsVariantContext = React.createContext<TabsVariant>("segmented");

/**
 * Tabs — switch between related panels on the same page.
 *
 * Two looks, set on `TabsList`: `segmented` (default) is a recessed track holding one raised,
 * current segment; `underline` is mono labels on a hairline baseline with a 1px foreground
 * indicator. The rule people break: tabs switch *panels*, not pages. For site sections use
 * `NavigationMenu` or `Sidebar`; for 2–5 options that set a value, use `ToggleGroup`.
 *
 * The root is full width, so it never shrink-wraps to the active panel's text: switching
 * tabs never moves the list. Panels may differ in height; content below them reflows.
 */
export function Tabs({ className, ...props }: BaseTabs.Root.Props) {
  return (
    <BaseTabs.Root
      data-slot="tabs"
      className={sx("flex w-full flex-col gap-5 data-[orientation=vertical]:flex-row", className)}
      {...props}
    />
  );
}

const listVariants: Record<TabsVariant, string> = {
  segmented:
    "relative z-0 inline-flex w-fit min-h-(--control-h) items-center gap-0.5 rounded-control p-[3px] material-field data-[orientation=vertical]:flex-col data-[orientation=vertical]:items-stretch",
  underline:
    "relative z-0 flex items-center gap-6 border-b border-border/60 data-[orientation=vertical]:flex-col data-[orientation=vertical]:items-start data-[orientation=vertical]:gap-0 data-[orientation=vertical]:border-b-0 data-[orientation=vertical]:border-l",
};

const indicatorVariants: Record<TabsVariant, string> = {
  segmented: [
    "pointer-events-none absolute top-0 left-0 -z-10 h-(--active-tab-height) w-(--active-tab-width) translate-x-(--active-tab-left) translate-y-(--active-tab-top) rounded-item",
    "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-raised",
    "transition-[translate,width,height] duration-(--duration) ease-(--ease)",
  ].join(" "),
  underline: [
    "pointer-events-none absolute -bottom-px left-0 h-px w-(--active-tab-width) translate-x-(--active-tab-left) bg-foreground",
    "data-[orientation=vertical]:top-0 data-[orientation=vertical]:bottom-auto data-[orientation=vertical]:-left-px data-[orientation=vertical]:h-(--active-tab-height) data-[orientation=vertical]:w-px data-[orientation=vertical]:translate-x-0 data-[orientation=vertical]:translate-y-(--active-tab-top)",
    "transition-[translate,width,height] duration-(--duration) ease-(--ease)",
  ].join(" "),
};

export interface TabsListProps extends BaseTabs.List.Props {
  /** `segmented` (default): recessed track + raised current segment. `underline`: mono labels on a hairline. */
  variant?: TabsVariant;
}

/** The row of tabs. Renders the animated indicator itself — do not add one. */
export function TabsList({ variant = "segmented", className, children, ...props }: TabsListProps) {
  return (
    <TabsVariantContext.Provider value={variant}>
      <BaseTabs.List
        data-slot="tabs-list"
        data-variant={variant}
        className={sx(listVariants[variant], className)}
        {...props}
      >
        {children}
        <BaseTabs.Indicator data-slot="tabs-indicator" className={indicatorVariants[variant]} />
      </BaseTabs.List>
    </TabsVariantContext.Provider>
  );
}

const tabVariants: Record<TabsVariant, string> = {
  segmented:
    "min-h-(--control-h-sm) gap-2 px-3 text-sm justify-center rounded-item font-body text-control-muted hover:text-foreground data-active:text-foreground",
  underline:
    "min-h-(--control-h) gap-2 font-mono text-[13px] text-muted-foreground hover:text-foreground data-active:text-foreground data-[orientation=vertical]:min-h-(--control-h-sm) data-[orientation=vertical]:pl-4",
};

/** One tab. Pass a `value` matching its `TabsPanel`. */
export function TabsTab({ className, ...props }: BaseTabs.Tab.Props) {
  const variant = React.useContext(TabsVariantContext);
  return (
    <BaseTabs.Tab
      data-slot="tabs-tab"
      className={sx(
        [
          "relative inline-flex shrink-0 cursor-pointer items-center leading-none whitespace-nowrap focus-ring select-none",
          "transition-colors duration-(--duration) ease-(--ease)",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          tabVariants[variant],
        ],
        className,
      )}
      {...props}
    />
  );
}

/** The content for one tab. Only the active panel is mounted unless `keepMounted` is set. */
export function TabsPanel({ className, ...props }: BaseTabs.Panel.Props) {
  return (
    <BaseTabs.Panel
      data-slot="tabs-panel"
      className={sx("min-w-0 flex-1 focus-ring", className)}
      {...props}
    />
  );
}
