import { Field as BaseField } from "@base-ui/react/field";
import { ChevronsUpDownIcon } from "lucide-react";
import type * as React from "react";
import { cn } from "@/zon/lib/cn";

const sizes = {
  sm: "min-h-(--control-h-sm) pr-8 pl-2.5 text-[13px]",
  default: "min-h-(--control-h) pr-10 pl-3.5 text-base sm:text-[15px]",
} as const;

export interface NativeSelectProps extends Omit<React.ComponentProps<"select">, "size"> {
  /** `sm` (30px) for dense surfaces; `default` (38px) everywhere else. */
  size?: keyof typeof sizes;
  /** Classes for the wrapper that positions the chevron (width, margins, grid placement). */
  wrapperClassName?: string;
}

/**
 * NativeSelect — a real `<select>` in the field material: recessed, 10px corners, 38px (30px
 * dense), with the same two-way chevron as `Select`. The browser draws the option list, so it
 * is the platform's own picker on phones and works before JavaScript loads.
 *
 * Prefer it for plain forms, long lists (countries, years) and mobile-first screens. Reach for
 * `Select` when the options need custom rendering, groups with rich labels or a styled popup.
 * The rule people break: styling `<option>` — browsers ignore most of it; keep options as text.
 */
export function NativeSelect({
  size = "default",
  className,
  wrapperClassName,
  children,
  ...props
}: NativeSelectProps) {
  return (
    <div
      data-slot="native-select-wrapper"
      data-size={size}
      className={cn("group/native-select relative inline-flex w-full min-w-0", wrapperClassName)}
    >
      <BaseField.Control
        data-slot="native-select"
        data-size={size}
        render={<select />}
        className={cn(
          "w-full min-w-0 material-field cursor-pointer appearance-none rounded-control font-body leading-none text-foreground outline-none",
          "disabled:cursor-not-allowed",
          // An empty first option is the placeholder: show it muted while it is chosen.
          "has-[option[value='']:checked]:text-control-muted",
          // The listbox the browser draws follows the theme.
          "[&_optgroup]:bg-field-surface [&_option]:bg-field-surface [&_option]:text-foreground",
          sizes[size],
          className,
        )}
        {...(props as Omit<BaseField.Control.Props, "render" | "className">)}
      >
        {children}
      </BaseField.Control>
      <ChevronsUpDownIcon
        aria-hidden
        data-slot="native-select-icon"
        className={cn(
          "pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground transition-colors duration-(--duration) ease-brand",
          "group-has-[select:disabled]/native-select:opacity-55 group-has-[select:enabled:hover]/native-select:text-foreground",
          size === "sm" ? "right-2 size-3.5" : "right-3 size-4",
        )}
      />
    </div>
  );
}

/** One `<option>`. Plain text only: browsers ignore most styling on options. */
export function NativeSelectOption(props: React.ComponentProps<"option">) {
  return <option data-slot="native-select-option" {...props} />;
}

/** A labelled group of options (`<optgroup>`). */
export function NativeSelectOptGroup(props: React.ComponentProps<"optgroup">) {
  return <optgroup data-slot="native-select-optgroup" {...props} />;
}
