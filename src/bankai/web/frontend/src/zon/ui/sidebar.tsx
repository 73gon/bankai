import { useRender } from "@base-ui/react/use-render";
import { PanelLeftIcon } from "lucide-react";
import * as React from "react";
import { cn } from "@/zon/lib/cn";
import { Button, type ButtonProps } from "@/zon/ui/button";
import { Drawer, DrawerContent, DrawerTitle } from "@/zon/ui/drawer";
import { ScrollArea, type ScrollAreaProps } from "@/zon/ui/scroll-area";
import { Separator, type SeparatorProps } from "@/zon/ui/separator";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/zon/ui/tooltip";

/* ── App shell state ───────────────────────────────────────────────────────── */

/**
 * How the sidebar collapses on desktop. `offcanvas` slides it away entirely, `icon` shrinks it to
 * a rail of 38px icon squares with tooltips, `none` keeps it open. On mobile, `offcanvas` and
 * `icon` both become a sheet.
 */
export type SidebarCollapsible = "offcanvas" | "icon" | "none";

export interface SidebarContextValue {
  /** `expanded` or `collapsed` — the desktop state. */
  state: "expanded" | "collapsed";
  /** Desktop open state. */
  open: boolean;
  setOpen: (open: boolean) => void;
  /** Mobile sheet open state. */
  openMobile: boolean;
  setOpenMobile: (open: boolean) => void;
  /** Below 768px the sidebar is a sheet. */
  isMobile: boolean;
  /** Toggles the sheet on mobile and the panel on desktop. */
  toggleSidebar: () => void;
  collapsible: SidebarCollapsible;
}

const SidebarContext = React.createContext<SidebarContextValue | null>(null);

/** The sidebar's density: `default` 38px rows, `sm` 30px rows (`Sidebar size`). */
const SidebarSizeContext = React.createContext<"default" | "sm">("default");

/** Where a `Sidebar` is rendered: the desktop panel or the mobile sheet. */
const SidebarSurfaceContext = React.createContext<"panel" | "sheet">("panel");

/**
 * useSidebar — the app shell's state, for building your own trigger or reacting to collapse:
 * `{ state, open, setOpen, openMobile, setOpenMobile, isMobile, toggleSidebar, collapsible }`.
 * Must be used inside a `SidebarProvider`.
 */
export function useSidebar() {
  const context = React.useContext(SidebarContext);
  if (!context) throw new Error("useSidebar must be used within a <SidebarProvider />");
  return context;
}

