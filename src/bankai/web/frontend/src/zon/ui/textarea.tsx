import { Field as BaseField } from "@base-ui/react/field";
import { sx } from "@/zon/lib/cn";

export interface TextareaProps extends Omit<BaseField.Control.Props, "render"> {}

/**
 * Textarea — a recessed multi-line field: the field material, 10px corners, at least 140px
 * tall, full width of its column, and resizable vertically with the handle in its corner.
 * Text stays ≥16px on mobile to avoid iOS zoom. Place inside `<Field>` for label + validation.
 *
 * Use it for anything longer than a sentence. The rule people break: setting `rows` or a
 * fixed height to make it look tidy — keep the 140px minimum and let people drag it taller.
 * For a textarea with a toolbar (a count, a Send button), use `InputGroupTextarea`.
 */
export function Textarea({ className, ...props }: TextareaProps) {
  return (
    <BaseField.Control
      data-slot="textarea"
      render={<textarea />}
      className={sx(
        "min-h-[140px] w-full material-field resize-y rounded-control px-3.5 py-3 font-body text-base outline-none sm:text-[15px]",
        className,
      )}
      {...props}
    />
  );
}
