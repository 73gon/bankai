import { Button as BaseButton } from "@base-ui/react/button";
import { sx } from "@/zon/lib/cn";

const variants = {
  /** The layered primary. One per view — the action that carries the page. */
  primary: "material-primary",
  /** Raised quiet surface. Every other action. */
  secondary: "material-raised",
  /** Flat until hovered, then raised. Toolbars and dense rows. */
  ghost: "material-row",
  /** Inline text action with the brand underline. */
  link: "link h-auto min-h-0 rounded-none px-0 font-normal",
  /** Raised surface with destructive ink. Use only for irreversible actions. */
  destructive: "material-raised text-destructive",
} as const;

const sizes = {
  sm: "min-h-(--control-h-sm) gap-1.5 px-3 text-[13px]",
  default: "min-h-(--control-h) gap-2 px-4 text-sm",
  lg: "min-h-(--control-h-lg) gap-2 px-5 text-[15px]",
  icon: "size-(--icon-button) rounded-full p-0",
  "icon-sm": "size-(--control-h-sm) rounded-full p-0",
} as const;

export type ButtonVariant = keyof typeof variants;
export type ButtonSize = keyof typeof sizes;

export interface ButtonProps extends BaseButton.Props {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

/**
 * Button — Base UI `Button` in the zon material.
 *
 * Heights come from `min-height`, never padding: 30 dense · 38 default · 46 prominent.
 * Icon sizes are circular and must have an `aria-label`.
 * Use `render={<a href="…" />}` with `nativeButton={false}` for links styled as buttons.
 */
export function Button({ variant = "primary", size = "default", className, ...props }: ButtonProps) {
  return (
    <BaseButton
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={sx(
        [
          "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-button font-body leading-none font-medium whitespace-nowrap focus-ring select-none",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          sizes[size],
          variants[variant],
        ],
        className,
      )}
      {...props}
    />
  );
}