const MOBILE_QUERY = "(width < 48rem)";
const subscribeMobile = (onChange: () => void) => {
  const mq = window.matchMedia(MOBILE_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
};
const useIsMobile = () =>
  React.useSyncExternalStore(
    subscribeMobile,
    () => window.matchMedia(MOBILE_QUERY).matches,
    () => false,
  );

function readStored(key: string): boolean | undefined {
  try {
    const value = window.localStorage.getItem(key);
    if (value === "true") return true;
    if (value === "false") return false;
  } catch {
    // Storage can be unavailable (private mode, blocked site data). The default stands.
  }
  return undefined;
}

function writeStored(key: string, open: boolean) {
  try {
    window.localStorage.setItem(key, String(open));
  } catch {
    // Ignore: persistence is a convenience.
  }
  try {
    // A cookie as well, so a server can read it and render the right state first time.
    document.cookie = `${encodeURIComponent(key)}=${open}; path=/; max-age=${60 * 60 * 24 * 365}; SameSite=Lax`;
  } catch {
    // Ignore.
  }
}

export interface SidebarProviderProps extends React.ComponentProps<"div"> {
  /** Initial desktop state when uncontrolled. Pass the cookie value here when rendering on a server. */
  defaultOpen?: boolean;
  /** Controlled desktop state. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** How the sidebar collapses on desktop. Defaults to `offcanvas`. */
  collapsible?: SidebarCollapsible;
  /**
   * Where the desktop state is remembered (localStorage and a cookie of this name). `null`
   * turns persistence off. Defaults to `"zon-sidebar"`.
   */
  storageKey?: string | null;
  /** Key that toggles the sidebar with ⌘ (macOS) or Ctrl. `false` turns it off. Defaults to `"b"`. */
  keyboardShortcut?: string | false;
}

/**
 * SidebarProvider — turns a `Sidebar` into an app shell: open state, collapse (`offcanvas`,
 * `icon` or `none`), ⌘B / Ctrl+B, a sheet on mobile and a remembered state.
 *
 * It renders the shell's flex row; put the `Sidebar` and a `SidebarInset` inside. The shell is
 * at least the viewport tall (`min-h-svh`) — give it a fixed height (`h-[560px] min-h-0`) to
 * embed it in a page. Widths come from `--sidebar-width` (16rem) and `--sidebar-width-icon`.
 */
export function SidebarProvider({
  defaultOpen = true,
  open: openProp,
  onOpenChange,
  collapsible = "offcanvas",
  storageKey = "zon-sidebar",
  keyboardShortcut = "b",
  className,
  style,
  children,
  ...props
}: SidebarProviderProps) {
  const isMobile = useIsMobile();
  const [openMobile, setOpenMobile] = React.useState(false);
  const [openState, setOpenState] = React.useState(defaultOpen);
  const open = openProp ?? openState;

  const openRef = React.useRef(open);
  openRef.current = open;

  const setOpen = React.useCallback(
    (value: boolean) => {
      if (openProp === undefined) setOpenState(value);
      onOpenChange?.(value);
      if (storageKey) writeStored(storageKey, value);
    },
    [openProp, onOpenChange, storageKey],
  );

  // Restore the remembered state after hydration (uncontrolled only).
  React.useEffect(() => {
    if (openProp !== undefined || !storageKey) return;
    const stored = readStored(storageKey);
    if (stored !== undefined) setOpenState(stored);
  }, [openProp, storageKey]);

  const toggleSidebar = React.useCallback(() => {
    if (isMobile) setOpenMobile((value) => !value);
    else setOpen(!openRef.current);
  }, [isMobile, setOpen]);

  React.useEffect(() => {
    if (!keyboardShortcut || collapsible === "none") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== keyboardShortcut.toLowerCase()) return;
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      event.preventDefault();
      toggleSidebar();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [keyboardShortcut, collapsible, toggleSidebar]);

  const value = React.useMemo<SidebarContextValue>(
    () => ({
      state: open || collapsible === "none" ? "expanded" : "collapsed",
      open,
      setOpen,
      openMobile,
      setOpenMobile,
      isMobile,
      toggleSidebar,
      collapsible,
    }),
    [open, setOpen, openMobile, isMobile, toggleSidebar, collapsible],
  );

  return (
    <SidebarContext.Provider value={value}>
      <TooltipProvider>
        <div
          data-slot="sidebar-wrapper"
          className={cn("flex min-h-svh w-full", className)}
          style={
            {
              "--sidebar-width": "16rem",
              "--sidebar-width-icon": "calc(var(--control-h) + 25px)",
              ...style,
            } as React.CSSProperties
          }
          {...props}
        >
          {children}
        </div>
      </TooltipProvider>
    </SidebarContext.Provider>
  );
}

/* ── Sidebar ───────────────────────────────────────────────────────────────── */

export interface SidebarProps extends React.ComponentProps<"nav"> {
  /** In an app shell: which edge the panel sits on. Put a right sidebar after the `SidebarInset`. */
  side?: "left" | "right";
  /**
   * In an app shell: `sidebar` (default) divides the panel from the page with a hairline;
   * `inset` drops the hairline and sets the page beside it as a raised 20px panel with an 8px
   * margin, so the sidebar reads as the ground the work sits on; `floating` is the reverse — the
   * sidebar itself is the raised 20px panel, 8px from the shell's edges, beside a flat page.
   * Desktop only.
   */
  variant?: "sidebar" | "inset" | "floating";
  /**
   * `default` 38px rows; `sm` the dense sidebar: 30px rows, 13px labels, tighter groups and a
   * narrower icon rail, for tools with many sections. Controls inside (a search field, a
   * button in the header) take the 30px height too.
   */
  size?: "default" | "sm";
}

/* ── The current row glides ──────────────────────────────────────────────── */

const ROW = '[data-slot="sidebar-item"]';
const CURRENT_ROW = `${ROW}[aria-current="page"]`;

/** An element's box relative to the nav's padding box, scrolled and transformed as it is on screen. */
function boxIn(nav: HTMLElement, el: Element) {
  const r = el.getBoundingClientRect();
  const n = nav.getBoundingClientRect();
  return {
    x: r.left - n.left - nav.clientLeft,
    y: r.top - n.top - nav.clientTop,
    w: r.width,
    h: r.height,
  };
}

/**
 * When the current page changes, its raised row glides from the old row to the new one — across
 * groups, and from the scrolling list to the footer. A copy of the row's material (this span,
 * under the rows in the nav's own stacking context) makes the trip while the two real rows stay
 * flat; on arrival the new row takes its own material again in the same frame. The first
 * placement, a current row that was not on screen, and reduced motion set it in place.
 */
function CurrentGlide() {
  const ref = React.useRef<HTMLSpanElement>(null);
  React.useLayoutEffect(() => {
    const ghost = ref.current;
    const nav = ghost?.parentElement;
    if (!ghost || !nav) return;
    let last = nav.querySelector<HTMLElement>(CURRENT_ROW);
    let running: Animation | null = null;
    const rows = new Set<HTMLElement>();

    const box = (el: Element) => boxIn(nav, el);
    const shown = (el: HTMLElement | null): el is HTMLElement =>
      !!el && el.isConnected && el.offsetWidth > 0 && el.offsetHeight > 0;
    const land = () => {
      running = null;
      ghost.style.visibility = "hidden";
      // Back to their own material, without a transition: the copy is gone in the same frame.
      for (const row of rows) row.dataset.glide = "landing";
      const landed = [...rows];
      rows.clear();
      requestAnimationFrame(() =>
        requestAnimationFrame(() =>
          landed.forEach((row) => row.dataset.glide === "landing" && delete row.dataset.glide),
        ),
      );
    };

    const move = () => {
      const next = nav.querySelector<HTMLElement>(CURRENT_ROW);
      if (next === last) return;
      const from = running ? box(ghost) : shown(last) ? box(last) : null;
      const prev = last;
      last = next;
      running?.cancel();
      if (!from || !shown(next) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        if (running) land();
        return;
      }
      const to = box(next);
      for (const row of [prev, next]) {
        if (row) {
          row.dataset.glide = "hidden";
          rows.add(row);
        }
      }
      ghost.style.visibility = "visible";
      const duration = parseFloat(getComputedStyle(nav).getPropertyValue("--duration")) || 150;
      const at = (b: typeof from) => ({
        translate: `${b.x}px ${b.y}px`,
        width: `${b.w}px`,
        height: `${b.h}px`,
      });
      running = ghost.animate([at(from), at(to)], {
        duration: duration * 2,
        easing: getComputedStyle(nav).getPropertyValue("--ease").trim() || "ease-out",
        fill: "forwards",
      });
      running.onfinish = land;
    };

    const watch = new MutationObserver(move);
    watch.observe(nav, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-current"],
    });
    return () => {
      watch.disconnect();
      running?.cancel();
      for (const row of rows) delete row.dataset.glide;
    };
  }, []);
  return (
    <span
      ref={ref}
      aria-hidden
      data-slot="sidebar-current-glide"
      data-current=""
      className="pointer-events-none invisible absolute top-0 left-0 -z-1 rounded-button material-row"
    />
  );
}

