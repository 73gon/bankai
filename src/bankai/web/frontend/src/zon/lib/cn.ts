import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

const twMerge = extendTailwindMerge<"material">({
  extend: {
    classGroups: {
      material: [
        "material-primary",
        "material-raised",
        "material-row",
        "material-field",
        "material-menu",
        "surface-container",
        "surface-card",
      ],
    },
  },
});

/**
 * A trigger's `data-slot`, left off when it renders another element: that element keeps its own
 * slot and the styles keyed on it — a `ToolbarButton`, `Toggle` or `Switch` inside a `TooltipTrigger`
 * would otherwise lose its material, because our parts spread incoming props over their slot.
 */
export function triggerSlot(slot: string, render: unknown): { "data-slot"?: string } {
  return render ? {} : { "data-slot": slot };
}

/** Merge class names; later Tailwind classes win over earlier ones. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Base UI parts accept `className` as a string or `(state) => string`. */
export type StateClassName<S> = string | ((state: S) => string | undefined) | undefined;

/**
 * Compose our classes with a consumer's `className` (string or state function) into the
 * function form Base UI accepts. Consumer classes always win.
 *
 *   className={sx("material-raised px-4", className)}
 */
export function sx<S>(base: ClassValue, className?: StateClassName<S>) {
  return (state: S) => cn(base, typeof className === "function" ? className(state) : className);
}
