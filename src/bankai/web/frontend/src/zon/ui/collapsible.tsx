import { Collapsible as BaseCollapsible } from "@base-ui/react/collapsible";
import { ChevronRightIcon } from "lucide-react";
import { sx } from "@/zon/lib/cn";

/**
 * Collapsible — one region that can be shown and hidden by a trigger.
 *
 * Use it for secondary detail ("Show technical notes"), not for a list of questions —
 * that is an `Accordion`. The rule people break: hiding content people need to complete
 * the task. Collapse what is optional, never what is required.
 *
 * The root is full width, so it never shrink-wraps to the open panel: opening and closing
 * only grows and shrinks the panel, and the trigger stays exactly where it is.
 */
export function Collapsible({ className, ...props }: BaseCollapsible.Root.Props) {
  return (
    <BaseCollapsible.Root
      data-slot="collapsible"
      className={sx("flex w-full flex-col", className)}
      {...props}
    />
  );
}

export interface CollapsibleTriggerProps extends BaseCollapsible.Trigger.Props {
  /** Show the chevron that rotates when open. Defaults to `true`. */
  showIcon?: boolean;
}

/** A mono text trigger with a rotating chevron. Use `render` to swap in a `Button`. */
export function CollapsibleTrigger({
  showIcon = true,
  className,
  children,
  ...props
}: CollapsibleTriggerProps) {
  return (
    <BaseCollapsible.Trigger
      data-slot="collapsible-trigger"
      className={sx(
        [
          "group/collapsible-trigger inline-flex w-fit cursor-pointer items-center gap-2 py-1 font-mono text-[13px] text-foreground-soft focus-ring select-none",
          "transition-colors duration-(--duration) ease-(--ease) hover:text-foreground data-panel-open:text-foreground",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
        ],
        className,
      )}
      {...props}
    >
      {showIcon && (
        <ChevronRightIcon
          aria-hidden
          className="size-3.5 shrink-0 transition-[rotate] duration-(--duration) ease-(--ease) group-data-panel-open/collapsible-trigger:rotate-90"
        />
      )}
      {children}
    </BaseCollapsible.Trigger>
  );
}

/** The hidden content. Height animates from `--collapsible-panel-height`. */
export function CollapsiblePanel({ className, ...props }: BaseCollapsible.Panel.Props) {
  return (
    <BaseCollapsible.Panel
      data-slot="collapsible-panel"
      className={sx(
        "h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-(--duration) ease-(--ease) data-ending-style:h-0 data-starting-style:h-0",
        className,
      )}
      {...props}
    />
  );
}