/**
 * Hover is one soft fill that follows the pointer up and down the list instead of each row lighting
 * on its own. It appears in place where the pointer comes in, glides from row to row (across the
 * gap between groups too, within a short linger), and fades where it is when the pointer leaves.
 * It sits under the rows and under the current row's material. Touch never shows it.
 */
function HoverGlide() {
  const ref = React.useRef<HTMLSpanElement>(null);
  React.useLayoutEffect(() => {
    const fill = ref.current;
    const nav = fill?.parentElement;
    if (!fill || !nav) return;
    let row: HTMLElement | null = null;
    let linger = 0;
    let frame = 0;

    const hide = () => {
      clearTimeout(linger);
      linger = 0;
      row = null;
      // Fades out where it is.
      delete fill.dataset.visible;
    };
    const place = () => {
      if (!row) return;
      if (!row.isConnected || row.offsetWidth === 0) return hide();
      const b = boxIn(nav, row);
      fill.style.translate = `${b.x}px ${b.y}px`;
      fill.style.width = `${b.w}px`;
      fill.style.height = `${b.h}px`;
    };
    const show = (next: HTMLElement) => {
      clearTimeout(linger);
      linger = 0;
      if (next === row) return;
      row = next;
      if (fill.dataset.visible !== undefined) return place();
      // Coming in: jump to the row with no transition, then fade in there.
      fill.dataset.instant = "";
      place();
      void fill.offsetWidth;
      delete fill.dataset.instant;
      fill.dataset.visible = "";
    };

    const over = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      const next = (event.target as Element).closest<HTMLElement>(ROW);
      if (next && nav.contains(next)) show(next);
      else if (row && !linger) linger = window.setTimeout(hide, 120);
    };
    const follow = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(place);
    };
    const resize = new ResizeObserver(follow);
    resize.observe(nav);
    nav.addEventListener("pointerover", over);
    nav.addEventListener("pointerleave", hide);
    // A scrolling list carries the row away; the fill goes with it.
    nav.addEventListener("scroll", follow, true);
    return () => {
      clearTimeout(linger);
      cancelAnimationFrame(frame);
      resize.disconnect();
      nav.removeEventListener("pointerover", over);
      nav.removeEventListener("pointerleave", hide);
      nav.removeEventListener("scroll", follow, true);
    };
  }, []);
  return (
    <span
      ref={ref}
      aria-hidden
      data-slot="sidebar-hover-glide"
      className={cn(
        "pointer-events-none absolute top-0 left-0 -z-1 rounded-button bg-(--ink-ghost) opacity-0 data-visible:opacity-100",
        "transition-[translate,width,height,opacity] duration-[calc(var(--duration)*1.4)] ease-brand data-instant:transition-none",
      )}
    />
  );
}

