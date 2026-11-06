import { Activity, memo, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Link, Navigate, Route, Routes, useLocation, type Location } from 'react-router-dom';
import { CalendarClock, Compass, Search as SearchIcon, ListVideo, HardDrive, Settings as SettingsIcon, Loader2, Download, ArrowUpCircle, RefreshCw, AlertCircle, ShieldAlert, Ban, ShieldCheck, LayoutDashboard, Subtitles, Languages, Shrink, BookOpen, Tags, Magnet } from 'lucide-react';
import { toast } from '@/lib/toast';
import { cn } from '@/zon/lib/cn';
import { api, pagePaths, recall, type UpdateStatus, type VpnStatus } from '@/lib/api';
import { Separator } from '@/zon/ui/separator';
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarItem, SidebarMenuBadge, SidebarProvider, SidebarRail, SidebarSeparator, SidebarTrigger, useSidebar } from '@/zon/ui/sidebar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/zon/ui/tooltip';
import { BankaiMark } from '@/components/bankai-mark';
import Dashboard from '@/pages/Dashboard';
import Discover from '@/pages/Discover';
import Search from '@/pages/Search';
import Library from '@/pages/Library';
import Server from '@/pages/Server';
import MasLibrary from '@/pages/MasLibrary';
import Torrents from '@/pages/Torrents';
import Settings from '@/pages/Settings';
import Anime from '@/pages/Anime';
import AnimeQueue from '@/pages/AnimeQueue';
import AnimeLibrary from '@/pages/AnimeLibrary';
import AnimeSettings from '@/pages/AnimeSettings';
import AnimeReview from '@/pages/AnimeReview';
import AnimeErai from '@/pages/AnimeErai';
import Recent from '@/pages/Recent';
import QBittorrent from '@/pages/QBittorrent';
import Tracks from '@/pages/Tracks';
import Encode from '@/pages/Encode';
import Genres from '@/pages/Genres';
import MangaLibrary from '@/pages/MangaLibrary';
import MangaReview from '@/pages/MangaReview';
import MangaSearch from '@/pages/MangaSearch';
import MangaSettings from '@/pages/MangaSettings';

// Two libraries live here, so the paths say which one they mean: /mas for
// movies and shows, /a for anime. `count` names a key of /api/sidebar/counts.
const MAS_NAV = [
  { to: '/mas/discover', label: 'Discover', icon: Compass },
  { to: '/mas/search', label: 'Search', icon: SearchIcon },
  { to: '/mas/filmpalast', label: 'Filmpalast', icon: CalendarClock },
  { to: '/mas/torrents', label: 'Torrents', icon: Magnet },
  { to: '/mas/queue', label: 'Queue', icon: ListVideo, count: 'mas_queue' },
  { to: '/mas/library', label: 'Library', icon: HardDrive },
  { to: '/mas/settings', label: 'Settings', icon: SettingsIcon },
];

const ANIME_NAV = [
  { to: '/a/search', label: 'Search', icon: SearchIcon },
  { to: '/a/queue', label: 'Queue', icon: ListVideo, count: 'anime_queue' },
  { to: '/a/library', label: 'Library', icon: HardDrive },
  { to: '/a/review', label: 'Review', icon: ShieldAlert, count: 'anime_review' },
  { to: '/a/blacklist', label: 'Blacklist', icon: Ban, count: 'anime_blacklist' },
  { to: '/a/erai', label: 'Erai-raws', icon: Subtitles },
  { to: '/a/settings', label: 'Settings', icon: SettingsIcon },
];

// Manga and light novels: found on nyaa, reviewed, filed for OpenPanel.
const MANGA_NAV = [
  { to: '/m/search', label: 'Search', icon: SearchIcon },
  { to: '/m/library', label: 'Library', icon: BookOpen },
  { to: '/m/review', label: 'Review', icon: ShieldAlert, count: 'manga_review' },
  { to: '/m/blacklist', label: 'Blacklist', icon: Ban },
  { to: '/m/settings', label: 'Settings', icon: SettingsIcon },
];

