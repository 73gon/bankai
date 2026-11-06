import { Slider as BaseSlider } from "@base-ui/react/slider";
import * as React from "react";
import { cn, sx } from "@/zon/lib/cn";

export interface SliderProps<
  Value extends number | readonly number[] = number | readonly number[],
> extends BaseSlider.Root.Props<Value> {
  /** A visible label above the track, associated with every thumb. */
  label?: React.ReactNode;
  /** Show the current value (or "min – max" for a range) in mono beside the label. */
  showValue?: boolean;
  /** Accessible names per thumb — required for range sliders ("Minimum budget", "Maximum budget"). */
  thumbLabels?: string[];
  /**
   * A dragged thumb squashes and stretches like something soft, and wobbles back round when let
   * go. Off by default: the thumb keeps its shape.
   */
  squash?: boolean;
}

/* ── Squash and stretch ────────────────────────────────────────────────────── */

/** How far a thumb stretches at most (0.4 = 40% longer along the track). */
const MAX_STRETCH = 0.4;
/** The pinch of a held thumb standing still: already a little wider and flatter under the finger. */
const HELD_STRETCH = 0.12;
/**
 * How quickly the stretch answers speed (px/s): it rises fast and eases toward the maximum, so an
 * ordinary drag already squashes — about two thirds of the way at this speed.
 */
const SPEED_SCALE = 220;
/** The spring that pulls the stretch toward its target: stiffness and damping, per second. */
const STIFFNESS = 420;
const DAMPING = 22;

/**
 * A dragged thumb behaves like something soft. Held, it is pinched a little wider and flatter;
 * the faster it moves, the longer and flatter it gets along the track (keeping its area). As it
 * slows it eases back to the pinch, and when it is let go a spring pulls it round again with a
 * small wobble. Driven per frame from the thumb's own movement, so it follows
 * pointer drags, and keyboard steps leave it alone. Only with `squash`; off under reduced motion.
 */
function useSquash(control: React.RefObject<HTMLElement | null>, enabled: boolean) {
  React.useEffect(() => {
    const root = control.current;
    if (!enabled || !root) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    type Body = { pos: number; speed: number; stretch: number; velocity: number };
    const bodies = new Map<HTMLElement, Body>();
    let frame = 0;
    let last = 0;

    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000 || 1 / 60);
      last = now;
      let moving = false;
      const thumbs = root.querySelectorAll<HTMLElement>('[data-slot="slider-thumb"]');
      // Base UI marks every thumb of a range as dragging; the held one is the one it focused.
      const focused = [...thumbs].some((thumb) => thumb.matches(":focus-within"));
      for (const thumb of thumbs) {
        const vertical = thumb.dataset.orientation === "vertical";
        const r = thumb.getBoundingClientRect();
        const pos = vertical ? r.top + r.height / 2 : r.left + r.width / 2;
        let body = bodies.get(thumb);
        if (!body) bodies.set(thumb, (body = { pos, speed: 0, stretch: 0, velocity: 0 }));
        const dragging = thumb.hasAttribute("data-dragging") && (!focused || thumb.matches(":focus-within"));
        const raw = dragging ? Math.abs(pos - body.pos) / dt : 0;
        body.pos = pos;
        // Smooth the speed so one uneven pointer event does not jolt the shape.
        body.speed += (raw - body.speed) * 0.35;
        const target = dragging
          ? HELD_STRETCH + (MAX_STRETCH - HELD_STRETCH) * (1 - Math.exp(-body.speed / SPEED_SCALE))
          : 0;
        body.velocity += (STIFFNESS * (target - body.stretch) - DAMPING * body.velocity) * dt;
        body.stretch += body.velocity * dt;
        const settled = !dragging && Math.abs(body.stretch) < 0.002 && Math.abs(body.velocity) < 0.02;
        if (settled) {
          body.stretch = body.velocity = body.speed = 0;
          thumb.style.removeProperty("scale");
        } else {
          moving = true;
          const along = 1 + body.stretch;
          const across = 1 / along;
          thumb.style.scale = vertical ? `${across} ${along}` : `${along} ${across}`;
        }
      }
      frame = moving ? requestAnimationFrame(tick) : 0;
    };
    const start = () => {
      if (reduce.matches || frame) return;
      last = performance.now();
      for (const [thumb, body] of bodies) {
        const r = thumb.getBoundingClientRect();
        body.pos = thumb.dataset.orientation === "vertical" ? r.top + r.height / 2 : r.left + r.width / 2;
      }
      frame = requestAnimationFrame(tick);
    };
    root.addEventListener("pointerdown", start);
    return () => {
      root.removeEventListener("pointerdown", start);
      cancelAnimationFrame(frame);
      for (const thumb of bodies.keys()) thumb.style.removeProperty("scale");
    };
  }, [control, enabled]);
}