/**
 * Sidebar — section navigation for docs, settings and dashboards.
 *
 * A `<nav>` of `SidebarGroup`s, each with an eyebrow `SidebarGroupLabel` and 38px
 * `SidebarItem` rows at 12px radius. Rows are flat at rest, share one soft hover fill that glides
 * with the pointer, and take the raised material when current. The rule people break: marking the current page with a flat fill or
 * a bold weight. Set `current` (it writes `aria-current="page"`) and let the material show it.
 * A sidebar never shows a scrollbar: a long one goes in `<ScrollArea scrollbar={false}>`.
 *
 * Inside a `SidebarProvider` it becomes the app shell's panel: a full-height column divided from
 * the page by a hairline, holding `SidebarHeader`, `SidebarContent` and `SidebarFooter`. It
 * collapses on desktop and turns into a sheet on mobile.
 */
export function Sidebar({
  side = "left",
  variant = "sidebar",
  size = "default",
  className,
  children,
  ...props
}: SidebarProps) {
  const shell = React.useContext(SidebarContext);
  const dense = size === "sm";
  // Dense: every control inside (the rows, a search field, a header button) is 30px.
  const sizeClass = dense ? "[--control-h:var(--control-h-sm)]" : undefined;
  const content = (
    <SidebarSizeContext.Provider value={size}>
      <HoverGlide />
      <CurrentGlide />
      {children}
    </SidebarSizeContext.Provider>
  );

  if (!shell) {
    return (
      <nav
        data-slot="sidebar"
        data-size={size}
        className={cn(
          "relative isolate flex w-full flex-col",
          dense ? "gap-5" : "gap-7",
          sizeClass,
          className,
        )}
        {...props}
      >
        {content}
      </nav>
    );
  }

  const { state, collapsible, isMobile, openMobile, setOpenMobile } = shell;
  // No fill beside the page: the page ground (the body's grain) runs under the sidebar too.
  // isolate: the gliding current row paints under the rows but over the panel's own fill.
  const panelClass = "isolate flex h-full flex-col font-body text-foreground";

  if (collapsible === "none") {
    return (
      <nav
        data-slot="sidebar"
        data-side={side}
        data-variant={variant}
        data-size={size}
        data-state="expanded"
        className={cn(
          panelClass,
          sizeClass,
          "sticky top-0 h-svh max-h-full w-(--sidebar-width) shrink-0 border-border/60",
          variant !== "sidebar" ? undefined : side === "left" ? "border-e" : "border-s",
          className,
        )}
        {...props}
      >
        {content}
      </nav>
    );
  }

  if (isMobile) {
    return (
      <Drawer side={side} open={openMobile} onOpenChange={setOpenMobile}>
        <DrawerContent
          data-slot="sidebar-sheet"
          className={cn(
            "w-[min(18rem,calc(100vw-3rem))] **:data-[slot=drawer-body]:gap-0 **:data-[slot=drawer-body]:p-0",
          )}
        >
          <SidebarSurfaceContext.Provider value="sheet">
            <DrawerTitle className="sr-only">{props["aria-label"] ?? "Navigation"}</DrawerTitle>
            <nav
              data-slot="sidebar"
              data-side={side}
              data-size={size}
              className={cn(panelClass, "relative min-h-0 flex-1 bg-background", sizeClass, className)}
              {...props}
            >
              {content}
            </nav>
          </SidebarSurfaceContext.Provider>
        </DrawerContent>
      </Drawer>
    );
  }

  const offcanvas = state === "collapsed" && collapsible === "offcanvas";
  return (
    <div
      data-slot="sidebar-shell"
      data-variant={variant}
      data-size={size}
      style={
        dense
          ? ({ "--sidebar-width-icon": "calc(var(--control-h-sm) + 25px)" } as React.CSSProperties)
          : undefined
      }
      data-state={state}
      data-collapsible={state === "collapsed" ? collapsible : undefined}
      data-side={side}
      className={cn(
        "group/sidebar sticky top-0 hidden h-svh max-h-full w-(--sidebar-width) shrink-0 overflow-clip md:block",
        "transition-[width] duration-(--duration) ease-brand",
        "data-[collapsible=icon]:w-(--sidebar-width-icon) data-[collapsible=offcanvas]:w-0",
      )}
    >
      <nav
        data-slot="sidebar"
        data-side={side}
        data-size={size}
        inert={offcanvas || undefined}
        className={cn(
          panelClass,
          sizeClass,
          "absolute inset-y-0 w-(--sidebar-width) border-border/60 transition-[width] duration-(--duration) ease-brand",
          "group-data-[collapsible=icon]/sidebar:w-(--sidebar-width-icon)",
          side === "left" ? "left-0" : "right-0",
          variant !== "sidebar" ? undefined : side === "left" ? "border-e" : "border-s",
          className,
        )}
        {...props}
      >
        {content}
      </nav>
    </div>
  );
}

