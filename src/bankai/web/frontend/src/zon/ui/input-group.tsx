import { Button as BaseButton } from "@base-ui/react/button";
import { Field as BaseField } from "@base-ui/react/field";
import { Input as BaseInput } from "@base-ui/react/input";
import type * as React from "react";
import { cn, sx } from "@/zon/lib/cn";

const groupSizes = {
  sm: "min-h-(--control-h-sm) text-[13px]",
  default: "min-h-(--control-h) text-base sm:text-[15px]",
} as const;

export interface InputGroupProps extends React.ComponentProps<"div"> {
  /** `sm` (30px) for dense surfaces; `default` (38px) everywhere else. */
  size?: keyof typeof groupSizes;
}

/**
 * InputGroup — one recessed field that holds an input (or textarea) together with addons:
 * an icon, a prefix like `https://`, a keyboard hint, a small button, or a toolbar under a
 * textarea. The group wears the field material (10px corners, 38px or 30px dense); the control
 * inside is borderless, and the group's border turns `--control-focus` while it has focus.
 *
 * Use it when the extra part belongs to the value: a search icon, a unit, a copy button. The
 * rule people break: putting a separate action (Submit, Save) inside — that is a `Button`
 * beside the field, or a `ButtonGroup`.
 */
export function InputGroup({ size = "default", className, ...props }: InputGroupProps) {
  return (
    <div
      role="group"
      data-slot="input-group"
      data-size={size}
      className={cn(
        "group/input-group relative flex w-full min-w-0 material-field items-center rounded-control font-body",
        groupSizes[size],
        // Focus on the inner control reads as one solid edge on the whole group.
        "has-[[data-slot=input-group-control]:focus-visible]:border-control-focus",
        "has-[[data-slot=input-group-control]:focus-visible]:outline-2 has-[[data-slot=input-group-control]:focus-visible]:outline-offset-0 has-[[data-slot=input-group-control]:focus-visible]:outline-control-focus",
        // Validation and disabled state come from the control (Field sets them there).
        "has-[[data-slot=input-group-control][aria-invalid=true]]:border-destructive has-[[data-slot=input-group-control][data-invalid]]:border-destructive",
        "has-[[data-slot=input-group-control][aria-invalid=true]]:shadow-invalid has-[[data-slot=input-group-control][data-invalid]]:shadow-invalid",
        "has-[[data-slot=input-group-control]:disabled]:cursor-not-allowed has-[[data-slot=input-group-control]:disabled]:opacity-55",
        // Block addons and textareas stack the group vertically.
        "has-[>[data-align=block-end]]:flex-col has-[>[data-align=block-start]]:flex-col has-[>textarea]:flex-col",
        "has-[>[data-align=block-end]]:items-stretch has-[>[data-align=block-start]]:items-stretch has-[>textarea]:items-stretch",
        "has-[>textarea]:min-h-[140px]",
        className,
      )}
      {...props}
    />
  );
}

const controlBase =
  "min-w-0 flex-1 border-0 bg-transparent font-body text-foreground shadow-none outline-none placeholder:text-control-muted focus-visible:outline-none disabled:cursor-not-allowed";

export interface InputGroupInputProps extends Omit<BaseInput.Props, "size"> {}

/**
 * The borderless input inside an `InputGroup`. Works inside `<Field>` like `Input`: the label,
 * description and validation attach to it, and the group shows the state.
 */
export function InputGroupInput({ className, ...props }: InputGroupInputProps) {
  return (
    <BaseInput
      data-slot="input-group-control"
      className={sx(
        [
          controlBase,
          // Stretch to the group's height. No h-full: the group only has a min-height, and a
          // percentage height there both resolves to the text's height and blocks the stretch,
          // which left the text sitting at the top of the field.
          "self-stretch px-3.5 group-data-[size=sm]/input-group:px-2.5",
          // Tighten the padding next to an inline addon, which brings its own.
          "group-has-[>[data-align=inline-end]]/input-group:pr-2 group-has-[>[data-align=inline-start]]/input-group:pl-2",
        ],
        className,
      )}
      {...props}
    />
  );
}

export interface InputGroupTextareaProps extends Omit<BaseField.Control.Props, "render"> {}

/**
 * The borderless textarea inside an `InputGroup`. It has no resize handle: the handle would sit
 * on the toolbar's edge inside the group. Instead it grows with its content (`field-sizing:
 * content`, where supported) up to 240px, then scrolls. The group (with its toolbar) is at
 * least 140px tall, like `Textarea`.
 */
export function InputGroupTextarea({ className, ...props }: InputGroupTextareaProps) {
  return (
    <BaseField.Control
      data-slot="input-group-control"
      render={<textarea />}
      className={sx(
        [
          controlBase,
          "field-sizing-content max-h-60 min-h-24 w-full resize-none px-3.5 py-3 text-base sm:text-[15px]",
          "group-has-[>[data-align=block-end]]/input-group:pb-1.5 group-has-[>[data-align=block-start]]/input-group:pt-1.5",
        ],
        className,
      )}
      {...props}
    />
  );
}

