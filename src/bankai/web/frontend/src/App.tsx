import { useEffect, useRef, useState } from 'react';
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { CalendarClock, Compass, Search as SearchIcon, ListVideo, HardDrive, Settings as SettingsIcon, PanelLeft, PanelLeftClose, Sparkles, Loader2, Download, ArrowUpCircle, RefreshCw, AlertCircle, ShieldAlert, Ban, Clapperboard, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { api, type UpdateStatus, type VpnStatus } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import Discover from '@/pages/Discover';
import Search from '@/pages/Search';
import Library from '@/pages/Library';
import Server from '@/pages/Server';
import MasLibrary from '@/pages/MasLibrary';
import Settings from '@/pages/Settings';
import Anime from '@/pages/Anime';
import AnimeQueue from '@/pages/AnimeQueue';
import AnimeLibrary from '@/pages/AnimeLibrary';
import AnimeSettings from '@/pages/AnimeSettings';
import AnimeReview from '@/pages/AnimeReview';
import Recent from '@/pages/Recent';
import QBittorrent from '@/pages/QBittorrent';

// Two libraries live here, so the paths say which one they mean: /mas for
// movies and shows, /a for anime. `count` names a key of /api/sidebar/counts.
const MAS_NAV = [
  { to: '/mas/discover', label: 'Discover', icon: Compass },
  { to: '/mas/search', label: 'Search', icon: SearchIcon },
  { to: '/mas/filmpalast', label: 'Filmpalast', icon: CalendarClock },
  { to: '/mas/queue', label: 'Queue', icon: ListVideo, count: 'mas_queue' },
  { to: '/mas/library', label: 'Library', icon: HardDrive },
  { to: '/mas/settings', label: 'Settings', icon: SettingsIcon },
];

const ANIME_NAV = [
  { to: '/a/discover', label: 'Discover', icon: Sparkles },
  { to: '/a/queue', label: 'Queue', icon: ListVideo, count: 'anime_queue' },
  { to: '/a/library', label: 'Library', icon: HardDrive },
  { to: '/a/review', label: 'Review', icon: ShieldAlert, count: 'anime_review' },
  { to: '/a/blacklist', label: 'Blacklist', icon: Ban, count: 'anime_blacklist' },
  { to: '/a/settings', label: 'Settings', icon: SettingsIcon },
];

// Neither library's own: the torrent client and the machine behind both.
const SYSTEM_NAV = [
  { to: '/qbittorrent', label: 'qBittorrent', icon: Download, count: 'qbittorrent' },
  { to: '/library', label: 'Library', icon: HardDrive },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

const NAV_GROUPS = [
  { label: 'Movies & Shows', items: MAS_NAV },
  { label: 'Anime', items: ANIME_NAV },
  { label: '', items: SYSTEM_NAV },
];

/** Badge counts for the whole sidebar, from one endpoint on one timer.
 *
 *  A badge per endpoint would mean several reads of a large state file every
 *  tick, on every page. A count that fails to arrive simply has no badge.
 */
function useSidebarCounts(): Record<string, number | null | undefined> {
  const [counts, setCounts] = useState<Record<string, number | null>>({});
  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const result = await api.sidebarCounts();
        if (alive) setCounts(result.counts);
      } catch {
        /* the row simply shows no numbers */
      }
    }
    void tick();
    const timer = window.setInterval(() => void tick(), 15_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  return counts;
}
const SIDEBAR_KEY = 'bankai:sidebar-collapsed';

function useSidebarState() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === '1';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore */
    }
  }, [collapsed]);
  return [collapsed, setCollapsed] as const;
}

function BrandMark() {
  return (
    <div className='flex items-center gap-2.5'>
      <span className='raised flex size-7 shrink-0 items-center justify-center rounded-lg text-foreground' aria-hidden='true'>
        <Clapperboard className='size-4' strokeWidth={1.7} />
      </span>
      <span className='font-mono text-sm font-semibold tracking-tight text-foreground'>bankai</span>
    </div>
  );
}

function UpdateSidebarStatus({ collapsed }: { collapsed: boolean }) {
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
    ? <Loader2 data-icon='inline-start' className='animate-spin' />
    : status?.available ? <ArrowUpCircle data-icon='inline-start' className='text-success' />
    : status?.phase === 'failed' ? <AlertCircle data-icon='inline-start' className='text-warning' />
    : <RefreshCw data-icon='inline-start' />;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant='secondary' size={collapsed ? 'icon' : 'default'} className={cn(!collapsed && 'w-full justify-start')}
          style={status?.available ? { borderColor: 'var(--success)' } : undefined}
          onClick={() => void click()} disabled={busy || updating || status?.checking} aria-label={label}>
          {icon}{!collapsed && <span className={status?.available ? 'text-success' : undefined}>{label}</span>}
        </Button>
      </TooltipTrigger>
      <TooltipContent side='right' className='max-w-72'>{detail}</TooltipContent>
    </Tooltip>
  );
}

