import { useRender } from "@base-ui/react/use-render";
import { cn } from "@/zon/lib/cn";

export type LabelProps = useRender.ComponentProps<"label">;

/**
 * Label — a standalone `<label>` for a control that lives outside a `Field`: a switch in a
 * toolbar, a checkbox in a table header, a native input in a third-party widget.
 *
 * Licht Sans 14px medium in foreground. It dims to 55% when the control it labels is disabled —
 * either a following `peer` control, a control nested inside it, or a `group` marked
 * `data-disabled`. The rule people break: using it inside a form. There, use `FieldLabel`,
 * which wires the label, description and error to the control for you.
 */
export function Label({ render, ref, className, ...props }: LabelProps) {
  return useRender({
    defaultTagName: "label",
    render,
    ref,
    props: {
      "data-slot": "label",
      ...props,
      className: cn(
        "inline-flex items-center gap-2.5 text-sm leading-snug font-medium text-foreground select-none",
        "peer-disabled:cursor-not-allowed peer-disabled:opacity-55 peer-data-disabled:cursor-not-allowed peer-data-disabled:opacity-55",
        "group-data-disabled:pointer-events-none group-data-disabled:opacity-55",
        "has-disabled:cursor-not-allowed has-disabled:opacity-55 has-data-disabled:cursor-not-allowed has-data-disabled:opacity-55",
        className,
      ),
    },
  });
}
