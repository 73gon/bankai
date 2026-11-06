import { Select as BaseSelect } from "@base-ui/react/select";
import { CheckIcon, ChevronDownIcon, ChevronsUpDownIcon, ChevronUpIcon } from "lucide-react";
import type * as React from "react";
import { cn, sx } from "@/zon/lib/cn";
import { ScrollArea } from "@/zon/ui/scroll-area";

/**
 * Select — pick one option from a list. Use `ToggleGroup` instead when there are 2–5 short
 * options and seeing them side by side is the point.
 *
 * Pass `items` to `<Select>` so `<SelectValue>` can render the selected label.
 */
export const Select = BaseSelect.Root;

const triggerSizes = {
  sm: "min-h-(--control-h-sm) px-2.5 text-[13px]",
  default: "min-h-(--control-h) pr-2.5 pl-3.5 text-[15px]",
} as const;

export interface SelectTriggerProps extends BaseSelect.Trigger.Props {
  size?: keyof typeof triggerSizes;
}

export function SelectTrigger({ size = "default", className, children, ...props }: SelectTriggerProps) {
  return (
    <BaseSelect.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={sx(
        [
          "group/trigger inline-flex w-full min-w-40 material-field cursor-pointer items-center justify-between gap-3 rounded-control font-body leading-none whitespace-nowrap outline-none select-none",
          triggerSizes[size],
        ],
        className,
      )}
      {...props}
    >
      {children}
      <BaseSelect.Icon className="flex text-muted-foreground transition-colors group-hover/trigger:not-group-data-disabled/trigger:text-foreground">
        <ChevronsUpDownIcon className="size-4" />
      </BaseSelect.Icon>
    </BaseSelect.Trigger>
  );
}

export function SelectValue({ className, ...props }: BaseSelect.Value.Props) {
  return (
    <BaseSelect.Value
      data-slot="select-value"
      className={sx(["truncate data-placeholder:text-control-muted"], className)}
      {...props}
    />
  );
}

export interface SelectContentProps extends BaseSelect.Popup.Props {
  /**
   * Open over the trigger, with the chosen option laid exactly on the value, as a macOS menu
   * does — instead of as a list below it. Mouse only: opened by touch, or with too little room
   * around the trigger, it opens below as usual.
   */
  alignItemWithTrigger?: boolean;
  /** Positioner props: side, align, sideOffset … */
  positioner?: Omit<BaseSelect.Positioner.Props, "children">;
}

export function SelectContent({
  className,
  children,
  alignItemWithTrigger = false,
  positioner,
  ...props
}: SelectContentProps) {
  return (
    <BaseSelect.Portal>
      <BaseSelect.Positioner
        sideOffset={6}
        alignItemWithTrigger={alignItemWithTrigger}
        {...positioner}
        className="z-50 outline-none select-none"
      >
        <BaseSelect.Popup
          data-slot="select-content"
          className={sx(
            [
              // As wide as the trigger, or wider for long options — up to 24rem (or the trigger, if
              // wider) and never past the window, so the popup never spans the screen.
              "max-w-[min(var(--available-width),max(var(--anchor-width),24rem))] min-w-(--anchor-width) popup-motion overflow-hidden rounded-button material-menu p-1 font-body outline-none",
            ],
            className,
          )}
          {...props}
        >
          <BaseSelect.ScrollUpArrow className="flex h-6 w-full cursor-default items-center justify-center text-muted-foreground">
            <ChevronUpIcon className="size-3.5" />
          </BaseSelect.ScrollUpArrow>
          {/* The list scrolls as ScrollArea's viewport: Select scrolls that element itself. */}
          <ScrollArea
            className="max-h-(--available-height)"
            viewportRender={
              <BaseSelect.List
                data-slot="select-list"
                role="listbox"
                className="scroll-py-1 data-has-overflow-y:pr-3"
              />
            }
          >
            {children}
          </ScrollArea>
          <BaseSelect.ScrollDownArrow className="flex h-6 w-full cursor-default items-center justify-center text-muted-foreground">
            <ChevronDownIcon className="size-3.5" />
          </BaseSelect.ScrollDownArrow>
        </BaseSelect.Popup>
      </BaseSelect.Positioner>
    </BaseSelect.Portal>
  );
}

export function SelectItem({ className, children, ...props }: BaseSelect.Item.Props) {
  return (
    <BaseSelect.Item
      data-slot="select-item"
      className={sx(
        [
          "grid min-h-(--control-h) cursor-pointer grid-cols-[1fr_1rem] items-center gap-3 rounded-item border border-transparent py-1.5 pr-2.5 pl-2.5 text-sm text-foreground-soft outline-none select-none",
          "data-highlighted:border-control-inner-edge data-highlighted:bg-control-hover data-highlighted:text-foreground data-highlighted:shadow-highlight",
          "data-disabled:pointer-events-none data-disabled:opacity-50 data-selected:text-foreground",
        ],
        className,
      )}
      {...props}
    >
      {/* A long option wraps to two lines, then ends in "…"; the row grows past 38px only then. */}
      <BaseSelect.ItemText className="col-start-1 line-clamp-2 [overflow-wrap:anywhere]">
        {children}
      </BaseSelect.ItemText>
      <BaseSelect.ItemIndicator className="col-start-2 flex">
        <CheckIcon className="size-4" />
      </BaseSelect.ItemIndicator>
    </BaseSelect.Item>
  );
}

export function SelectGroup({ className, ...props }: BaseSelect.Group.Props) {
  return <BaseSelect.Group data-slot="select-group" className={className} {...props} />;
}

export function SelectGroupLabel({ className, ...props }: BaseSelect.GroupLabel.Props) {
  return (
    <BaseSelect.GroupLabel
      data-slot="select-group-label"
      className={sx(["px-2.5 pt-2.5 pb-1.5 eyebrow text-[11px]"], className)}
      {...props}
    />
  );
}

export function SelectSeparator({ className, ...props }: React.ComponentProps<typeof BaseSelect.Separator>) {
  return (
    <BaseSelect.Separator
      data-slot="select-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className as string)}
      {...props}
    />
  );
}

export const SelectLabel = BaseSelect.Label;
