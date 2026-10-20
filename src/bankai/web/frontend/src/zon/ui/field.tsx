import { Field as BaseField } from "@base-ui/react/field";
import { Fieldset as BaseFieldset } from "@base-ui/react/fieldset";
import { sx } from "@/zon/lib/cn";

/**
 * Field — groups a label, a control, a description and validation.
 * Every control needs a visible label. Errors use destructive ink *plus* a message.
 */
export function Field({ className, ...props }: BaseField.Root.Props) {
  return <BaseField.Root data-slot="field" className={sx("flex flex-col gap-2", className)} {...props} />;
}

export function FieldLabel({ className, ...props }: BaseField.Label.Props) {
  return (
    <BaseField.Label
      data-slot="field-label"
      className={sx(
        [
          "text-sm leading-snug font-medium text-foreground select-none data-disabled:opacity-55",
          // A label wrapping a checkbox, radio or switch lays the control out beside its text.
          "has-[[data-slot=checkbox],[data-slot=radio]]:flex has-[[data-slot=checkbox],[data-slot=radio]]:cursor-pointer has-[[data-slot=checkbox],[data-slot=radio]]:items-start has-[[data-slot=checkbox],[data-slot=radio]]:gap-2.5 has-[[data-slot=checkbox],[data-slot=radio]]:font-normal",
          "has-data-[slot=switch]:flex has-data-[slot=switch]:cursor-pointer has-data-[slot=switch]:items-center has-data-[slot=switch]:gap-3 has-data-[slot=switch]:font-normal",
        ],
        className,
      )}
      {...props}
    />
  );
}

export function FieldDescription({ className, ...props }: BaseField.Description.Props) {
  return (
    <BaseField.Description
      data-slot="field-description"
      className={sx("text-[13px] leading-normal text-muted-foreground", className)}
      {...props}
    />
  );
}

export function FieldError({ className, ...props }: BaseField.Error.Props) {
  return (
    <BaseField.Error
      data-slot="field-error"
      className={sx("text-[13px] leading-normal text-destructive", className)}
      {...props}
    />
  );
}

/** A labelled group of fields (radio groups, checkbox groups, address blocks). */
export function Fieldset({ className, ...props }: BaseFieldset.Root.Props) {
  return (
    <BaseFieldset.Root
      data-slot="fieldset"
      className={sx("flex flex-col gap-4 border-0 p-0", className)}
      {...props}
    />
  );
}

export function FieldsetLegend({ className, ...props }: BaseFieldset.Legend.Props) {
  return (
    <BaseFieldset.Legend data-slot="fieldset-legend" className={sx("mb-1 eyebrow", className)} {...props} />
  );
}

export const FieldItem = BaseField.Item;
export const FieldValidity = BaseField.Validity;