/** The top of an app-shell sidebar: a logo, a workspace switcher, a search row. Never scrolls. */
export function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  const dense = React.useContext(SidebarSizeContext) === "sm";
  return (
    <div
      data-slot="sidebar-header"
      className={cn(
        "flex shrink-0 flex-col px-3",
        dense ? "gap-1.5 pt-3 pb-1.5" : "gap-2 pt-4 pb-2",
        className,
      )}
      {...props}
    />
  );
}

/** The bottom of an app-shell sidebar: the account, settings, help. Never scrolls. */
export function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  const dense = React.useContext(SidebarSizeContext) === "sm";
  return (
    <div
      data-slot="sidebar-footer"
      className={cn(
        "mt-auto flex shrink-0 flex-col px-3",
        dense ? "gap-1.5 pt-1.5 pb-3" : "gap-2 pt-2 pb-4",
        className,
      )}
      {...props}
    />
  );
}

export interface SidebarContentProps extends ScrollAreaProps {}

/**
 * The scrolling middle of an app-shell sidebar, holding the `SidebarGroup`s 28px apart. It is a
 * `ScrollArea` with `scrollbar={false}`: it scrolls, never shows a bar, and fades at the edges.
 */
export function SidebarContent({ className, contentClassName, ...props }: SidebarContentProps) {
  const dense = React.useContext(SidebarSizeContext) === "sm";
  return (
    <ScrollArea
      data-slot="sidebar-content"
      scrollbar={false}
      className={cn("min-h-0 flex-1", className)}
      contentClassName={cn("flex flex-col px-3", dense ? "gap-5 py-2" : "gap-7 py-3", contentClassName)}
      {...props}
    />
  );
}

/** A hairline between the header, content and footer, inset 12px to line up with the rows. Inside `SidebarContent` pass `mx-0`. */
export function SidebarSeparator({ className, ...props }: SeparatorProps) {
  return (
    <Separator
      data-slot="sidebar-separator"
      className={cn("mx-3 my-1 data-[orientation=horizontal]:w-auto", className)}
      {...props}
    />
  );
}

/* ── Groups and rows ───────────────────────────────────────────────────────── */