// Neither library's own: the torrent client and the machine behind both.
const SYSTEM_NAV = [
  { to: '/qbittorrent', label: 'qBittorrent', icon: Download, count: 'qbittorrent' },
  { to: '/library', label: 'Library', icon: HardDrive },
  { to: '/tracks', label: 'Tracks', icon: Languages },
  { to: '/encode', label: 'Encode', icon: Shrink },
  { to: '/genres', label: 'Genres', icon: Tags },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

// Above both libraries: what the whole machine is doing.
const TOP_NAV: Array<{ to: string; label: string; icon: typeof LayoutDashboard; count?: string }> = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
];

const NAV_GROUPS = [
  { label: '', items: TOP_NAV },
  { label: 'Movies & Shows', items: MAS_NAV },
  { label: 'Anime', items: ANIME_NAV },
  { label: 'Manga', items: MANGA_NAV },
  { label: '', items: SYSTEM_NAV },
];

/** Badge counts for the whole sidebar, from one endpoint on one timer.
 *
 *  A badge per endpoint would mean several reads of a large state file every
 *  tick, on every page. A count that fails to arrive simply has no badge.
 */
/**
 * Fetch each main tab's answer once, a few seconds after the app opens, so the
 * first visit to a tab this session is as quick as a return to it. One at a
 * time and only once: the server keeps these ready, so each is a cheap read.
 */
