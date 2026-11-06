import { Button as BaseButton } from "@base-ui/react/button";
import { Field as BaseField } from "@base-ui/react/field";
import { Input as BaseInput } from "@base-ui/react/input";
import { XIcon } from "lucide-react";
import * as React from "react";
import { cn, sx } from "@/zon/lib/cn";
import { mergeRefs } from "@/zon/lib/merge-refs";
import { cssDuration } from "@/zon/lib/motion";

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
 * inside is borderless, and like any text field the group takes a softer edge on focus, no ring.
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

export interface InputGroupPasswordToggleProps extends Omit<
  InputGroupButtonProps,
  "variant" | "size" | "children"
> {
  /** The password is shown (the input is `type="text"`). */
  visible: boolean;
  onVisibleChange: (visible: boolean) => void;
  /** The button's names for each state. Default: "Show password" / "Hide password". */
  labels?: { show?: string; hide?: string };
}

/*
 * The reveal sweeps: on a press, the field's new text — or its dots — is uncovered left to right
 * behind a soft 32px edge, across the text itself rather than the whole field, on 2.2 × --duration
 * with the press's ease-out (the brand ease spends most of its way in a frame or two, too soon to
 * see). The value is there at once and the field stays usable, so revealing a password many times
 * a day never means waiting. Only a press of the toggle plays it; reduced motion skips it.
 */
let measurer: CanvasRenderingContext2D | null = null;

/** Where the field's text runs, in the input's own pixels: from its left padding to its end. */
function textSpan(input: HTMLInputElement) {
  const style = getComputedStyle(input);
  measurer ??= document.createElement("canvas").getContext("2d");
  const text = input.type === "password" ? "•".repeat(input.value.length) : input.value;
  let width = 0;
  if (measurer) {
    measurer.font = style.font;
    measurer.letterSpacing = style.letterSpacing;
    width = measurer.measureText(text).width;
  }
  const left = parseFloat(style.paddingLeft) - input.scrollLeft;
  return { left, right: Math.min(input.clientWidth, left + width), width: input.clientWidth };
}

/**
 * A mask twice the field's width with a soft edge at its middle, slid so the edge travels from
 * just before the text's start to just past its end — or back, to erase it (`out`).
 */
function sweepFrames(input: HTMLInputElement, out = false): Keyframe[] {
  const { left, right, width } = textSpan(input);
  const soft = 16;
  const mask = {
    maskImage: `linear-gradient(90deg, #000 calc(50% - ${soft}px), transparent calc(50% + ${soft}px))`,
    maskSize: `${width * 2}px 100%`,
    maskRepeat: "no-repeat",
  };
  const frames = [
    { ...mask, maskPosition: `${left - soft - width}px 0` },
    { ...mask, maskPosition: `${right + soft - width}px 0` },
  ];
  return out ? frames.reverse() : frames;
}

/** The input this part shares an InputGroup with. */
const groupInput = (el: Element | null) =>
  el
    ?.closest('[data-slot="input-group"]')
    ?.querySelector<HTMLInputElement>('input[data-slot="input-group-control"]') ?? null;

/**
 * InputGroupPasswordToggle — the show/hide button for a password: a ghost icon button with an
 * eye whose slash draws across it when the password is shown and draws back when it is hidden,
 * the eye opening a gap along the slash as it passes, while the field's text sweeps into its
 * new form behind a soft edge. It names what it will do and carries `aria-pressed`. Switch the input's `type` with
 * `visible`.
 */
export function InputGroupPasswordToggle({
  visible,
  onVisibleChange,
  labels,
  ref,
  onClick,
  ...props
}: InputGroupPasswordToggleProps) {
  const own = React.useRef<HTMLButtonElement>(null);
  const pressed = React.useRef(false);
  const refs = React.useMemo(() => mergeRefs(own, ref), [ref]);

  // After the press re-renders the input with its new type — and before that is painted, so the
  // first frame seen is the animation's first frame, not the finished text — its text arrives.
  React.useLayoutEffect(() => {
    if (!pressed.current) return;
    pressed.current = false;
    const input = groupInput(own.current);
    if (!input) return;
    const duration = cssDuration(input) * 2.2;
    if (!duration) return;
    input.animate(sweepFrames(input), { duration, easing: "cubic-bezier(0.33, 1, 0.68, 1)" });
  }, [visible]);

  return (
    <InputGroupButton
      ref={refs}
      // Still an input-group-button (the addon insets it 3px, like any button), marked as the toggle.
      data-password-toggle=""
      variant="ghost"
      size="icon"
      aria-label={visible ? (labels?.hide ?? "Hide password") : (labels?.show ?? "Show password")}
      aria-pressed={visible}
      onClick={(event) => {
        pressed.current = true;
        onVisibleChange(!visible);
        onClick?.(event);
      }}
      {...props}
    >
      <EyeSlashIcon slashed={visible} />
    </InputGroupButton>
  );
}

