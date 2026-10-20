import { Input as BaseInput } from "@base-ui/react/input";
import { sx } from "@/zon/lib/cn";

const sizes = {
  sm: "min-h-(--control-h-sm) px-2.5 text-[13px]",
  default: "min-h-(--control-h) px-3.5 text-base sm:text-[15px]",
} as const;

export interface InputProps extends Omit<BaseInput.Props, "size"> {
  /** `sm` (30px) for dense surfaces; `default` (38px) everywhere else. */
  size?: keyof typeof sizes;
}

/**
 * Input — a recessed single-line field. 10px radius, 38px default, 30px dense, full width of
 * its column. Text stays ≥16px on mobile to avoid iOS zoom. Place inside `<Field>` for label +
 * validation. For more than a sentence, use `Textarea`.
 */
export function Input({ size = "default", className, ...props }: InputProps) {
  return (
    <BaseInput
      data-slot="input"
      data-size={size}
      className={sx(
        [
          "w-full min-w-0 material-field rounded-control font-body outline-none",
          "file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium",
          sizes[size],
        ],
        className,
      )}
      {...props}
    />
  );
}