/** A labelled group of rows. Pair it with a `SidebarGroupLabel`. */
export function SidebarGroup({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sidebar-group" className={cn("flex flex-col gap-0.5", className)} {...props} />;
}

/**
 * The eyebrow above a group: 12px mono, uppercase, 0.18em tracking. In the icon rail it fades
 * and folds away with the panel's width (a negative margin of its own fixed height), so the rows
 * glide up instead of jumping; screen readers still hear it.
 */
export function SidebarGroupLabel({ className, ...props }: React.ComponentProps<"div">) {
  const dense = React.useContext(SidebarSizeContext) === "sm";
  return (
    <div
      data-slot="sidebar-group-label"
      className={cn(
        "overflow-hidden eyebrow whitespace-nowrap transition-[margin,opacity] duration-(--duration) ease-brand",
        "group-data-[collapsible=icon]/sidebar:pointer-events-none group-data-[collapsible=icon]/sidebar:opacity-0",
        // Fixed heights (line + padding) so the fold is exact: 16 + 8 = 24px, dense 14 + 6 = 20px.
        dense
          ? "px-2.5 pb-1.5 text-[11px] leading-[14px] group-data-[collapsible=icon]/sidebar:-mt-5"
          : "px-3 pb-2 leading-4 group-data-[collapsible=icon]/sidebar:-mt-6",
        className,
      )}
      {...props}
    />
  );
}

export interface SidebarItemProps extends useRender.ComponentProps<"a"> {
  /** The current page. Sets `aria-current="page"` and the raised treatment. */
  current?: boolean;
  /**
   * In an icon-collapsed sidebar, the tooltip beside the 38px square. Defaults to the row's text.
   * `false` turns it off.
   */
  tooltip?: React.ReactNode;
}

function textOf(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return "";
}

/**
 * One navigation row. Renders an `<a>`; pass `render={<Link to="…" />}` for router links.
 * Leading icons are 16px; a trailing `Kbd`, `Tag` or `SidebarMenuBadge` is pushed to the end
 * with `ml-auto`. In an icon-collapsed app shell it shrinks to a 38px square with a tooltip.
 */
export function SidebarItem({
  current,
  tooltip,
  render,
  ref,
  className,
  children,
  onClick,
  ...props
}: SidebarItemProps) {
  const shell = React.useContext(SidebarContext);
  const surface = React.useContext(SidebarSurfaceContext);
  const dense = React.useContext(SidebarSizeContext) === "sm";
  const inShell = shell !== null;

  const element = useRender({
    defaultTagName: "a",
    render,
    ref,
    props: {
      "data-slot": "sidebar-item",
      "aria-current": current ? "page" : undefined,
      ...props,
      onClick: (event: React.MouseEvent<HTMLAnchorElement>) => {
        onClick?.(event);
        // A link chosen in the mobile sheet closes it.
        if (!event.defaultPrevented && surface === "sheet" && shell) shell.setOpenMobile(false);
      },
      className: cn(
        // cursor-pointer: rows may render as a <button> (theme switch, account menu), which has no pointer by default.
        "flex min-h-(--control-h) w-full cursor-pointer items-center rounded-button material-row font-body leading-none no-underline focus-ring select-none",
        dense ? "gap-2 px-2.5 text-[13px]" : "gap-2.5 px-3 text-sm",
        "aria-disabled:pointer-events-none aria-disabled:opacity-50",
        // Hover is quieter than current: the label brightens over the sidebar's gliding fill (HoverGlide),
        // the row itself takes no fill, edge or shadow. The raised row means "you are here".
        "hover:not-aria-[current=page]:bg-transparent! hover:not-aria-[current=page]:bg-none! hover:not-aria-[current=page]:shadow-none!",
        // While the current row glides (CurrentGlide), the old and new rows stay flat.
        "data-glide:transition-none! data-[glide=hidden]:bg-transparent! data-[glide=hidden]:bg-none! data-[glide=hidden]:shadow-none!",
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        inShell && "overflow-hidden whitespace-nowrap",
        inShell &&
          // A square in the icon rail with the 16px icon centred: (row - icon - border) / 2 a side.
          "group-data-[collapsible=icon]/sidebar:size-(--control-h) group-data-[collapsible=icon]/sidebar:px-[calc((var(--control-h)-18px)/2)]",
        className,
      ),
      children,
    },
  });

  if (!shell || tooltip === false) return element;

  const iconRail = !shell.isMobile && shell.state === "collapsed" && shell.collapsible === "icon";
  const label = tooltip ?? textOf(children);
  if (!label) return element;

  return (
    <Tooltip disabled={!iconRail}>
      <TooltipTrigger render={element} />
      <TooltipContent side="right" align="center" sideOffset={10}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

/** A count at the end of a row ("12"): mono, tabular, muted. Hidden in the icon rail. */
export function SidebarMenuBadge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="sidebar-menu-badge"
      className={cn(
        "ml-auto pl-2 font-mono text-xs leading-none text-muted-foreground tabular-nums group-data-[collapsible=icon]/sidebar:hidden",
        className,
      )}
      {...props}
    />
  );
}

/* ── Controls ──────────────────────────────────────────────────────────────── */

/**
 * The button that opens and closes the sidebar: a circular ghost icon button, usually first in
 * the inset's toolbar. On mobile it opens the sheet. Shows the ⌘B / Ctrl+B shortcut's action.
 */
export function SidebarTrigger({
  variant = "ghost",
  size = "icon",
  className,
  children,
  onClick,
  ...props
}: ButtonProps) {
  const { toggleSidebar, isMobile, open, openMobile } = useSidebar();
  return (
    <Button
      data-slot="sidebar-trigger"
      variant={variant}
      size={size}
      aria-label="Toggle sidebar"
      aria-expanded={isMobile ? openMobile : open}
      className={cn(
        "text-muted-foreground",
        // The circle pads its glyph by ~10px; pull what follows in, and give a separator after
        // it a little more room, so the gaps either side of the separator read the same.
        size === "icon" && "-mr-1.5 [&+[data-slot=separator]]:mr-1",
        size === "icon-sm" && "-mr-1 [&+[data-slot=separator]]:mr-0.75",
        className,
      )}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) toggleSidebar();
      }}
      {...props}
    >
      {children ?? <PanelLeftIcon aria-hidden />}
    </Button>
  );
}

