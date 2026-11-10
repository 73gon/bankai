import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Columns2, HardDrive, LayoutGrid, RefreshCw, Rows3, Search } from 'lucide-react';
import { LibrarySplit } from '@/components/library-split';
import { AnimePoster } from '@/components/AnimePoster';
import { useRememberedOpen } from '@/lib/prefs';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Card } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';

/*
 * The parts the anime, Movies & Shows and manga libraries share: how the page is viewed, the title
 * open in the address, the header, the body that switches between grid, table and split, the poster
 * card, the title's own page and the counts at the bottom. Each library brings its own rows,
 * columns, cards' contents and title page; written three times, they had drifted apart (Movies &
 * Shows' view switch was three icon buttons, the others a segmented control) (Nov 2026).
 */

export type LibraryView = 'grid' | 'table' | 'split';
export type LibraryScope = 'anime' | 'mas' | 'manga';

/** "3.67 TiB", "1.24 GiB", or MiB under one. */
export function formatSize(bytes: number): string {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

// The split view needs the room; narrower, a title opens as its page.
const WIDE_QUERY = '(min-width: 64rem)';
function subscribeWide(onChange: () => void) {
  const query = window.matchMedia(WIDE_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

/** Whether the window has room for the split view. */
export function useWide(): boolean {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE_QUERY).matches, () => true);
}

function storedView(scope: LibraryScope): LibraryView {
  try {
    const saved = localStorage.getItem(`bankai.${scope}.library.view`);
    return saved === 'table' || saved === 'split' ? saved : 'grid';
  } catch {
    return 'grid';
  }
}

/** The library's view, kept in the browser; ``split`` only while the window has room for it. */
export function useLibraryView(scope: LibraryScope) {
  const [view, setView] = useState<LibraryView>(() => storedView(scope));
  const wide = useWide();
  useEffect(() => {
    try {
      localStorage.setItem(`bankai.${scope}.library.view`, view);
    } catch {
      /* a remembered view is a convenience, not state worth failing over */
    }
  }, [scope, view]);
  return { view, setView, wide, split: view === 'split' && wide };
}

/**
 * The title open in the library, in the address (``?show=<key>``, ``?movie=``, ``?series=``): Back
 * closes it, a title can be linked to. Opened from the list it is pushed, and closing it goes back
 * there; clicking through titles in the split view replaces it instead (no trail to walk back).
 * Opened by a link, closing replaces it with the list. The one open in the split view is opened
 * again on coming back to the page (``useRememberedOpen``).
 */
export function useLibraryAddress(scope: LibraryScope, params: string[], { split, ready = true }: { split: boolean; ready?: boolean }) {
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const found = params.map((param) => [param, search.get(param)] as const).find(([, key]) => key !== null);
  const current = found ? { param: found[0], key: found[1] as string } : null;

  function open(param: string, key: string) {
    if (current?.param === param && current.key === key) return;
    const next = new URLSearchParams(search);
    for (const other of params) next.delete(other);
    next.set(param, key);
    setSearch(next, { replace: split && current !== null, state: { fromList: true } });
  }

  function close() {
    if ((location.state as { fromList?: boolean } | null)?.fromList) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(search);
    for (const other of params) next.delete(other);
    setSearch(next, { replace: true });
  }

  // One parameter: the key alone is kept (what was kept before); several: "param:key".
  const remembered = current ? (params.length > 1 ? `${current.param}:${current.key}` : current.key) : null;
  useRememberedOpen(scope, remembered, split && ready, (value) => {
    const at = params.length > 1 ? value.indexOf(':') : -1;
    const param = at > 0 ? value.slice(0, at) : params[0];
    if (!params.includes(param)) return;
    const next = new URLSearchParams(search);
    next.set(param, at > 0 ? value.slice(at + 1) : value);
    setSearch(next, { replace: true });
  });

  return { current, open, close, search, setSearch, location };
}

/** The library's title, a line under it, the search field, the page's own actions and the view switch. */
export function LibraryHeader({
  title,
  subtitle,
  search,
  view,
  onView,
  wide,
  actions,
  onRescan,
  rescanning = false,
}: {
  title: string;
  subtitle?: ReactNode;
  search: { value: string; onChange: (value: string) => void; placeholder: string; label: string };
  view: LibraryView;
  onView: (view: LibraryView) => void;
  wide: boolean;
  /** Buttons of the library's own, before the view switch. */
  actions?: ReactNode;
  onRescan?: () => void;
  rescanning?: boolean;
}) {
  return (
    <div className='flex flex-wrap items-end justify-between gap-3'>
      <div className='flex min-w-0 flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>{title}</h1>
        {subtitle}
      </div>
      <div className='flex flex-wrap items-center gap-2'>
        <InputGroup className='w-60'>
          <InputGroupAddon align='inline-start'><Search /></InputGroupAddon>
          <InputGroupInput aria-label={search.label} placeholder={search.placeholder} value={search.value} onChange={(event) => search.onChange(event.target.value)} />
        </InputGroup>
        {actions}
        <ViewSwitch view={view} onView={onView} wide={wide} />
        {onRescan && (
          <Button variant='secondary' onClick={onRescan} disabled={rescanning}>
            <RefreshCw data-icon='inline-start' className={rescanning ? 'animate-spin' : ''} /> Rescan
          </Button>
        )}
      </div>
    </div>
  );
}

/** Grid, Table and (where there is room) Split. */
export function ViewSwitch({ view, onView, wide }: { view: LibraryView; onView: (view: LibraryView) => void; wide: boolean }) {
  const items = [['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table'], ...(wide ? [['split', Columns2, 'Split']] as const : [])] as const;
  return (
    <ToggleGroup aria-label='Library view' value={[view]} onValueChange={(values) => { if (values[0]) onView(values[0] as LibraryView); }}>
      {items.map(([value, Icon, label]) => (
        <ToggleGroupItem key={value} value={value} icon={<Icon />}>{label}</ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

/**
 * What the library shows under its header: a spinner while loading, an empty state, else the grid,
 * the table, or the table beside the open title (split). ``table`` is the same in the table and the
 * split view.
 */
export function LibraryBody({
  loading,
  count,
  empty,
  view,
  split,
  grid,
  table,
  detail,
  splitId,
  open,
  onClose,
  splitEmpty,
}: {
  loading: boolean;
  /** Rows shown: none gives the empty state. */
  count: number;
  empty: { title: string; description?: ReactNode; icon?: ReactNode };
  view: LibraryView;
  split: boolean;
  grid: () => ReactNode;
  table: () => ReactNode;
  detail: ReactNode;
  splitId: LibraryScope;
  open: boolean;
  onClose: () => void;
  splitEmpty: { title: string; description: string; icon?: ReactNode };
}) {
  if (loading) return <div className='flex min-h-40 items-center justify-center'><Spinner /></div>;
  if (count === 0) {
    return (
      <Empty>
        <EmptyMedia>{empty.icon ?? <HardDrive />}</EmptyMedia>
        <EmptyTitle>{empty.title}</EmptyTitle>
        {empty.description && <EmptyDescription>{empty.description}</EmptyDescription>}
      </Empty>
    );
  }
  if (view === 'grid') return <PosterGrid>{grid()}</PosterGrid>;
  if (split) {
    // The page's whole width, the open title beside the table; the divider is dragged, and where it
    // sits is kept for every device.
    return (
      <LibrarySplit id={splitId} list={table()} open={open} onClose={onClose} empty={splitEmpty}>
        {detail}
      </LibrarySplit>
    );
  }
  return <>{table()}</>;
}

/** The posters, as many to a row as there is room for. Paints the ground: opaque, it scrolls on the compositor (App.tsx). */
export function PosterGrid({ children }: { children: ReactNode }) {
  return (
    <div className='page-ground min-h-0 flex-1 overflow-y-auto'>
      <div className='grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>{children}</div>
    </div>
  );
}

/** A tag in a poster's corner: readable over any cover. */
export const CORNER_TAG = 'bg-background/85 backdrop-blur-sm';

/**
 * A title as a poster card: its cover with the title (and year) over it, tags in its corners, an
 * optional coloured rule under the cover (how complete it is), and a line of what it holds.
 */
export function PosterCard({
  cover,
  title,
  year,
  topLeft,
  topRight,
  bottomLeft,
  rule,
  children,
  onOpen,
  onIntent,
  onLeave,
}: {
  cover: string | null | undefined;
  title: string;
  year?: ReactNode;
  topLeft?: ReactNode;
  topRight?: ReactNode;
  bottomLeft?: ReactNode;
  /** The rule's colour class and what it means. */
  rule?: { className: string; label: string } | null;
  children?: ReactNode;
  onOpen: () => void;
  /** Pointer or focus on it: what will be opened can be fetched ahead. */
  onIntent?: () => void;
  onLeave?: () => void;
}) {
  return (
    <button
      type='button'
      onClick={onOpen}
      onPointerEnter={onIntent}
      onPointerLeave={onLeave}
      onFocus={onIntent}
      aria-label={'View ' + title}
      className='block w-full cursor-pointer rounded-card text-left focus-ring'
    >
      <Card className='poster-card h-full gap-0 overflow-hidden [--card-padding:0]'>
        <div className='relative block w-full'>
          <AnimePoster url={cover} title={title} />
          {topLeft && <span className='absolute left-2 top-2'>{topLeft}</span>}
          {topRight && <span className='absolute right-2 top-2'>{topRight}</span>}
          <span className='absolute inset-x-0 bottom-0 bg-linear-to-t from-black via-black/75 to-transparent px-3 pb-2.5 pt-10 text-left text-sm font-medium leading-tight text-white'>
            {bottomLeft && <span className='mb-1.5 block'>{bottomLeft}</span>}
            {title}
            {year ? <span className='ml-1.5 font-mono text-[0.68rem] font-normal tabular-nums text-white/70'>{year}</span> : null}
          </span>
        </div>
        {rule && <span className={cn('block h-0.5 w-full', rule.className)} title={rule.label} aria-hidden='true' />}
        {children && <div className='flex flex-col gap-2 p-3'>{children}</div>}
      </Card>
    </button>
  );
}

/** A title opened outside the split view: a page of its own, "← Library" above it. */
export function TitlePage({ onBack, children }: { onBack: () => void; children: ReactNode }) {
  return (
    <div className='flex min-h-0 flex-1 flex-col gap-6'>
      <div>
        <Button variant='ghost' size='sm' onClick={onBack}><ArrowLeft data-icon='inline-start' /> Library</Button>
      </div>
      {children}
    </div>
  );
}

/** The counts under a library: "245 series · 5,484 episodes · 3.67 TiB stored". */
export function LibraryFooter({ children }: { children: ReactNode }) {
  return (
    <footer className='flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-2.5 text-xs text-muted-foreground'>
      {children}
    </footer>
  );
}

/** One count of the footer: the number in mono, then what it counts. A dot class colours it. */
export function FooterStat({ value, children, dot }: { value: ReactNode; children: ReactNode; dot?: string }) {
  return (
    <span className='flex items-center gap-1.5'>
      {dot && <span className={cn('size-1.5 rounded-full', dot)} />}
      <span className='font-mono tabular-nums text-foreground'>{value}</span> {children}
    </span>
  );
}