/*
 * The eye from lucide, with its slash drawn rather than swapped: the slash and the gap it cuts in
 * the eye (a mask stroke along the same line) share one dash, from just past 1 (not drawn) to 0
 * (drawn), on 1.4 × --duration like the text's settle — instant under reduced motion.
 */
const slash = "m2 2 20 20";
const drawn = "transition-[stroke-dashoffset] duration-[calc(var(--duration)*1.4)] ease-(--ease)";

function EyeSlashIcon({ slashed }: { slashed: boolean }) {
  const mask = React.useId().replace(/[^a-zA-Z0-9]/g, "");
  // Hidden, the dash sits just before the line's start, so not even a round cap shows.
  const offset = slashed ? 0 : 1.02;
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      data-slashed={slashed ? "" : undefined}
    >
      <mask id={mask} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="white" />
        <path
          d={slash}
          stroke="black"
          strokeWidth={5}
          pathLength={1}
          strokeDasharray="1 2"
          strokeDashoffset={offset}
          className={drawn}
        />
      </mask>
      <g mask={`url(#${mask})`}>
        <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
        <circle cx="12" cy="12" r="3" />
      </g>
      <path d={slash} pathLength={1} strokeDasharray="1 2" strokeDashoffset={offset} className={drawn} />
    </svg>
  );
}

export interface InputGroupClearButtonProps extends Omit<
  InputGroupButtonProps,
  "variant" | "size" | "children" | "onClick"
> {
  /**
   * Clear your state (`setQuery("")`). Without it, the button empties the field itself and fires
   * its change event, for an uncontrolled input.
   */
  onClear?: () => void;
  /** The button's name. Default "Clear". Say what it clears: "Clear search". */
  label?: string;
}

/**
 * InputGroupClearButton — empties the field it sits in, smoothly: the text is erased right to
 * left behind a soft edge, as if typed away, then the value clears and focus returns to the
 * field. The button fades out while the field is empty and back in when there is text, instead of
 * popping in and out. Reduced motion clears at once.
 */
export function InputGroupClearButton({
  onClear,
  label = "Clear",
  ref,
  className,
  ...props
}: InputGroupClearButtonProps) {
  const own = React.useRef<HTMLButtonElement>(null);
  const refs = React.useMemo(() => mergeRefs(own, ref), [ref]);
  const [empty, setEmpty] = React.useState(true);
  const clearing = React.useRef(false);

  // Whether there is anything to clear: after every render (a controlled value) and on input.
  React.useLayoutEffect(() => {
    const input = groupInput(own.current);
    if (input && !clearing.current) setEmpty(input.value === "");
  });
  React.useEffect(() => {
    const input = groupInput(own.current);
    if (!input) return;
    const sync = () => {
      if (!clearing.current) setEmpty(input.value === "");
    };
    input.addEventListener("input", sync);
    return () => input.removeEventListener("input", sync);
  }, []);

  const clear = () => {
    const input = groupInput(own.current);
    if (!input || clearing.current) return;
    const done = () => {
      if (onClear) onClear();
      else {
        // An uncontrolled input: set its value as a person would, so React and Base UI hear it.
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      input.focus();
    };
    const duration = cssDuration(input) * 1.6;
    if (!duration || input.value === "") {
      done();
      return;
    }
    clearing.current = true;
    setEmpty(true);
    // Held at its end until the value is really gone, so the text never flashes back.
    const erase = input.animate(sweepFrames(input, true), {
      duration,
      easing: "cubic-bezier(0.33, 1, 0.68, 1)",
      fill: "forwards",
    });
    erase.onfinish = () => {
      done();
      const release = () => {
        if (input.value !== "" && input.isConnected) return void requestAnimationFrame(release);
        erase.cancel();
        clearing.current = false;
      };
      release();
    };
  };

  return (
    <InputGroupButton
      ref={refs}
      data-clear-button=""
      data-empty={empty ? "" : undefined}
      variant="ghost"
      size="icon"
      aria-label={label}
      aria-hidden={empty || undefined}
      tabIndex={empty ? -1 : undefined}
      onClick={clear}
      className={cn(
        // The fade rides the material's own transition list (--material-motion), which a transition
        // utility would replace.
        "[--material-motion:opacity_var(--duration)_var(--ease),scale_var(--duration)_var(--ease)] data-empty:pointer-events-none data-empty:scale-75 data-empty:opacity-0",
        className,
      )}
      {...props}
    >
      <XIcon />
    </InputGroupButton>
  );
}
