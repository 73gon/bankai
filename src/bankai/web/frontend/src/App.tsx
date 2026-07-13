import { useEffect, useState } from 'react';
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { Compass, Search as SearchIcon, ListVideo, HardDrive, Settings as SettingsIcon, PanelLeft, PanelLeftClose } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import Discover from '@/pages/Discover';
import Search from '@/pages/Search';
import Library from '@/pages/Library';
import Server from '@/pages/Server';
import Settings from '@/pages/Settings';

const NAV = [
  { to: '/discover', label: 'Discover', icon: Compass },
  { to: '/search', label: 'Search', icon: SearchIcon },
  { to: '/queue', label: 'Queue', icon: ListVideo },
  { to: '/library', label: 'Library', icon: HardDrive },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
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
  return <span className='font-mono text-[0.95rem] font-semibold tracking-[0.02em] text-foreground'>bankai</span>;
}

export default function App() {
  const [collapsed, setCollapsed] = useSidebarState();

  return (
    <TooltipProvider delayDuration={150}>
      <div className='flex min-h-screen flex-col md:flex-row'>
        {/* Sidebar (desktop) / top bar (mobile) */}
        <aside
          className={cn(
            'sticky top-0 z-30 flex shrink-0 flex-row items-center gap-1 border-b border-border/70 bg-background/70 px-3 py-2 backdrop-blur-xl',
            'md:h-screen md:flex-col md:items-stretch md:gap-1 md:border-b-0 md:border-r md:py-4 md:transition-[width] md:duration-200',
            collapsed ? 'md:w-[4.75rem] md:px-2.5' : 'md:w-60 md:px-3',
          )}
        >
          {/* Header row: brand + collapse toggle (desktop) */}
          <div className={cn('hidden md:flex md:items-center', collapsed ? 'md:justify-center md:px-0' : 'md:justify-between md:px-1')}>
            {collapsed ? (
              <button
                type='button'
                onClick={() => setCollapsed(false)}
                aria-label='Expand sidebar'
                className='flex h-10 w-10 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-all duration-200 hover:bg-white/[0.04] hover:text-foreground'
              >
                <PanelLeft className='h-[18px] w-[18px]' />
              </button>
            ) : (
              <>
                <BrandMark />
                <button
                  type='button'
                  onClick={() => setCollapsed(true)}
                  aria-label='Collapse sidebar'
                  className='flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-all duration-200 hover:bg-white/[0.04] hover:text-foreground'
                >
                  <PanelLeftClose className='h-[18px] w-[18px]' />
                </button>
              </>
            )}
          </div>

          {/* Brand (mobile top bar) */}
          <div className='flex items-center px-1 md:hidden'>
            <BrandMark />
          </div>

          <div className='hidden md:my-3 md:block md:h-px md:bg-border/70' />

          <nav className={cn('flex flex-1 flex-row gap-1 overflow-x-auto md:mt-0 md:flex-col md:overflow-visible', collapsed && 'md:items-center')}>
            {NAV.map(({ to, label, icon: Icon }) => {
              const link = (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    cn(
                      'group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-all duration-200',
                      collapsed && 'md:mx-0 md:h-10 md:w-10 md:shrink-0 md:justify-center md:gap-0 md:p-0',
                      isActive
                        ? 'border border-white/10 bg-gradient-to-b from-white/[0.09] to-white/[0.03] text-foreground shadow-[inset_0_1px_0_oklch(1_0_0/0.08)]'
                        : 'border border-transparent text-muted-foreground hover:bg-white/[0.04] hover:text-foreground',
                    )
                  }
                >
                  <Icon className='h-[18px] w-[18px] shrink-0' />
                  <span className={cn('md:inline', collapsed && 'md:hidden')}>{label}</span>
                </NavLink>
              );
              return collapsed ? (
                <Tooltip key={to}>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side='right'>{label}</TooltipContent>
                </Tooltip>
              ) : (
                link
              );
            })}
          </nav>
        </aside>

        <main className='flex-1 px-4 py-6 md:px-8 md:py-8'>
          <div className='w-full animate-fade-in'>
            <Routes>
              <Route path='/' element={<Navigate to='/discover' replace />} />
              <Route path='/discover' element={<Discover />} />
              <Route path='/search' element={<Search />} />
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