const addonAlign = {
  "inline-start":
    "order-first pl-3.5 group-data-[size=sm]/input-group:pl-2.5 has-[>[data-slot=input-group-button]]:pl-[3px] group-data-[size=sm]/input-group:has-[>[data-slot=input-group-button]]:pl-0.5",
  "inline-end":
    "order-last pr-3.5 group-data-[size=sm]/input-group:pr-2.5 has-[>[data-slot=input-group-button]]:pr-[3px] group-data-[size=sm]/input-group:has-[>[data-slot=input-group-button]]:pr-0.5",
  "block-start": "order-first w-full justify-start px-3.5 pt-2.5 pb-1",
  "block-end": "order-last w-full justify-start px-2 pt-1 pb-2",
} as const;

export interface InputGroupAddonProps extends React.ComponentProps<"div"> {
  /**
   * Where the addon sits: `inline-start` / `inline-end` beside the control, `block-start` /
   * `block-end` above or below it (a header or toolbar, usually with a textarea).
   */
  align?: keyof typeof addonAlign;
}

/**
 * An addon inside an `InputGroup`: an icon, `InputGroupText`, a `Kbd`, or `InputGroupButton`s.
 * Clicking the addon's empty space focuses the control.
 */
export function InputGroupAddon({
  align = "inline-start",
  className,
  onMouseDown,
  ...props
}: InputGroupAddonProps) {
  return (
    <div
      data-slot="input-group-addon"
      data-align={align}
      className={cn(
        "flex h-auto shrink-0 cursor-text items-center gap-2 text-muted-foreground select-none",
        "[&>svg]:shrink-0 [&>svg:not([class*='size-'])]:size-4",
        "group-has-[[data-slot=input-group-control]:disabled]/input-group:cursor-not-allowed",
        addonAlign[align],
        className,
      )}
      onMouseDown={(event) => {
        onMouseDown?.(event);
        if (event.defaultPrevented) return;
        // Clicks on the addon's padding or icon focus the control, as on a native field — not
        // clicks inside a popup it opened, which reach here through React's portal.
        if (!event.currentTarget.contains(event.target as Node)) return;
        if ((event.target as HTMLElement).closest("button, a, input, select, textarea")) return;
        const control = event.currentTarget.parentElement?.querySelector<HTMLElement>(
          "[data-slot=input-group-control]",
        );
        if (control) {
          event.preventDefault();
          control.focus();
        }
      }}
      {...props}
    />
  );
}

/** Plain text inside an addon: a prefix, a suffix or a count. Licht Mono, muted, tabular. */
export function InputGroupText({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="input-group-text"
      className={cn(
        "flex items-center gap-1.5 font-mono text-[13px] whitespace-nowrap text-muted-foreground tabular-nums",
        "group-data-[size=sm]/input-group:text-xs [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}

const buttonVariants = {
  secondary: "material-raised",
  ghost: "material-row text-muted-foreground",
  primary: "material-primary",
} as const;

const buttonSizes = {
  /** 24px — fits the dense group, or a quiet action in the default one. */
  sm: "min-h-(--control-inset-sm) gap-1 rounded-(--input-group-button-radius) px-2 text-xs",
  /** 30px — fits the 38px group with a 3px inset, like a segment. */
  default: "min-h-(--control-inset) gap-1.5 rounded-(--input-group-button-radius) px-2.5 text-[13px]",
  "icon-sm": "size-(--control-inset-sm) rounded-full p-0 [&_svg:not([class*='size-'])]:size-3.5",
  icon: "size-(--control-inset) rounded-full p-0",
} as const;

export interface InputGroupButtonProps extends BaseButton.Props {
  /** `secondary` (raised, default), `ghost` (flat until hovered) or `primary` (the one action, e.g. Send). */
  variant?: keyof typeof buttonVariants;
  /** 30px (`default`, `icon`) fits the 38px group; 24px (`sm`, `icon-sm`) fits the dense one. */
  size?: keyof typeof buttonSizes;
}

/**
 * A small button sized to sit inside the group: 30px in the 38px field (a 3px inset, like the
 * NumberField steppers), 24px in the dense one. Its corners are concentric with the field's:
 * outer radius − inset = inner radius, so 7px in the default group and 8px in the dense one
 * (see `--input-group-button-radius` in controls.css). Icon-only sizes are round and need
 * `aria-label`.
 */
export function InputGroupButton({
  variant = "secondary",
  size = "default",
  className,
  ...props
}: InputGroupButtonProps) {
  return (
    <BaseButton
      data-slot="input-group-button"
      data-variant={variant}
      data-size={size}
      className={sx(
        [
          "inline-flex shrink-0 cursor-pointer items-center justify-center font-body leading-none font-medium whitespace-nowrap focus-ring select-none",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          buttonVariants[variant],
          buttonSizes[size],
        ],
        className,
      )}
      {...props}
    />
  );
}
