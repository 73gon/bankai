import * as React from "react";

/*
 * Collapsible buttons share their row's room. A row is the nearest ancestor marked
 * `data-collapse-row`, or else the button's parent. When the row's content does not fit, the
 * last collapsible button folds to its icon, then the one before it, only as many as needed;
 * when there is room again they unfold in the opposite order.
 *
 * The decision is made from widths measured while each button was expanded and settled, never
 * from a button mid-animation, so a folding button cannot change its own outcome. A child of
 * the row that grows (`flex-grow`) counts at its min-width — or at its `--collapse-min`, when
 * it sets one — so a search field gives way first.
 *
 * A fold takes a moment, and a row that wraps would break onto two lines while it runs. So a
 * row should not wrap while its buttons can still fold: give the growing child `min-w-0` and
 * its real minimum as `--collapse-min`, and it lends the room a folding button still takes.
 * When every button is folded and the row still does not fit, it gets `data-collapse-overflow`:
 * the moment to let it wrap.
 */

interface Member {
  el: HTMLElement;
  set: (collapsed: boolean) => void;
  collapsed: boolean;
  /** Width when expanded, measured while expanded and not animating. */
  full: number;
}

interface Row {
  el: HTMLElement;
  members: Set<Member>;
  observer: ResizeObserver;
  frame: number;
}

const rows = new Map<HTMLElement, Row>();

const px = (value: string) => {
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : 0;
};

/** A length from a custom property: px or rem. */
const length = (value: string) => {
  const v = value.trim();
  if (v.endsWith("rem")) return px(v) * px(getComputedStyle(document.documentElement).fontSize);
  return px(v);
};

/** The room the row's children ask for: fixed children at their width, growing ones at their min. */
function contentWidth(row: HTMLElement) {
  const children = [...row.children].filter((child): child is HTMLElement => {
    if (!(child instanceof HTMLElement)) return false;
    const style = getComputedStyle(child);
    return style.display !== "none" && style.position !== "absolute" && style.position !== "fixed";
  });
  const style = getComputedStyle(row);
  const gap = px(style.columnGap) * Math.max(0, children.length - 1);
  return (
    gap +
    children.reduce((sum, child) => {
      const s = getComputedStyle(child);
      return (
        sum +
        (px(s.flexGrow) > 0
          ? Math.max(px(s.minWidth), length(s.getPropertyValue("--collapse-min")))
          : child.getBoundingClientRect().width)
      );
    }, 0)
  );
}

const settled = (el: HTMLElement) => el.getAnimations({ subtree: true }).length === 0;

function layout(row: Row) {
  row.frame = 0;
  const members = [...row.members].sort((a, b) =>
    a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
  );
  for (const m of members) {
    if (!m.collapsed && settled(m.el)) m.full = m.el.getBoundingClientRect().width;
  }
  const style = getComputedStyle(row.el);
  const room = row.el.clientWidth - px(style.paddingLeft) - px(style.paddingRight);
  // Everything expanded: today's content with each button's live width swapped for its full one.
  let excess =
    contentWidth(row.el) +
    members.reduce((sum, m) => sum + m.full - m.el.getBoundingClientRect().width, 0) -
    room;
  const next = new Map<Member, boolean>();
  for (const m of [...members].reverse()) {
    const compact = m.el.getBoundingClientRect().height;
    const fold = excess > 0.5;
    next.set(m, fold);
    if (fold) excess -= Math.max(0, m.full - compact);
  }
  row.el.toggleAttribute("data-collapse-overflow", excess > 0.5);
  for (const [m, fold] of next) {
    if (m.collapsed !== fold) {
      m.collapsed = fold;
      m.set(fold);
    }
  }
}

function schedule(row: Row) {
  if (!row.frame) row.frame = requestAnimationFrame(() => layout(row));
}

function join(el: HTMLElement, member: Member) {
  const rowEl = el.parentElement?.closest<HTMLElement>("[data-collapse-row]") ?? el.parentElement;
  if (!rowEl) return () => {};
  let row = rows.get(rowEl);
  if (!row) {
    const created: Row = {
      el: rowEl,
      members: new Set(),
      observer: new ResizeObserver(() => schedule(created)),
      frame: 0,
    };
    created.observer.observe(rowEl);
    rows.set(rowEl, created);
    row = created;
  }
  const current = row;
  current.members.add(member);
  // A button's own size changes too (a font loading, a new label): measure again.
  current.observer.observe(el);
  // The first decision lands before the first paint, so a tight row never animates on load.
  layout(current);
  return () => {
    current.members.delete(member);
    current.observer.unobserve(el);
    if (current.members.size === 0) {
      rowEl.removeAttribute("data-collapse-overflow");
      current.observer.disconnect();
      cancelAnimationFrame(current.frame);
      rows.delete(rowEl);
    } else schedule(current);
  };
}

/**
 * Whether a collapsible button should show only its icon now. Joins the button's row while
 * `enabled`; `ready` turns true after the first decision, so the first one is drawn without motion.
 */
export function useCollapsible(ref: React.RefObject<HTMLElement | null>, enabled: boolean) {
  const [collapsed, setCollapsed] = React.useState(false);
  const [ready, setReady] = React.useState(false);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return;
    const member: Member = {
      el,
      set: setCollapsed,
      collapsed: false,
      full: el.getBoundingClientRect().width,
    };
    const leave = join(el, member);
    const frame = requestAnimationFrame(() => setReady(true));
    return () => {
      cancelAnimationFrame(frame);
      leave();
      setCollapsed(false);
      setReady(false);
    };
  }, [enabled, ref]);

  return { collapsed: enabled && collapsed, ready };
}
