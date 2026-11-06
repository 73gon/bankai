import { Input as BaseInput } from "@base-ui/react/input";
import { sx } from "@/zon/lib/cn";

const sizes = {
  sm: "min-h-(--control-h-sm) px-2.5 text-[13px] [&[type=file]]:p-0.5 file:h-6",
  default:
    "min-h-(--control-h) px-3.5 text-base sm:text-[15px] [&[type=file]]:p-[3px] file:h-(--control-h-sm)",
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
          // A file input's "Choose file" is a small raised button inside the field, inset like an
          // InputGroup button (30px in 38, 24px in 30, 7px corners); the file name stays quiet.
          "[&[type=file]]:cursor-pointer [&[type=file]]:text-muted-foreground",
          "file:mr-3 file:cursor-pointer file:rounded-item file:border-0 file:bg-control-surface file:bg-linear-to-b file:from-control-inner-edge file:to-transparent file:px-3 file:font-body file:text-[13px] file:font-medium file:text-foreground file:shadow-raised file:hover:bg-control-hover",
          // The search field's clear button (×) is a button: it takes the pointer.
          "[&::-webkit-search-cancel-button]:cursor-pointer",
          sizes[size],
        ],
        className,
      )}
      {...props}
    />
  );
}