/**
 * A thin strip along the sidebar's inner edge: hover shows the hairline in `--control-focus`,
 * click toggles the sidebar. A pointer convenience — keyboard users have the trigger and ⌘B.
 */
export function SidebarRail({ className, onClick, ...props }: React.ComponentProps<"button">) {
  const { toggleSidebar, state, isMobile, collapsible } = useSidebar();
  const surface = React.useContext(SidebarSurfaceContext);
  if (isMobile || surface === "sheet" || collapsible === "none") return null;
  return (
    <button
      type="button"
      data-slot="sidebar-rail"
      aria-label="Toggle sidebar"
      title="Toggle sidebar"
      tabIndex={-1}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) toggleSidebar();
      }}
      className={cn(
        "absolute inset-y-0 z-20 w-2 border-0 bg-transparent p-0 outline-none",
        "after:absolute after:inset-y-0 after:w-px after:bg-transparent after:transition-[background-color] after:duration-(--duration) after:ease-brand after:content-[''] hover:after:bg-control-focus",
        "group-data-[side=left]/sidebar:right-0 group-data-[side=right]/sidebar:left-0 group-data-[side=left]/sidebar:after:right-[-1px] group-data-[side=right]/sidebar:after:left-[-1px]",
        state === "expanded"
          ? "group-data-[side=left]/sidebar:cursor-w-resize group-data-[side=right]/sidebar:cursor-e-resize"
          : "group-data-[side=left]/sidebar:cursor-e-resize group-data-[side=right]/sidebar:cursor-w-resize",
        className,
      )}
      {...props}
    />
  );
}

/**
 * The main area beside an app-shell sidebar: a `<main>` column that takes the remaining width.
 * Put a toolbar row (with `SidebarTrigger`) first, then the page. Beside a `variant="inset"`
 * sidebar it becomes a raised 20px panel with an 8px margin (styles in parts/navigation.css).
 * Otherwise it has no fill of its own: the page ground shows through.
 */
export function SidebarInset({ className, ...props }: React.ComponentProps<"main">) {
  return (
    <main
      data-slot="sidebar-inset"
      className={cn("relative flex min-w-0 flex-1 flex-col", className)}
      {...props}
    />
  );
}