function VpnSidebarStatus({ collapsed }: { collapsed: boolean }) {
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
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  async function connect() {
    setConnecting(true);
    try {
      const next = await api.vpnConnect();
      setStatus(next);
      if (next.connected) toast.success('NordVPN connected on laptop.');
      else toast.error('NordVPN did not report a connected state.');
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

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          {disconnected ? (
            <Button size='icon' variant='ghost' onClick={() => void connect()} disabled={connecting} aria-label={`${statusLabel}. Connect NordVPN`}>
              {connecting ? <Loader2 data-icon='inline-start' className='animate-spin' /> : dot}
            </Button>
          ) : (
            <div className='flex size-10 items-center justify-center' aria-label={statusLabel}>{dot}</div>
          )}
        </TooltipTrigger>
        <TooltipContent side='right'>{disconnected ? `${statusLabel} — click to connect` : statusLabel}</TooltipContent>
      </Tooltip>
    );
  }

  // A control, like the update button above it, rather than a line of text
  // with a button sometimes stuck on the end. Connected it re-checks;
  // disconnected it connects.
  return (
    <Button
      variant='secondary'
      className='w-full justify-start'
      onClick={() => void (disconnected ? connect() : refresh())}
      disabled={connecting}
      title={status?.detail || statusLabel}
    >
      {connecting ? (
        <Loader2 className='animate-spin' aria-hidden='true' />
      ) : (
        <ShieldCheck aria-hidden='true' />
      )}
      <span>{connected ? 'VPN connected' : disconnected ? 'Connect VPN' : 'VPN'}</span>
      <span className='ml-auto flex items-center'>{dot}</span>
    </Button>
  );
}

