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
import Settings from '@/pages/Settings';
import Anime from '@/pages/Anime';
import AnimeQueue from '@/pages/AnimeQueue';
import AnimeLibrary from '@/pages/AnimeLibrary';
import AnimeSettings from '@/pages/AnimeSettings';
import AnimeReview from '@/pages/AnimeReview';
import Recent from '@/pages/Recent';
import QBittorrent from '@/pages/QBittorrent';

const MAIN_NAV = [
  { to: '/discover', label: 'Discover', icon: Compass },
  { to: '/search', label: 'Search', icon: SearchIcon },
  { to: '/filmpalast', label: 'Filmpalast', icon: CalendarClock },
  { to: '/qbittorrent', label: 'qBittorrent', icon: Download },
  { to: '/queue', label: 'Queue', icon: ListVideo },
  { to: '/library', label: 'Library', icon: HardDrive },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

const ANIME_NAV = [
  { to: '/anime/discover', label: 'Discover', icon: Sparkles },
  { to: '/anime/queue', label: 'Queue', icon: ListVideo },
  { to: '/anime/library', label: 'Library', icon: HardDrive },
  { to: '/anime/review', label: 'Review', icon: ShieldAlert },
  { to: '/anime/blacklist', label: 'Blacklist', icon: Ban },
  { to: '/anime/settings', label: 'Settings', icon: SettingsIcon },
];

const NAV_GROUPS = [
  { label: 'Bankai', items: MAIN_NAV },
  { label: 'Anime', items: ANIME_NAV },
];
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
      <span className='flex size-7 shrink-0 items-center justify-center rounded-lg bg-secondary text-foreground shadow-[var(--control-edge)]' aria-hidden='true'>
        <Clapperboard className='size-4' strokeWidth={1.7} />
      </span>
      <div className='flex flex-col gap-0.5'>
        <span className='font-mono text-sm font-semibold tracking-tight text-foreground'>bankai</span>
        <span className='hidden text-[11px] leading-none text-muted-foreground md:block'>Media workspace</span>
      </div>
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

  return (
    <div className='flex min-h-8 items-center gap-2 px-2 text-xs text-muted-foreground'>
      <ShieldCheck className='size-3.5 shrink-0' aria-hidden='true' />
      <span className='font-medium'>{connected ? 'VPN connected' : 'VPN'}</span>
      <Tooltip>
        <TooltipTrigger asChild>{dot}</TooltipTrigger>
        <TooltipContent side='right'>{status?.detail || statusLabel}</TooltipContent>
      </Tooltip>
      {disconnected && (
        <Button className='ml-auto' size='sm' variant='secondary' onClick={() => void connect()} disabled={connecting}>
          {connecting && <Loader2 data-icon='inline-start' className='animate-spin' />}
          Connect
        </Button>
      )}
    </div>
  );
}

export default function App() {
  const [collapsed, setCollapsed] = useSidebarState();
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
                {!collapsed && (
                  <p className='hidden px-2 pb-2 pt-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground md:block'>
                    {group.label}
                  </p>
                )}
                {group.items.map(({ to, label, icon: Icon }) => {
                  const link = (
                    <NavLink
                      key={to}
                      to={to}
                      className={() =>
                        cn(
                          'sidebar-link group flex min-h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[13px] font-medium',
                          collapsed && 'md:size-9 md:justify-center md:gap-0 md:p-0',
                        )
                      }
                    >
                      <Icon className='size-4 shrink-0' strokeWidth={1.65} aria-hidden='true' />
                      <span className={cn('md:inline', collapsed && 'md:hidden')}>{label}</span>
                    </NavLink>
                  );
                  return collapsed ? (
                    <Tooltip key={to}>
                      <TooltipTrigger asChild>{link}</TooltipTrigger>
                      <TooltipContent side='right'>{group.label}: {label}</TooltipContent>
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
              <Route path='/' element={<Navigate to='/discover' replace />} />
              <Route path='/discover' element={<Discover />} />
              <Route path='/search' element={<Search />} />
              <Route path='/filmpalast' element={<Recent />} />
              <Route path='/recent' element={<Navigate to='/filmpalast' replace />} />
              <Route path='/anime' element={<Navigate to='/anime/discover' replace />} />
              <Route path='/anime/discover' element={<Anime />} />
              <Route path='/anime/queue' element={<AnimeQueue />} />
              <Route path='/anime/library' element={<AnimeLibrary />} />
              <Route path='/anime/review' element={<AnimeReview />} />
              <Route path='/anime/blacklist' element={<AnimeReview blacklist />} />
              <Route path='/anime/settings' element={<AnimeSettings />} />
              <Route path='/qbittorrent' element={<QBittorrent />} />
              <Route path='/queue' element={<Library />} />
              <Route path='/library' element={<Server />} />
              <Route path='/server' element={<Navigate to='/library' replace />} />
              <Route path='/settings' element={<Settings />} />
            </Routes>
          </div>
        </main>
      </div>
    </TooltipProvider>
  );
}
