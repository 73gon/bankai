import { Toggle as BaseToggle } from "@base-ui/react/toggle";
import { sx } from "@/zon/lib/cn";

const sizes = {
  sm: "min-h-(--control-h-sm) gap-1.5 px-3 text-[13px]",
  default: "min-h-(--control-h) gap-2 px-4 text-sm",
  icon: "size-(--icon-button) rounded-full p-0",
  "icon-sm": "size-(--control-h-sm) rounded-full p-0",
} as const;

export type ToggleSize = keyof typeof sizes;

export interface ToggleProps<Value extends string = string> extends BaseToggle.Props<Value> {
  size?: ToggleSize;
}

/**
 * Toggle — a raised button that stays pressed. Off, it is the quiet raised surface with soft
 * ink, and hover only brightens the ink. On, it sinks into the surface: the hover fill, a deeper inset shadow, the
 * `--control-focus` edge of a current item and full foreground ink. Never a colour fill or
 * layout movement.
 *
 * Use it for a single on/off tool ("Show grid", bold, pin). For a choice between options use
 * `ToggleGroup`; for a form value use `Checkbox` or `Switch`. Icon sizes are circular and need
 * an `aria-label` — and keep that label constant; the pressed state is announced separately.
 */
export function Toggle<Value extends string = string>({
  size = "default",
  className,
  ...props
}: ToggleProps<Value>) {
  return (
    <BaseToggle<Value>
      data-slot="toggle"
      data-size={size}
      className={sx(
        [
          // The raised material, written out: the shared utility fills on hover, which would
          // read as "on". Hover here only brightens the ink; the pressed inset lives in controls.css.
          "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-button border border-transparent bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent font-body leading-none font-medium whitespace-nowrap shadow-raised focus-ring select-none",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          // Off is the quiet raised surface with soft ink; on sinks in behind the focus-grey edge.
          "text-foreground-soft hover:text-foreground",
          "data-pressed:bg-control-hover data-pressed:text-foreground data-pressed:shadow-(--toggle-shadow-pressed)",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          sizes[size],
        ],
        className,
      )}
      {...props}
    />
  );
}