export default function App() {
  const [collapsed, setCollapsed] = useSidebarState();
  const counts = useSidebarCounts();
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
    <TooltipProvider delayDuration={350} skipDelayDuration={400}>
      <div className='flex min-h-screen flex-col md:h-screen md:min-h-0 md:flex-row md:overflow-hidden'>
        {/* Sidebar (desktop) / top bar (mobile) */}
        <aside
          className={cn(
            'sticky top-0 z-30 flex min-w-0 shrink-0 flex-row items-center gap-3 border-b border-sidebar-border bg-sidebar px-3 py-2',
            'md:h-dvh md:flex-col md:items-stretch md:gap-0 md:border-b-0 md:border-r md:p-0',
            collapsed ? 'md:w-16' : 'md:w-60',
          )}
        >
          {/* Header row: brand + collapse toggle (desktop) */}
          <div className={cn('hidden h-16 shrink-0 md:flex md:items-center', collapsed ? 'md:justify-center' : 'md:justify-between md:px-4')}>
            {collapsed ? (
              <Button
                variant='ghost'
                size='icon'
                onClick={() => setCollapsed(false)}
                aria-label='Expand sidebar'
              >
                <PanelLeft data-icon='inline-start' />
              </Button>
            ) : (
              <>
                <BrandMark />
                <Button
                  variant='ghost'
                  size='icon'
                  onClick={() => setCollapsed(true)}
                  aria-label='Collapse sidebar'
                >
                  <PanelLeftClose data-icon='inline-start' />
                </Button>
              </>
            )}
          </div>

          {/* Brand (mobile top bar) */}
          <div className='flex items-center px-1 md:hidden'>
            <BrandMark />
          </div>

          <Separator className='hidden md:block' />

          <nav aria-label='Main navigation' className={cn('flex min-h-0 min-w-0 flex-1 flex-row gap-1 overflow-x-auto py-1 md:flex-col md:gap-0 md:overflow-y-auto md:overflow-x-hidden md:py-3', collapsed && 'md:items-center')}>
            {NAV_GROUPS.map((group, groupIndex) => (
              <div
                key={group.label}
                className={cn(
                  'contents md:flex md:w-full md:flex-col md:gap-0.5 md:px-3',
                  collapsed && 'md:items-center md:px-2',
                )}
              >
                {groupIndex > 0 && <Separator className='my-3 hidden md:block' />}
                {!collapsed && group.label && (
                  <p className='hidden px-2 pb-2 pt-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground md:block'>
                    {group.label}
                  </p>
                )}
                {group.items.map(({ to, label, icon: Icon, count }) => {
                  // Zero is the resting state of most of these, and a row of
                  // noughts reads as clutter rather than as information.
                  const badge = count ? counts[count] : undefined;
                  const link = (
                    <NavLink
                      key={to}
                      to={to}
                      className={() =>
                        cn(
                          'sidebar-link group flex shrink-0 items-center gap-2 whitespace-nowrap px-2.5 text-[13px] font-medium',
                          collapsed && 'md:size-9 md:justify-center md:gap-0 md:p-0',
                        )
                      }
                    >
                      <Icon className='size-4 shrink-0' strokeWidth={1.65} aria-hidden='true' />
                      <span className={cn('md:inline', collapsed && 'md:hidden')}>{label}</span>
                      {typeof badge === 'number' && badge > 0 && (
                        <span
                          className={cn(
                            'ml-auto font-mono text-[11px] leading-none tabular-nums text-muted-foreground',
                            collapsed && 'md:hidden',
                          )}
                        >
                          {badge > 999 ? '999+' : badge}
                        </span>
                      )}
                    </NavLink>
                  );
                  return collapsed ? (
                    <Tooltip key={to}>
                      <TooltipTrigger asChild>{link}</TooltipTrigger>
                      <TooltipContent side='right'>
                        {group.label ? `${group.label}: ${label}` : label}
                        {typeof badge === 'number' && badge > 0 ? ` (${badge})` : ''}
                      </TooltipContent>
                    </Tooltip>
                  ) : link;
                })}
              </div>
            ))}
          </nav>

          <Separator className='hidden md:block' />
          <div className={cn('hidden p-3 md:flex md:shrink-0 md:flex-col md:gap-2', collapsed && 'md:items-center md:px-2')}>
            <UpdateSidebarStatus collapsed={collapsed} />
            <VpnSidebarStatus collapsed={collapsed} />
          </div>
        </aside>

        <main className='min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-6 md:py-6'>
          <div className='h-full w-full animate-fade-in'>
            <Routes>
              <Route path='/' element={<Navigate to='/mas/discover' replace />} />

              {/* Movies & shows. */}
              <Route path='/mas' element={<Navigate to='/mas/discover' replace />} />
              <Route path='/mas/discover' element={<Discover />} />
              <Route path='/mas/search' element={<Search />} />
              <Route path='/mas/filmpalast' element={<Recent />} />
              <Route path='/mas/queue' element={<Library />} />
              <Route path='/mas/library' element={<MasLibrary />} />
              <Route path='/mas/settings' element={<Settings scope='mas' />} />

              {/* Anime. */}
              <Route path='/a' element={<Navigate to='/a/discover' replace />} />
              <Route path='/a/discover' element={<Anime />} />
              <Route path='/a/queue' element={<AnimeQueue />} />
              <Route path='/a/library' element={<AnimeLibrary />} />
              {/* Keyed apart: both render AnimeReview, so without this React
                  reuses the instance and the open releases dialog, the busy
                  flag and the purge target all follow you between them. */}
              <Route path='/a/review' element={<AnimeReview key='review' />} />
              <Route path='/a/blacklist' element={<AnimeReview key='blacklist' blacklist />} />
              <Route path='/a/settings' element={<AnimeSettings />} />

              {/* Neither library's own. */}
              <Route path='/qbittorrent' element={<QBittorrent />} />
              <Route path='/library' element={<Server />} />
              <Route path='/settings' element={<Settings scope='global' />} />

              {/* The paths these pages used to live at. Kept so a bookmark,
                  an open tab or a link in a note still arrives somewhere. */}
              <Route path='/discover' element={<Navigate to='/mas/discover' replace />} />
              <Route path='/search' element={<Navigate to='/mas/search' replace />} />
              <Route path='/filmpalast' element={<Navigate to='/mas/filmpalast' replace />} />
              <Route path='/recent' element={<Navigate to='/mas/filmpalast' replace />} />
              <Route path='/queue' element={<Navigate to='/mas/queue' replace />} />
              <Route path='/server' element={<Navigate to='/library' replace />} />
              <Route path='/anime' element={<Navigate to='/a/discover' replace />} />
              <Route path='/anime/discover' element={<Navigate to='/a/discover' replace />} />
              <Route path='/anime/queue' element={<Navigate to='/a/queue' replace />} />
              <Route path='/anime/library' element={<Navigate to='/a/library' replace />} />
              <Route path='/anime/review' element={<Navigate to='/a/review' replace />} />
              <Route path='/anime/blacklist' element={<Navigate to='/a/blacklist' replace />} />
              <Route path='/anime/settings' element={<Navigate to='/a/settings' replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </TooltipProvider>
  );
}
