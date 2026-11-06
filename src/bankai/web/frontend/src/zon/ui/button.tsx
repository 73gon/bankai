import { Button as BaseButton } from "@base-ui/react/button";
import * as React from "react";
import { sx } from "@/zon/lib/cn";
import { useCollapsible } from "@/zon/lib/collapse-row";
import { mergeRefs } from "@/zon/lib/merge-refs";

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

/*
 * A collapsible button carries the icon–label gap inside its label (--button-gap), so nothing
 * of it is left once the label folds away. Folded, its padding and corners make a circle as
 * tall as the button (parts/controls.css), moving on the material's --material-motion slot.
 */
const collapsibleGaps = {
  sm: "[--button-gap:--spacing(1.5)]",
  default: "[--button-gap:--spacing(2)]",
  lg: "[--button-gap:--spacing(2)]",
} as const;

export type ButtonVariant = keyof typeof variants;
export type ButtonSize = keyof typeof sizes;

export interface ButtonProps extends BaseButton.Props {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Fold the label away to leave only the icon when the button's row runs out of room, and
   * unfold it when there is room again — animated, the last collapsible button in the row
   * first. The row is the nearest ancestor with `data-collapse-row`, or the button's parent.
   * Give it an icon and a text label; the label stays its accessible name. Not for icon sizes.
   */
  collapsible?: boolean;
  /**
   * The opposite of `collapsible`, played by the pointer: what opens on hover or keyboard focus.
   * `"label"` (or `true`) rests as a round icon button and shows the label beside the icon;
   * `"icon"` rests as text and slides the icon in — before the text or after it, as you place it.
   * On a touch screen it stays as it rests; the label is always its accessible name. It takes
   * room as it opens, so give it some: the end of a row. Not for icon sizes.
   */
  expandable?: boolean | "label" | "icon";
}

/**
 * For `expandable="icon"`: icons (and other elements) go into a column that opens on hover; the
 * text stays as it is.
 */
function withIcon(children: React.ReactNode) {
  const parts = React.Children.toArray(children);
  const firstText = parts.findIndex((part) => typeof part === "string" || typeof part === "number");
  return parts.map((part, index) =>
    typeof part === "string" || typeof part === "number" ? (
      part
    ) : (
      <span
        key={index}
        data-slot="button-icon"
        data-before-label={firstText === -1 || index < firstText ? "" : undefined}
      >
        <span>{part}</span>
      </span>
    ),
  );
}

/** Text children become the label that folds away; icons and other elements stay as they are. */
function withLabel(children: React.ReactNode) {
  const parts = React.Children.toArray(children);
  const iconFirst =
    parts.findIndex((part) => React.isValidElement(part)) <
    parts.findIndex((part) => typeof part === "string" || typeof part === "number");
  return parts.map((part, index) =>
    typeof part === "string" || typeof part === "number" ? (
      <span key={index} data-slot="button-label" data-after-icon={iconFirst ? "" : undefined}>
        <span>{part}</span>
      </span>
    ) : (
      part
    ),
  );
}

/**
 * Button — Base UI `Button` in the zon material.
 *
 * Heights come from `min-height`, never padding: 30 dense · 38 default · 46 prominent.
 * Icon sizes are circular and must have an `aria-label`. `collapsible` lets a button with an
 * icon and a label fold to a round icon button when its row is too tight; `expandable` rests
 * as that icon button and unfolds on hover or focus.
 * Use `render={<a href="…" />}` with `nativeButton={false}` for links styled as buttons.
 */
export function Button({
  variant = "primary",
  size = "default",
  collapsible = false,
  expandable = false,
  className,
  children,
  ref,
  ...props
}: ButtonProps) {
  const own = React.useRef<HTMLElement>(null);
  const foldable = size !== "icon" && size !== "icon-sm";
  const expands = !foldable || !expandable ? null : expandable === "icon" ? "icon" : "label";
  const canExpand = expands === "label";
  const canCollapse = collapsible && foldable && !expands;
  const { collapsed, ready } = useCollapsible(own, canCollapse);
  // An expandable button rests folded; hover and focus unfold it in CSS (parts/controls.css).
  const folds = canCollapse || canExpand || expands === "icon";
  const refs = React.useMemo(() => mergeRefs(own as React.Ref<HTMLButtonElement>, ref), [ref]);
  return (
    <BaseButton
      data-slot="button"
      data-variant={variant}
      data-size={size}
      data-collapsible={canCollapse ? "" : undefined}
      data-collapsed={collapsed || canExpand ? "" : undefined}
      data-expandable={expands ?? undefined}
      data-collapse-instant={canCollapse && !ready ? "" : undefined}
      ref={refs}
      className={sx(
        [
          "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-button font-body leading-none font-medium whitespace-nowrap focus-ring select-none",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          sizes[size],
          folds && [
            "gap-0 data-collapse-instant:transition-none!",
            // The fold's padding and corners; an expandable button waits out its hover intent first.
            "[--material-motion:padding_calc(var(--duration)*2)_var(--ease)_var(--expand-delay,0s),border-radius_calc(var(--duration)*2)_var(--ease)_var(--expand-delay,0s)]",
            collapsibleGaps[size as keyof typeof collapsibleGaps],
          ],
          variants[variant],
        ],
        className,
      )}
      {...props}
    >
      {expands === "icon" ? withIcon(children) : folds ? withLabel(children) : children}
    </BaseButton>
  );
}