function usePrefetchTabs(): void {
  useEffect(() => {
    let alive = true;
    const timer = window.setTimeout(async () => {
      const tabs: Array<() => Promise<unknown>> = [
        () => api.animeLibrary(),
        () => api.animeQueue(0, 100, false, { q: '', status: 'all' }),
        () => api.animeReview(),
        () => api.animeBlacklist(),
        () => api.qbittorrentTorrents(),
        () => api.titles(),
        () => api.masLibrary(true),
      ];
      for (const fetchTab of tabs) {
        if (!alive) return;
        try {
          await fetchTab();
        } catch {
          /* a tab that cannot be fetched now simply loads when opened */
        }
      }
    }, 3_000);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);
}

function useSidebarCounts(): Record<string, number | null | undefined> {
  const [counts, setCounts] = useState<Record<string, number | null>>(() => recall<{ counts: Record<string, number | null> }>(pagePaths.sidebarCounts)?.counts ?? {});
  useEffect(() => {
    let alive = true;
    let timer = 0;
    // The next ask waits for the last answer. A fixed interval kept firing
    // while a slow server was still working, stacking dozens of requests
    // that each made it slower.
    async function tick() {
      try {
        const result = await api.sidebarCounts();
        if (alive) setCounts(result.counts);
      } catch {
        /* the row simply shows no numbers */
      }
      if (alive) timer = window.setTimeout(() => void tick(), 15_000);
    }
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);
  return counts;
}
// The old key said collapsed as '1'; zon remembers open as 'true' / 'false'
// under its own key, and reads the old one only until it has written that.
const SIDEBAR_KEY = 'bankai:sidebar';
const OLD_SIDEBAR_KEY = 'bankai:sidebar-collapsed';

function startsOpen(): boolean {
  try {
    return localStorage.getItem(OLD_SIDEBAR_KEY) !== '1';
  } catch {
    return true;
  }
}

function BrandMark() {
  return (
    <div className='flex items-center gap-1.5'>
      <BankaiMark className='size-5 shrink-0 text-brand' aria-hidden='true' />
      <span className='font-mono text-sm font-semibold tracking-tight text-foreground'>bankai</span>
    </div>
  );
}

/** Brand and toggle; in the icon rail the toggle alone, where the brand would not fit. */
function BankaiSidebarHeader() {
  const { state, isMobile } = useSidebar();
  const rail = state === 'collapsed' && !isMobile;
  return (
    <SidebarHeader>
      <div className={cn('flex min-h-(--control-h) items-center overflow-hidden whitespace-nowrap', rail ? 'justify-center' : 'justify-between pl-[9px]')}>
        {!rail && <BrandMark />}
        {!isMobile && <SidebarTrigger size='icon-sm' className='mr-0' aria-label={rail ? 'Expand sidebar' : 'Collapse sidebar'} />}
      </div>
    </SidebarHeader>
  );
}

function UpdateSidebarStatus() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const wasUpdating = useRef(false);
  const updating = status != null && ['waiting', 'applying', 'restarting'].includes(status.phase);

  function accept(next: UpdateStatus) {
    const active = ['waiting', 'applying', 'restarting'].includes(next.phase);
    if (wasUpdating.current && next.phase === 'done') {
      window.location.reload();
      return;
    }
    wasUpdating.current = active;
    setStatus(next);
  }

  useEffect(() => {
    void api.updateStatus().then(accept).catch(() => {});
    const timer = window.setInterval(() => {
      void api.updateStatus().then(accept).catch(() => {});
    }, updating ? 5000 : 60000);
    return () => window.clearInterval(timer);
  }, [updating]);

  async function click() {
    setBusy(true);
    try {
      if (status?.available || (status?.phase === 'failed' && status.supported)) {
        accept(await api.applyUpdate());
        toast.success('Update requested. Running work will be preserved.');
      } else {
        const next = await api.checkUpdate();
        accept(next);
        if (next.error) toast.error(next.error);
        else if (next.available) toast.success(next.commits_behind + ' new commits available');
        else toast.success('Bankai is up to date');
      }
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  const label = updating
    ? status?.phase === 'waiting' ? 'Preparing update' : 'Updating…'
    : status?.available ? 'Update available'
    : status?.phase === 'failed' ? 'Retry update'
    : status?.checking ? 'Checking updates…' : 'Check for updates';
  const detail = status?.error || status?.unavailable_reason || (status?.available
    ? status.commits_behind + ' new commits. Click to update without interrupting independent workers.'
    : status?.detail || 'Check for new Bankai commits');
  const icon = busy || updating || status?.checking
    ? <Loader2 className='animate-spin' />
    : status?.available ? <ArrowUpCircle className='text-success' />
    : status?.phase === 'failed' ? <AlertCircle className='text-warning' />
    : <RefreshCw />;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <SidebarItem render={<button type='button' />} tooltip={false}
            onClick={() => void click()} aria-disabled={busy || updating || status?.checking || undefined} aria-label={label} />
        }
      >
        {icon}<span className={status?.available ? 'text-success' : undefined}>{label}</span>
      </TooltipTrigger>
      <TooltipContent side='right' variant='text' className='max-w-72'>{detail}</TooltipContent>
    </Tooltip>
  );
}

function VpnSidebarStatus() {
  const [status, setStatus] = useState<VpnStatus | null>(null);
  const [connecting, setConnecting] = useState(false);

  async function refresh() {
    try {
      setStatus(await api.vpnStatus());
    } catch (error: any) {
      setStatus({ connected: false, status: 'unavailable', detail: error.message });
    }
  }

  useEffect(() => {
    let alive = true;
    let timer = 0;
    // Chained like the sidebar counts, so a slow answer never piles up.
    async function tick() {
      await refresh();
      if (alive) timer = window.setTimeout(() => void tick(), 30_000);
    }
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  async function connect() {
    setConnecting(true);
    try {
      const next = await api.vpnConnect();
      setStatus(next);
      if (next.connected) toast.success('VPN connected.');
      else toast.error('The VPN did not report a connected state.');
    } catch (error: any) {
      toast.error(error.message);
      await refresh();
    } finally {
      setConnecting(false);
    }
  }

  const connected = status?.connected === true;
  const disconnected = status !== null && !connected;
  const statusLabel = connected ? 'VPN connected' : status?.status === 'unavailable' ? 'VPN status unavailable' : 'VPN disconnected';
  const dot = (
    <span
      aria-hidden='true'
      className={cn(
        'size-1.5 shrink-0 rounded-full',
        connected ? 'bg-success text-success' : disconnected ? 'bg-destructive text-destructive' : 'bg-muted-foreground text-muted-foreground',
      )}
    />
  );

  // A row like the update one above it. Connected it re-checks; disconnected
  // it connects.
  return (
    <SidebarItem
      render={<button type='button' />}
      onClick={() => void (disconnected ? connect() : refresh())}
      aria-disabled={connecting || undefined}
      tooltip={disconnected ? `${statusLabel}: click to connect` : statusLabel}
      title={status?.detail || statusLabel}
    >
      {connecting ? <Loader2 className='animate-spin' aria-hidden='true' /> : <ShieldCheck aria-hidden='true' />}
      <span>{connected ? 'VPN connected' : disconnected ? 'Connect VPN' : 'VPN'}</span>
      <span className='ml-auto flex items-center group-data-[collapsible=icon]/sidebar:hidden'>{dot}</span>
    </SidebarItem>
  );
}

// Every page, by its address. Two addresses can show one component (Review and
// Blacklist): keyed apart, each keeps its own state.
const PAGES: Record<string, ReactNode> = {
  '/dashboard': <Dashboard />,
  '/mas/discover': <Discover />,
  '/mas/search': <Search />,
  '/mas/filmpalast': <Recent />,
  '/mas/torrents': <Torrents />,
  '/mas/queue': <Library />,
  '/mas/library': <MasLibrary />,
  '/mas/settings': <Settings scope='mas' />,
  '/a/search': <Anime />,
  '/a/queue': <AnimeQueue />,
  '/a/library': <AnimeLibrary />,
  '/a/review': <AnimeReview key='review' />,
  '/a/blacklist': <AnimeReview key='blacklist' blacklist />,
  '/a/erai': <AnimeErai />,
  '/a/settings': <AnimeSettings />,
  '/m/search': <MangaSearch />,
  '/m/library': <MangaLibrary />,
  '/m/review': <MangaReview key='review' />,
  '/m/blacklist': <MangaReview key='blacklist' blacklist />,
  '/m/settings': <MangaSettings />,
  '/qbittorrent': <QBittorrent />,
  '/tracks': <Tracks />,
  '/encode': <Encode />,
  '/genres': <Genres />,
  '/library': <Server />,
  '/settings': <Settings scope='global' />,
};

// Where the sections and the old addresses lead. Kept so a bookmark, an open
// tab or a link in a note still arrives somewhere.
const REDIRECTS: Record<string, string> = {
  '/': '/dashboard',
  '/mas': '/mas/discover',
  '/a': '/a/search',
  '/a/discover': '/a/search',
  '/m': '/m/library',
  '/discover': '/mas/discover',
  '/search': '/mas/search',
  '/filmpalast': '/mas/filmpalast',
  '/recent': '/mas/filmpalast',
  '/queue': '/mas/queue',
  '/server': '/library',
  '/anime': '/a/search',
  '/anime/discover': '/a/search',
  '/anime/queue': '/a/queue',
  '/anime/library': '/a/library',
  '/anime/review': '/a/review',
  '/anime/blacklist': '/a/blacklist',
  '/anime/settings': '/a/settings',
};

/**
 * One kept page. Memoised: a hidden page renders again only when its own address
 * or visibility changes -- a fresh location object for every page on every click
 * re-rendered all of them each time.
 */
const KeptPage = memo(function KeptPage({ path, at, visible }: { path: string; at: Location; visible: boolean }) {
  return (
    <Activity mode={visible ? 'visible' : 'hidden'}>
      <KeptRoutes path={path} at={at} />
    </Activity>
  );
});

/** Its router, apart from the Activity: showing or hiding a page leaves the page as it is
 *  (`<Routes location>` hands the page a new context object whenever it renders). */
const KeptRoutes = memo(function KeptRoutes({ path, at }: { path: string; at: Location }) {
  return (
    <Routes location={at}>
      <Route path={path} element={PAGES[path]} />
    </Routes>
  );
});

/**
 * The pages, each kept once visited: leaving one hides it (React's Activity)
 * instead of taking it down, so coming back is instant -- its state, its data,
 * its scroll position as they were -- while its effects (timers, polling,
 * fetches) rest until it shows again, when they run and refresh it.
 *
 * A hidden page reads the address it was last shown at, not the one shown now:
 * the anime library must not see the Movies & Shows library's ?show=.
 */
function KeptPages() {
  const location = useLocation();
  const active = location.pathname in PAGES ? location.pathname : null;
  // Each visited page and the address it was last shown at, in the order first visited.
  const shown = useRef(new Map<string, Location>());
  if (active) {
    // Back at the same address, the page keeps the location it had: nothing of it renders again.
    const before = shown.current.get(active);
    if (!before || before.search !== location.search || before.hash !== location.hash || before.state !== location.state) {
      shown.current.set(active, location);
    }
  }
  // Each page's own scroll position: the pages share one scrolling area.
  const scrolls = useRef(new Map<string, number>());
  const previous = useRef<string | null>(null);
  useLayoutEffect(() => {
    const area = document.querySelector<HTMLElement>('[data-slot=sidebar-inset]');
    if (!area || previous.current === active) return;
    if (previous.current) scrolls.current.set(previous.current, area.scrollTop);
    area.scrollTop = active ? scrolls.current.get(active) ?? 0 : 0;
    previous.current = active;
  }, [active]);

  // How many pages are built, for a look from the browser's console.
  useEffect(() => {
    const count = String(shown.current.size);
    if (document.documentElement.dataset.keptPages !== count) document.documentElement.dataset.keptPages = count;
  });

  return (
    <>
      {[...shown.current].map(([path, at]) => (
        <KeptPage key={path} path={path} at={at} visible={path === active} />
      ))}
      {!active && (
        <Routes>
          {Object.entries(REDIRECTS).map(([from, to]) => <Route key={from} path={from} element={<Navigate to={to} replace />} />)}
          <Route path='*' element={<Navigate to='/dashboard' replace />} />
        </Routes>
      )}
    </>
  );
}

export default function App() {
  const counts = useSidebarCounts();
  const { pathname } = useLocation();
  usePrefetchTabs();
  useEffect(() => {
    const pointer = () => { document.documentElement.dataset.inputMethod = 'pointer'; };
    const keyboard = () => { document.documentElement.dataset.inputMethod = 'keyboard'; };
    document.addEventListener('pointerdown', pointer, true);
    document.addEventListener('keydown', keyboard, true);
    return () => {
      document.removeEventListener('pointerdown', pointer, true);
      document.removeEventListener('keydown', keyboard, true);
    };
  }, []);

  return (
    // The page scrolls inside the inset, not the window: sticky bars in the
    // pages (the queue's selection bar) are sticky to it.
    <SidebarProvider collapsible='icon' storageKey={SIDEBAR_KEY} defaultOpen={startsOpen()} className='h-svh min-h-0 overflow-hidden'>
      <Sidebar aria-label='Main navigation' size='sm'>
        <BankaiSidebarHeader />
        <SidebarSeparator />
        <SidebarContent>
          {NAV_GROUPS.map((group, groupIndex) => (
            <SidebarGroup key={group.label || groupIndex}>
              {group.label && <SidebarGroupLabel>{group.label}</SidebarGroupLabel>}
              {group.items.map(({ to, label, icon: Icon, count }) => {
                // Zero is the resting state of most of these, and a row of
                // noughts reads as clutter rather than as information.
                const badge = count ? counts[count] : undefined;
                const shown = typeof badge === 'number' && badge > 0;
                return (
                  <SidebarItem
                    key={to}
                    render={<Link to={to} />}
                    current={pathname === to || pathname.startsWith(to + '/')}
                    tooltip={`${group.label ? `${group.label}: ` : ''}${label}${shown ? ` (${badge})` : ''}`}
                  >
                    <Icon strokeWidth={1.65} aria-hidden='true' />
                    {label}
                    {shown && <SidebarMenuBadge>{badge > 999 ? '999+' : badge}</SidebarMenuBadge>}
                  </SidebarItem>
                );
              })}
            </SidebarGroup>
          ))}
        </SidebarContent>
        <SidebarSeparator />
        <SidebarFooter>
          <UpdateSidebarStatus />
          <VpnSidebarStatus />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>

      {/* Opaque, with zon's ground painted in (page-ground, lib/ground.ts): on a screen at 100%
          scaling Chrome scrolls a see-through scroller by painting all of it again every frame
          and blending the grain anew (20-30 fps on the manga review), an opaque one on the
          compositor (60). Scrollers inside it stay see-through, showing this ground. */}
      <SidebarInset className='overflow-y-auto page-ground'>
        {/* Mobile: the sidebar is a sheet, opened from this bar. */}
        <header className='sticky top-0 z-30 flex shrink-0 items-center gap-2 border-b border-border/60 bg-background px-2 py-1.5 md:hidden'>
          <SidebarTrigger />
          <Separator orientation='vertical' className='my-auto h-4 min-h-0 self-center' />
          <BrandMark />
        </header>
        <div className='min-h-0 w-full flex-1 px-(--page-gutter) py-6 [--page-gutter:1rem] md:[--page-gutter:1.5rem]'>
          <div className='h-full w-full'>
            <KeptPages />
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
