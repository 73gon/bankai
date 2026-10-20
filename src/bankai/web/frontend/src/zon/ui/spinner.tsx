import { LoaderCircleIcon } from "lucide-react";
import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/zon/lib/cn";
import { bracketGeometry } from "@/zon/lib/logo-geometry";

const spinnerSizes = {
  sm: "size-3 text-[12px]",
  default: "size-4 text-[16px]",
  lg: "size-5 text-[20px]",
} as const;

export type SpinnerSize = keyof typeof spinnerSizes;
export type SpinnerVariant =
  | "ring"
  | "arc"
  | "dots"
  | "bars"
  | "pulse"
  | "orbit"
  | "grid"
  | "cursor"
  | "ascii"
  | "brackets"
  | "crescent";

/** Every variant, in gallery order. */
export const SPINNER_VARIANTS: readonly SpinnerVariant[] = [
  "ring",
  "arc",
  "dots",
  "bars",
  "pulse",
  "orbit",
  "grid",
  "cursor",
  "ascii",
  "brackets",
  "crescent",
];

/** The four frames of the ASCII spinner. All four are in the Licht Mono latin subset; the minus sign (U+2212) sits on the maths axis, centred, where a hyphen would sit low. */
const ASCII_FRAMES = ["|", "/", "−", "\\"] as const;

export interface SpinnerProps extends Omit<ComponentProps<"span">, "children"> {
  /**
   * The face. `ring` (default) a turning `LoaderCircle` · `arc` a progress-ring arc that grows
   * and shrinks on a hairline track · `dots` three dots in sequence · `bars` an equaliser ·
   * `pulse` a ring widening from a dot · `orbit` a dot circling a hairline · `grid` a 3×3 wave ·
   * `cursor` a blinking terminal block · `ascii` `| / − \` in Licht Mono · `brackets` the
   * logo's brackets opening and closing · `crescent` a moon waxing and waning.
   */
  variant?: SpinnerVariant;
  /** `sm` 12px · `default` 16px · `lg` 20px. */
  size?: SpinnerSize;
  /** The accessible name announced for the status. Say what is loading: "Sending message". */
  label?: string;
}

function Svg({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg
      aria-hidden
      focusable="false"
      viewBox={viewBox}
      fill="currentColor"
      data-slot="spinner-art"
      className="block size-full overflow-visible"
    >
      {children}
    </svg>
  );
}

/* The brackets at a 14-unit height, in a 24-unit square with room to open by 3 units a side. */
const BR = bracketGeometry(14);
const BR_X = (24 - (2 * BR.w + BR.gap)) / 2;
const BR_Y = (24 - BR.h) / 2;

function Art({ variant, maskId }: { variant: SpinnerVariant; maskId: string }) {
  switch (variant) {
    case "arc":
      return (
        <Svg>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" opacity="0.2" />
          <circle
            data-part="arc"
            cx="12"
            cy="12"
            r="9"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            pathLength={100}
          />
        </Svg>
      );
    case "dots":
      return (
        <Svg>
          {[4.5, 12, 19.5].map((cx) => (
            <circle key={cx} data-part="dot" cx={cx} cy="12" r="2.5" />
          ))}
        </Svg>
      );
    case "bars":
      return (
        <Svg>
          {[3, 8.5, 14, 19.5].map((x) => (
            <rect key={x} data-part="bar" x={x} y="4" width="2.5" height="16" rx="0.5" />
          ))}
        </Svg>
      );
    case "pulse":
      return (
        <Svg>
          <circle
            data-part="ripple"
            cx="12"
            cy="12"
            r="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <circle
            data-part="ripple"
            cx="12"
            cy="12"
            r="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <circle cx="12" cy="12" r="3" />
        </Svg>
      );
    case "orbit":
      return (
        <Svg>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.25" />
          <g data-part="satellite">
            <circle cx="12" cy="3" r="3" />
          </g>
        </Svg>
      );
    case "grid":
      return (
        <Svg>
          {[0, 1, 2].flatMap((row) =>
            [0, 1, 2].map((col) => (
              <rect
                key={`${row}${col}`}
                data-part="cell"
                x={2 + col * 7.5}
                y={2 + row * 7.5}
                width="5"
                height="5"
                rx="0.75"
                style={{ animationDelay: `${(row + col) * 0.1}s` }}
              />
            )),
          )}
        </Svg>
      );
    case "cursor":
      return (
        <Svg>
          <rect data-part="caret" x="7.5" y="3" width="9" height="18" />
        </Svg>
      );
    case "brackets":
      return (
        <Svg>
          <g transform={`translate(${BR_X} ${BR_Y})`}>
            <g data-part="bracket-left">
              {BR.left.map(([x, y, w, h]) => (
                <rect key={`${x},${y}`} x={x} y={y} width={w} height={h} />
              ))}
            </g>
            {/* The offset sits on the outer group: a CSS transform would replace the attribute. */}
            <g transform={`translate(${BR.w + BR.gap} 0)`}>
              <g data-part="bracket-right">
                {BR.right.map(([x, y, w, h]) => (
                  <rect key={`${x},${y}`} x={x} y={y} width={w} height={h} />
                ))}
              </g>
            </g>
          </g>
        </Svg>
      );
    case "crescent":
      return (
        <Svg>
          <defs>
            <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
              <rect width="24" height="24" fill="white" />
              <circle data-part="shadow" cx="12" cy="12" r="9.5" fill="black" />
            </mask>
          </defs>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.3" />
          <circle cx="12" cy="12" r="9" mask={`url(#${maskId})`} />
        </Svg>
      );
    default:
      return null;
  }
}

/**
 * Spinner — an indeterminate loading indicator in the current ink, with `role="status"` and an
 * accessible name.
 *
 * Eleven faces, all CSS-driven and monochrome: `ring` (default) for controls; `arc`, `dots`,
 * `bars`, `pulse`, `orbit` and `grid` for panels and empty states; `cursor` and `ascii` for
 * mono, technical surfaces; `brackets` and `crescent` as brand moments. Every face stands still
 * under reduced motion. The rule people break: a spinner for work with a known length — use
 * `Progress` when you can say how far along it is.
 */
export function Spinner({
  variant = "ring",
  size = "default",
  label = "Loading",
  className,
  ...props
}: SpinnerProps) {
  const maskId = `ep-spinner-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span
      role="status"
      aria-label={label}
      data-slot="spinner"
      data-variant={variant}
      data-size={size}
      className={cn(
        "inline-flex shrink-0 items-center justify-center align-middle text-current",
        spinnerSizes[size],
        className,
      )}
      {...props}
    >
      {variant === "ring" ? (
        <LoaderCircleIcon aria-hidden className="size-full animate-spin motion-reduce:animate-none" />
      ) : variant === "ascii" ? (
        <span
          aria-hidden
          className="block h-[1em] w-[1ch] overflow-hidden text-center font-mono leading-none"
        >
          <span data-slot="spinner-frames" className="flex flex-col motion-reduce:hidden">
            {ASCII_FRAMES.map((frame) => (
              <span key={frame} className="block h-[1em]">
                {frame}
              </span>
            ))}
          </span>
          <span className="hidden h-[1em] motion-reduce:block">…</span>
        </span>
      ) : (
        <Art variant={variant} maskId={maskId} />
      )}
    </span>
  );
}
