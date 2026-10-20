import * as React from "react";

/** The box of a glide target, relative to its container's padding box. */
export interface GlideRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GlideOptions {
  /** Off: no target is tracked and `rect` stays null. */
  enabled?: boolean;
  /** Re-measure when these attributes change anywhere inside the container. */
  attributes?: string[];
  /** Re-measure on these events at the container (focusin, pointerover …). */
  events?: string[];
  /** Elements to watch for size changes besides the container itself. */
  items?: string;
  /**
   * Keep the indicator in place this long (ms) after the target goes away. Moving between items
   * (from one trigger to the next) can leave a moment with no target; within the linger the next
   * one is reached by gliding instead of reappearing in place.
   */
  linger?: number;
}

export interface Glide {
  /** The target's box, or the last one while `visible` is false (so a fade-out stays put). */
  rect: GlideRect | null;
  /** A target exists right now. */
  visible: boolean;
  /**
   * Skip the transition this frame: true before the first placement and when the indicator
   * reappears after having no target, so it never flies in from a stale position.
   */
  instant: boolean;
}

/**
 * The target's layout box relative to the container, from offset* — unaffected by transforms, so a
 * button scaled mid-press measures at its resting size. Walks offsetParents up to the container.
 */
function layoutBox(target: Element, container: HTMLElement): GlideRect {
  const node = target as HTMLElement;
  let x = 0;
  let y = 0;
  let current: HTMLElement | null = node;
  while (current && current !== container) {
    x += current.offsetLeft;
    y += current.offsetTop;
    const parent = current.offsetParent as HTMLElement | null;
    if (parent && parent !== container) {
      x += parent.clientLeft - parent.scrollLeft;
      y += parent.clientTop - parent.scrollTop;
    }
    current = parent;
  }
  return { x, y, width: node.offsetWidth, height: node.offsetHeight };
}

const same = (a: GlideRect | null, b: GlideRect | null) =>
  a === b || (!!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height);

/**
 * useGlide — one indicator that glides between the items of a container: the selected segment,
 * the current page, the focused slot, the hovered trigger. `find` returns the item it should
 * cover; the hook measures it and re-measures when attributes or events say the target moved,
 * and when the container or an item resizes. Position the indicator absolutely inside the
 * container with `glideStyle(glide)` and transition `translate`, `width` and `height`.
 */
export function useGlide(
  container: React.RefObject<HTMLElement | null>,
  find: (container: HTMLElement) => Element | null,
  { enabled = true, attributes = [], events = [], items, linger = 0 }: GlideOptions = {},
): Glide {
  const [state, setState] = React.useState<Glide>({ rect: null, visible: false, instant: true });
  const findRef = React.useRef(find);
  findRef.current = find;
  const key = `${enabled}|${attributes.join()}|${events.join()}|${items ?? ""}|${linger}`;

  React.useLayoutEffect(() => {
    const el = container.current;
    if (!enabled || !el) {
      setState({ rect: null, visible: false, instant: true });
      return;
    }

    let lingering = 0;
    const hide = () => setState((prev) => (prev.visible ? { ...prev, visible: false } : prev));
    const measure = () => {
      const target = findRef.current(el);
      if (!target && linger > 0) {
        if (!lingering) {
          lingering = window.setTimeout(() => {
            lingering = 0;
            if (findRef.current(el)) measure();
            else hide();
          }, linger);
        }
        return;
      }
      clearTimeout(lingering);
      lingering = 0;
      setState((prev) => {
        if (!target) return prev.visible ? { ...prev, visible: false } : prev;
        const rect = layoutBox(target, el);
        // Appearing again (or for the first time): jump into place, then glide from there.
        const instant = !prev.visible;
        if (prev.visible && same(prev.rect, rect)) return prev;
        return { rect, visible: true, instant };
      });
    };

    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };

    const resize = new ResizeObserver(measure);
    const observe = () => {
      resize.disconnect();
      resize.observe(el);
      if (items) el.querySelectorAll(items).forEach((node) => resize.observe(node));
    };
    const mutations = new MutationObserver((records) => {
      if (records.some((r) => r.type === "childList")) observe();
      measure();
    });
    mutations.observe(el, {
      subtree: true,
      childList: true,
      attributes: attributes.length > 0,
      ...(attributes.length > 0 ? { attributeFilter: attributes } : {}),
    });
    for (const type of events) el.addEventListener(type, schedule);
    observe();
    measure();

    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(lingering);
      mutations.disconnect();
      resize.disconnect();
      for (const type of events) el.removeEventListener(type, schedule);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures the options.
  }, [container, key]);

  // After an instant placement, re-enable the transition on the next frame.
  React.useEffect(() => {
    if (!state.instant || !state.visible) return;
    const frame = requestAnimationFrame(() =>
      setState((prev) => (prev.instant ? { ...prev, instant: false } : prev)),
    );
    return () => cancelAnimationFrame(frame);
  }, [state.instant, state.visible]);

  return state;
}

/** Inline style that places an absolutely positioned indicator over the glide target. */
export function glideStyle({ rect }: Glide): React.CSSProperties | undefined {
  return rect ? { translate: `${rect.x}px ${rect.y}px`, width: rect.width, height: rect.height } : undefined;
}

/**
 * The indicator's motion: translate and size in 2 × --duration with the brand ease, opacity in
 * --duration. Put `data-instant` on it while `glide.instant`, `data-visible` while visible.
 * Reduced motion sets --duration to 0, so it moves instantly.
 */
export const glideMotion =
  "pointer-events-none absolute top-0 left-0 opacity-0 data-visible:opacity-100 transition-[translate,width,height,opacity] duration-[calc(var(--duration)*2)] ease-brand data-instant:transition-none";

/** Merges a component's own ref with the one passed in by the caller. */
export function useMergedRef<T>(
  ref: React.Ref<T> | undefined,
): [React.RefObject<T | null>, (node: T | null) => void] {
  const own = React.useRef<T | null>(null);
  const set = React.useCallback(
    (node: T | null) => {
      own.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) (ref as React.MutableRefObject<T | null>).current = node;
    },
    [ref],
  );
  return [own, set];
}
