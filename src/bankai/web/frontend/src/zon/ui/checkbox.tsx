import { Checkbox as BaseCheckbox } from "@base-ui/react/checkbox";
import { CheckboxGroup as BaseCheckboxGroup } from "@base-ui/react/checkbox-group";
import { CheckIcon, MinusIcon } from "lucide-react";
import { sx } from "@/zon/lib/cn";

export interface CheckboxProps extends BaseCheckbox.Root.Props {}

/**
 * Checkbox — an 18 × 18px box with 5px corners. Recessed like a field while unticked; ticked
 * (or indeterminate) it takes the primary fill with a fine edge and soft top/bottom shading.
 * Ticking draws the check in; unticking draws it back out (parts/controls.css).
 *
 * Use it for independent on/off choices and consent. Wrap it in `<FieldLabel>` (inside a
 * `<Field>`) so the label text is clickable. The rule people break: a checkbox with no visible
 * label — every checkbox needs readable text beside it, not just an `aria-label`.
 */
export function Checkbox({ className, ...props }: CheckboxProps) {
  return (
    <BaseCheckbox.Root
      data-slot="checkbox"
      className={sx(
        [
          "group/checkbox relative inline-flex size-[18px] shrink-0 material-field cursor-pointer items-center justify-center rounded-checkbox p-0",
          "data-checked:border-transparent data-checked:bg-primary data-checked:bg-none data-checked:text-primary-foreground data-checked:shadow-checked",
          "data-indeterminate:border-transparent data-indeterminate:bg-primary data-indeterminate:bg-none data-indeterminate:text-primary-foreground data-indeterminate:shadow-checked",
          "data-readonly:cursor-default",
        ],
        className,
      )}
      {...props}
    >
      <BaseCheckbox.Indicator
        data-slot="checkbox-indicator"
        // Its own ink: unticking drops the box's text colour at once, and the check must stay
        // visible on the still-dark fill while it undraws.
        className="pointer-events-none flex items-center justify-center text-primary-foreground"
      >
        <CheckIcon className="size-3.5 group-data-indeterminate/checkbox:hidden" strokeWidth={3} />
        <MinusIcon className="hidden size-3.5 group-data-indeterminate/checkbox:block" strokeWidth={3} />
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  );
}

export interface CheckboxGroupProps extends BaseCheckboxGroup.Props {}

/**
 * CheckboxGroup — shared state for a list of checkboxes; its value is the array of ticked
 * `value`s. Render it through `<Fieldset render={<CheckboxGroup />}>` inside a `<Field name>`
 * so the group gets a legend and joins form validation. Each checkbox needs its own `value`.
 */
export function CheckboxGroup({ className, ...props }: CheckboxGroupProps) {
  return (
    <BaseCheckboxGroup
      data-slot="checkbox-group"
      className={sx("flex flex-col items-start gap-3", className)}
      {...props}
    />
  );
}
