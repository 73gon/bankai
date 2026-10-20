import { NumberField as BaseNumberField } from "@base-ui/react/number-field";
import { MinusIcon, MoveHorizontalIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { cn, sx } from "@/zon/lib/cn";

const groupSizes = {
  sm: "min-h-(--control-h-sm) p-0.5",
  default: "min-h-(--control-h) p-[3px]",
} as const;

const stepperSizes = {
  sm: "size-(--control-inset-sm) material-row",
  default: "size-(--control-inset) material-raised",
} as const;

export interface NumberFieldProps extends BaseNumberField.Root.Props {
  /** `sm` (30px) for dense surfaces; `default` (38px) everywhere else. */
  size?: keyof typeof groupSizes;
  /**
   * A visible label rendered as a scrub area: drag across it horizontally to change the value.
   * Omit it when the field already sits under a `<FieldLabel>`.
   */
  label?: React.ReactNode;
  /** Placeholder for the input while it is empty. */
  placeholder?: string;
  /** Accessible names for the steppers. */
  decrementLabel?: string;
  incrementLabel?: string;
}

/** Keys that step the value without editing the text: they leave a hidden caret hidden. */
const STEP_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
  "Shift",
  "Alt",
  "Control",
  "Meta",
  "Tab",
  "Escape",
]);

/** Changes that step the value — these roll the digits; typing, pasting and blur formatting don't. */
const STEP_REASONS = new Set<string>(["increment-press", "decrement-press", "keyboard", "wheel"]);

type Roll = { from: string; to: string; dir: 1 | -1; key: number };

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * The digit roll drawn over the input while a step animates (aria-hidden; the input underneath
 * keeps the value, the caret and the accessible name). The two values are lined up from the right,
 * so units, tens and hundreds meet their own places; only characters that differ move.
 */
function DigitRoll({ roll, className, onDone }: { roll: Roll; className: string; onDone: () => void }) {
  const width = Math.max(roll.from.length, roll.to.length);
  const from = roll.from.padStart(width, " ");
  const to = roll.to.padStart(width, " ");
  React.useEffect(() => {
    // A fallback for when no animation runs (or its end event is missed).
    const t = setTimeout(onDone, 1000);
    return () => clearTimeout(t);
  }, [onDone]);
  return (
    <span
      aria-hidden
      data-slot="number-field-roll"
      className={cn(
        "pointer-events-none absolute inset-0 flex items-center justify-center overflow-hidden font-mono whitespace-pre text-foreground tabular-nums",
        className,
      )}
      style={{ "--roll-dir": roll.dir } as React.CSSProperties}
      onAnimationEnd={(e) => {
        if ((e.target as HTMLElement).hasAttribute("data-new")) onDone();
      }}
    >
      {[...to].map((next, i) => {
        const prev = from[i]!;
        if (prev === next) return <span key={i}>{next}</span>;
        return (
          <span
            key={i}
            data-roll=""
            data-grow={prev === " " ? "" : undefined}
            data-shrink={next === " " ? "" : undefined}
          >
            <span data-old="">{prev}</span>
            <span data-new="">{next}</span>
          </span>
        );
      })}
    </span>
  );
}

/**
 * NumberField — a recessed field for exact numbers, with raised −/+ steppers and mono, tabular
 * digits so values do not jitter as they change.
 *
 * Use it for counts, quantities and amounts where the exact value matters. Pass `format`
 * (`Intl.NumberFormatOptions`) for currency or units instead of typing them into the label.
 * The rule people break: no `min`/`max` — set the bounds so the steppers stop where the
 * business rule does.
 */
