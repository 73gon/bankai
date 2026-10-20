import { Select as BaseSelect } from "@base-ui/react/select";
import { CheckIcon, ChevronDownIcon, ChevronsUpDownIcon, ChevronUpIcon } from "lucide-react";
import type * as React from "react";
import { cn, sx } from "@/zon/lib/cn";

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
      <BaseSelect.Icon className="flex text-muted-foreground transition-colors group-hover/trigger:text-foreground">
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
  /** Positioner props: side, align, sideOffset, alignItemWithTrigger … */
  positioner?: Omit<BaseSelect.Positioner.Props, "children">;
}

export function SelectContent({ className, children, positioner, ...props }: SelectContentProps) {
  return (
    <BaseSelect.Portal>
      <BaseSelect.Positioner
        sideOffset={6}
        alignItemWithTrigger={false}
        {...positioner}
        className="z-50 outline-none select-none"
      >
        <BaseSelect.Popup
          data-slot="select-content"
          className={sx(
            [
              "min-w-(--anchor-width) popup-motion overflow-hidden rounded-button material-menu p-1 font-body outline-none",
            ],
            className,
          )}
          {...props}
        >
          <BaseSelect.ScrollUpArrow className="flex h-6 w-full cursor-default items-center justify-center text-muted-foreground">
            <ChevronUpIcon className="size-3.5" />
          </BaseSelect.ScrollUpArrow>
          <BaseSelect.List className="max-h-(--available-height) scroll-py-1 overflow-y-auto">
            {children}
          </BaseSelect.List>
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
          "grid min-h-(--control-h) cursor-pointer grid-cols-[1fr_1rem] items-center gap-3 rounded-item border border-transparent pr-2.5 pl-2.5 text-sm text-foreground-soft outline-none select-none",
          "data-highlighted:border-control-inner-edge data-highlighted:bg-control-hover data-highlighted:text-foreground data-highlighted:shadow-highlight",
          "data-disabled:pointer-events-none data-disabled:opacity-50 data-selected:text-foreground",
        ],
        className,
      )}
      {...props}
    >
      <BaseSelect.ItemText className="col-start-1 truncate">{children}</BaseSelect.ItemText>
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
