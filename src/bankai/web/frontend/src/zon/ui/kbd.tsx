import type * as React from "react";
import { cn } from "@/zon/lib/cn";

/**
 * The key-cap look as a class string, for elements that must not be a `<kbd>` — e.g. an
 * "Esc" hint that is really a close button: `<button className={kbdClassName}>Esc</button>`.
 */
export const kbdClassName = cn(
  "inline-flex min-h-5 min-w-5 items-center justify-center rounded-checkbox px-1.5 font-mono text-[11px] leading-none font-normal text-foreground-soft select-none",
  "bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-key",
  "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-3",
);

/**
 * Kbd — a keyboard key, as a raised mini key: Licht Mono 11px, 20px tall, 5px radius.
 *
 * Use it for shortcuts in menus, tooltips and docs ("Press ⌘ K"). One key per `Kbd`; group a
 * chord with `KbdGroup`. The rule people break: using it for code or for labels — inline code
 * is `<code>`, a label is a `Tag`.
 */
export function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return <kbd data-slot="kbd" className={cn(kbdClassName, className)} {...props} />;
}

/** A chord of keys, e.g. ⌘ + K. Renders a `<kbd>` wrapping one `Kbd` per key. */
export function KbdGroup({ className, ...props }: React.ComponentProps<"kbd">) {
  return <kbd data-slot="kbd-group" className={cn("inline-flex items-center gap-1", className)} {...props} />;
}
