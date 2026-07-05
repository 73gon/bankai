import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { Compass, Search as SearchIcon, Library as LibraryIcon, Server as ServerIcon, Settings as SettingsIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import Discover from '@/pages/Discover';
import Search from '@/pages/Search';
import Library from '@/pages/Library';
import Server from '@/pages/Server';
import Settings from '@/pages/Settings';

const NAV = [
  { to: '/', label: 'Discover', icon: Compass, end: true },
  { to: '/search', label: 'Search', icon: SearchIcon },
  { to: '/library', label: 'Queue', icon: LibraryIcon },
  { to: '/server', label: 'Server', icon: ServerIcon },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

function Brand() {
  return (
    <div className='flex items-center gap-2 px-2 py-1'>
      <div className='font-mono text-base font-semibold tracking-wide'>bankai</div>
    </div>
  );
}

export default function App() {
  return (
    <div className='flex min-h-screen flex-col md:flex-row'>
      {/* Sidebar (desktop) / top bar (mobile) */}
      <aside className='sticky top-0 z-30 flex shrink-0 flex-row items-center gap-1 border-b border-border bg-background px-3 py-2 md:h-screen md:w-60 md:flex-col md:items-stretch md:gap-2 md:border-b-0 md:border-r md:px-3 md:py-5'>
        <div className='hidden md:block'>
          <Brand />
        </div>
        <div className='md:hidden'>
          <Brand />
        </div>
        <nav className='flex flex-1 flex-row gap-1 overflow-x-auto md:mt-4 md:flex-col md:overflow-visible'>
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                )
              }
            >
              <Icon className='h-4 w-4 shrink-0' />
              <span className='hidden md:inline'>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className='flex-1 px-4 py-6 md:px-8 md:py-8'>
        <div className='w-full animate-fade-in'>
          <Routes>
            <Route path='/' element={<Discover />} />
            <Route path='/search' element={<Search />} />
            <Route path='/queue' element={<Navigate to='/library' replace />} />
            <Route path='/library' element={<Library />} />
            <Route path='/server' element={<Server />} />
            <Route path='/settings' element={<Settings />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