/**
 * Slider — a recessed 6px track with a soft indicator fill and raised 18px thumbs.
 *
 * Pass an array as `value`/`defaultValue` for a range; one thumb is rendered per value. Use a
 * slider when the rough position matters more than the exact number — for exact values use
 * `NumberField`. The rule people break: a range slider without `thumbLabels`, so both thumbs
 * announce the same name.
 */
export function Slider<Value extends number | readonly number[] = number | readonly number[]>({
  label,
  showValue = false,
  thumbLabels,
  squash = false,
  className,
  children,
  ...props
}: SliderProps<Value>) {
  const source = props.value ?? props.defaultValue;
  const count = Array.isArray(source) ? source.length : 1;
  const hasHeader = label != null || showValue;
  const control = React.useRef<HTMLDivElement>(null);
  useSquash(control, squash);

  return (
    <BaseSlider.Root<Value>
      data-slot="slider"
      className={sx(
        "group/slider flex w-full flex-col gap-2 data-[orientation=vertical]:h-full data-[orientation=vertical]:w-auto data-disabled:[&>[data-slot=slider-control]]:opacity-55",
        className,
      )}
      {...props}
    >
      {hasHeader && (
        <div data-slot="slider-header" className="flex items-baseline justify-between gap-4">
          {label != null && (
            <BaseSlider.Label
              data-slot="slider-label"
              className="text-sm leading-snug font-medium text-foreground select-none group-data-disabled/slider:text-foreground-soft"
            >
              {label}
            </BaseSlider.Label>
          )}
          {showValue && (
            <BaseSlider.Value
              data-slot="slider-value"
              className="ml-auto font-mono text-xs text-muted-foreground tabular-nums group-data-disabled/slider:opacity-55"
            >
              {(formatted) => formatted.join(" – ")}
            </BaseSlider.Value>
          )}
        </div>
      )}
      <BaseSlider.Control
        ref={control}
        data-slot="slider-control"
        className="flex w-full touch-none items-center py-2 select-none data-disabled:cursor-not-allowed data-[orientation=vertical]:h-full data-[orientation=vertical]:min-h-32 data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col data-[orientation=vertical]:px-2 data-[orientation=vertical]:py-0"
      >
        <BaseSlider.Track
          data-slot="slider-track"
          className="relative h-1.5 w-full material-field rounded-full select-none data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5"
        >
          <BaseSlider.Indicator
            data-slot="slider-indicator"
            className="rounded-full bg-foreground-soft select-none"
          />
          {Array.from({ length: count }, (_, index) => (
            <BaseSlider.Thumb
              key={index}
              index={index}
              aria-label={thumbLabels?.[index]}
              data-slot="slider-thumb"
              className={cn(
                "size-[18px] cursor-grab rounded-full bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent select-none",
                "shadow-thumb",
                "transition-[background-color,box-shadow] duration-(--duration) ease-brand",
                "hover:not-data-disabled:bg-control-hover data-dragging:cursor-grabbing",
                // Held, it takes the hover fill; with `squash`, dragged, it squashes and stretches (useSquash).
                "active:not-data-disabled:bg-control-hover data-dragging:bg-control-hover",
                "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-0 has-[:focus-visible]:outline-control-focus",
                "data-disabled:cursor-not-allowed",
              )}
            />
          ))}
          {children}
        </BaseSlider.Track>
      </BaseSlider.Control>
    </BaseSlider.Root>
  );
}
