import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useGroupRef, type Layout } from 'react-resizable-panels';
import { HardDrive, X } from 'lucide-react';
import { adoptServerPref, localPref, saveSharedPref, serverPrefs } from '@/lib/prefs';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@zonsh/ui/resizable';

/** A saved divider: both panes' shares, in percent. */
function layoutOf(value: unknown): Layout | null {
  if (!value || typeof value !== 'object') return null;
  const { list, detail } = value as Record<string, unknown>;
  return typeof list === 'number' && typeof detail === 'number' && list > 0 && detail > 0 ? { list, detail } : null;
}

const same = (a: Layout, b: Layout) => Math.abs(a.list - b.list) < 0.5 && Math.abs(a.detail - b.detail) < 0.5;

/**
 * The height the page's scrolling area shows from the element's top down, less what follows it (the
 * page's bottom padding, a footer): the split fills the window below the page's header, and each pane
 * scrolls on its own instead of the page scrolling both. Measured again as the window or the header changes.
 */
/** How much room the content after an element takes, inside a scrolling area: the elements that follow
 *  it at every level up to the area (their heights and margins, and the gap of a flex or grid parent),
 *  and the paddings and borders closing each parent. By their sizes, not where they sit: a footer pinned
 *  to the page's bottom (Movies & Shows' counts, under a tab panel that fills the page) moved with
 *  nothing, so the split kept whatever height it first had -- half the screen. Elements beside it, and
 *  absolutely placed or hidden ones, take no room. */
function spaceBelow(element: HTMLElement, area: HTMLElement): number {
  let space = 0;
  let node: HTMLElement = element;
  while (node !== area && node.parentElement) {
    const parent: HTMLElement = node.parentElement;
    const parentStyle = getComputedStyle(parent);
    const gap = /flex|grid/.test(parentStyle.display) ? parseFloat(parentStyle.rowGap) || 0 : 0;
    const top = node.getBoundingClientRect().top;
    for (let next = node.nextElementSibling; next; next = next.nextElementSibling) {
      const style = getComputedStyle(next);
      if (style.position === 'absolute' || style.position === 'fixed' || next.getClientRects().length === 0) continue;
      const box = next.getBoundingClientRect();
      if (box.top <= top + 1) continue;
      space += gap + box.height + parseFloat(style.marginTop || '0') + parseFloat(style.marginBottom || '0');
    }
    node = parent;
    space += parseFloat(parentStyle.paddingBottom || '0') + (node === area ? 0 : parseFloat(parentStyle.borderBottomWidth || '0'));
  }
  return space;
}

function useFillHeight(ref: RefObject<HTMLElement | null>, min: number) {
  const [height, setHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    const area = element?.closest<HTMLElement>('[data-slot=sidebar-inset]');
    if (!element || !area) return;
    const measure = () => {
      // A kept page while hidden has no box: measured again when it shows.
      if (element.getClientRects().length === 0) return;
      const box = element.getBoundingClientRect();
      const top = box.top - area.getBoundingClientRect().top + area.scrollTop;
      // What sits below it, measured itself: the area's scrollHeight is no less than its height, so
      // on a page shorter than the screen it gave back the split's own height and it never grew.
      setHeight(Math.max(min, Math.floor(area.clientHeight - top - spaceBelow(element, area))));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(area);
    if (element.parentElement) observer.observe(element.parentElement);
    return () => observer.disconnect();
  }, [ref, min]);
  return height;
}

/**
 * A library's split view: its table on the left, the open title on the right,
 * the divider between them dragged. Where the divider sits is kept for every
 * device (`split.<id>` in the UI settings): this browser's copy places it at
 * once, the server's -- another device moved it -- follows when it arrives,
 * unless the divider was dragged here meanwhile.
 */
export function LibrarySplit({
  id,
  list,
  open,
  onClose,
  empty,
  className,
  children,
}: {
  id: string;
  list: ReactNode;
  /** Whether a title is open; without one the right pane says what to do. */
  open: boolean;
  onClose: () => void;
  empty: { title: string; description: string; icon?: ReactNode };
  className?: string;
  /** The open title. */
  children: ReactNode;
}) {
  const key = 'split.' + id;
  const groupRef = useGroupRef();
  const [initial] = useState(() => layoutOf(localPref(key)));
  const dragged = useRef(false);
  const box = useRef<HTMLDivElement>(null);
  const height = useFillHeight(box, 400);

  useEffect(() => {
    let alive = true;
    void serverPrefs().then((prefs) => {
      const saved = layoutOf(prefs[key]);
      const group = groupRef.current;
      if (!alive || !saved || dragged.current || !group) return;
      if (!same(saved, group.getLayout())) group.setLayout(saved);
      adoptServerPref(key, saved);
    });
    return () => { alive = false; };
  }, [key, groupRef]);

  return (
    // The group's box: the page's whole width, out past its gutters, and a height of its own. The
    // group sets width and height 100% inline, so it takes its size from here -- given as classes on
    // the group they lost, and the group grew with the longer pane while the page scrolled both.
    <div ref={box} className={cn('-mx-(--page-gutter) border-t border-border/60', className)} style={{ height: height ?? undefined }}>
      <ResizablePanelGroup
        groupRef={groupRef}
        defaultLayout={initial ?? undefined}
        onLayoutChanged={(layout, meta) => {
          if (!meta.isUserInteraction) return;
          dragged.current = true;
          const wanted = layoutOf(meta.requestedLayout) ?? layoutOf(layout);
          if (wanted) saveSharedPref(key, wanted);
        }}
      >
        {/* No gutter in the list's pane: its table ends at the pane's edges. The open title keeps the
            page's gutter, so its plain tables reach out to the pane's edges as on the title's own page. */}
        {/* Both panes paint the ground, as the app's scrolling area: opaque, each scrolls on the compositor (App.tsx). */}
        <ResizablePanel id='list' defaultSize='40%' minSize='18rem' className='page-ground [--page-gutter:0px]'>{list}</ResizablePanel>
        <ResizableHandle />
        <ResizablePanel id='detail' minSize='28rem'>
          <div className='page-ground flex h-full flex-col gap-6 overflow-y-auto px-(--page-gutter) pb-5 pt-3'>
            {open ? (
              <>
                <div className='flex justify-end'>
                  <Button variant='ghost' size='icon-sm' onClick={onClose} aria-label='Close'><X /></Button>
                </div>
                {children}
              </>
            ) : (
              <Empty>
                <EmptyMedia>{empty.icon ?? <HardDrive />}</EmptyMedia>
                <EmptyTitle>{empty.title}</EmptyTitle>
                <EmptyDescription>{empty.description}</EmptyDescription>
              </Empty>
            )}
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}