export function NumberField({
  size = "default",
  label,
  placeholder,
  decrementLabel = "Decrease",
  incrementLabel = "Increase",
  id: idProp,
  className,
  onValueChange,
  ...props
}: NumberFieldProps) {
  const generatedId = React.useId();
  const id = idProp ?? generatedId;
  const textSize = size === "sm" ? "text-[13px]" : "text-base sm:text-[15px]";

  /* The digit roll: remember the text and direction of a step, read the new text once it renders. */
  const input = React.useRef<HTMLInputElement>(null);
  const last = React.useRef<number | null>(props.value ?? props.defaultValue ?? null);
  const pending = React.useRef<{ from: string; dir: 1 | -1 } | null>(null);
  const [roll, setRoll] = React.useState<Roll | null>(null);
  // Uncontrolled, the value lives inside Base UI: a step re-renders this wrapper so the effect below runs.
  const [, setStepped] = React.useState(0);
  const endRoll = React.useCallback(() => setRoll(null), []);
  React.useLayoutEffect(() => {
    const step = pending.current;
    const el = input.current;
    if (!step || !el) return;
    pending.current = null;
    if (el.value !== step.from)
      setRoll({ from: step.from, to: el.value, dir: step.dir, key: performance.now() });
  });
  /*
   * The caret. A mouse press on a stepper or the scrub label moves focus into the input (Base UI
   * does this so the arrow keys keep stepping), but a blinking caret there reads as "type here".
   * It stays hidden until the number itself is clicked or the text is edited.
   */
  const [caretHidden, setCaretHidden] = React.useState(false);
  const hideCaret = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") setCaretHidden(true);
  };
  const showCaret = () => setCaretHidden(false);

  const handleValueChange: NumberFieldProps["onValueChange"] = (value, details) => {
    const before = last.current;
    last.current = value;
    if (
      input.current &&
      STEP_REASONS.has(details.reason) &&
      value != null &&
      before != null &&
      value !== before &&
      !reducedMotion()
    ) {
      // During a running roll the input text is the value it is rolling to: the next roll starts there.
      pending.current = { from: input.current.value, dir: value > before ? 1 : -1 };
      setStepped((n) => n + 1);
    }
    onValueChange?.(value, details);
  };

  return (
    <BaseNumberField.Root
      data-slot="number-field"
      data-size={size}
      id={id}
      className={sx("flex flex-col items-start gap-2", className)}
      onValueChange={handleValueChange}
      {...props}
    >
      {label != null && (
        <BaseNumberField.ScrubArea
          data-slot="number-field-scrub-area"
          onPointerDown={hideCaret}
          className="cursor-ew-resize select-none data-disabled:cursor-not-allowed data-disabled:opacity-55"
        >
          <label
            htmlFor={id}
            className="cursor-ew-resize text-sm leading-snug font-medium text-foreground select-none"
          >
            {label}
          </label>
          <BaseNumberField.ScrubAreaCursor className="text-foreground drop-shadow-[0_1px_1px_#0006]">
            <MoveHorizontalIcon className="size-5" />
          </BaseNumberField.ScrubAreaCursor>
        </BaseNumberField.ScrubArea>
      )}
      <BaseNumberField.Group
        data-slot="number-field-group"
        className={cn(
          "flex w-full material-field items-center gap-1 rounded-control",
          "has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-0 has-[input:focus-visible]:outline-control-focus",
          // The whole group already dims when disabled; do not dim the steppers twice.
          "data-disabled:[&>button]:opacity-100",
          groupSizes[size],
        )}
      >
        <BaseNumberField.Decrement
          data-slot="number-field-decrement"
          aria-label={decrementLabel}
          onPointerDown={hideCaret}
          className={cn(
            "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full text-foreground focus-ring",
            "data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:size-3.5",
            stepperSizes[size],
          )}
        >
          <MinusIcon />
        </BaseNumberField.Decrement>
        <span className="relative flex min-w-0 flex-1 items-center self-stretch">
          <BaseNumberField.Input
            ref={input}
            data-slot="number-field-input"
            placeholder={placeholder}
            onPointerDown={showCaret}
            onBlur={showCaret}
            onKeyDown={(e) => {
              if (caretHidden && !STEP_KEYS.has(e.key)) showCaret();
            }}
            className={cn(
              "w-full min-w-0 bg-transparent text-center font-mono text-foreground tabular-nums outline-none placeholder:text-control-muted",
              caretHidden ? "caret-transparent" : "caret-foreground",
              textSize,
              // While the digits roll, the overlay draws them; the input keeps its caret and value.
              roll && "text-transparent",
            )}
          />
          {roll && <DigitRoll key={roll.key} roll={roll} className={textSize} onDone={endRoll} />}
        </span>
        <BaseNumberField.Increment
          data-slot="number-field-increment"
          aria-label={incrementLabel}
          onPointerDown={hideCaret}
          className={cn(
            "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full text-foreground focus-ring",
            "data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:size-3.5",
            stepperSizes[size],
          )}
        >
          <PlusIcon />
        </BaseNumberField.Increment>
      </BaseNumberField.Group>
    </BaseNumberField.Root>
  );
}
