import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Search, Subtitles } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api, eraiPath, recall, type EraiPage, type EraiRelease } from '@/lib/api';
import { cn, timeAgo } from '@/lib/utils';

type GermanFilter = 'all' | 'yes' | 'no';

// What bankai has done with a torrent Erai-raws lists.
const STATUS: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'muted' | 'info' }> = {
  done: { label: 'In library', variant: 'success' },
  existing: { label: 'In library', variant: 'success' },
  queued: { label: 'Queued', variant: 'info' },
  downloading: { label: 'Downloading', variant: 'info' },
  complete: { label: 'Downloaded', variant: 'info' },
  transferring: { label: 'Publishing', variant: 'info' },
  held: { label: 'Held', variant: 'warning' },
  blacklisted: { label: 'Blacklisted', variant: 'destructive' },
  failed: { label: 'Failed', variant: 'destructive' },
  filtered: { label: 'Filtered', variant: 'muted' },
};

function SubtitleChips({ row, languages }: { row: EraiRelease; languages: Record<string, string> }) {
  // German first: it is the one this page is for.
  const codes = [...row.subs].sort((a, b) => Number(b === 'de') - Number(a === 'de'));
  return (
    <div className='flex flex-wrap gap-1'>
      {codes.map((code) => (
        <span
          key={code}
          title={languages[code] ?? code}
          className={cn(
            'rounded px-1.5 py-0.5 font-mono text-[0.65rem] uppercase',
            code === 'de' ? 'bg-success/20 font-semibold text-success' : 'bg-foreground/[0.06] text-muted-foreground',
          )}
        >
          {code}
        </span>
      ))}
      {codes.length === 0 && <span className='text-xs text-muted-foreground'>none listed</span>}
    </div>
  );
}

export default function AnimeErai() {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [german, setGerman] = useState<GermanFilter>('all');
  const [page, setPage] = useState(0);
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<EraiPage | undefined>(() => recall<EraiPage>(eraiPath(0, '', 'all')));
  const [loading, setLoading] = useState(() => recall(eraiPath(0, '', 'all')) === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(0), [search, german]);

  const load = useCallback(async () => {
    const held = recall<EraiPage>(eraiPath(page, search, german));
    if (held) setData(held);
    else setLoading(true);
    try {
      setData(await api.eraiReleases(page, search, german));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, [page, search, german]);

  useEffect(() => {
    void load();
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    try {
      const result = await api.refreshErai();
      toast.success(result.refreshing ? 'Reading Erai-raws feeds; new releases appear within a minute' : 'Set the Erai-raws feed token first');
      window.setTimeout(() => void load(), 30_000);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRefreshing(false);
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className='flex min-h-0 flex-col gap-4'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div>
          <h1 className='font-serif text-3xl font-semibold'>Erai-raws</h1>
          <p className='text-sm text-muted-foreground'>
            Every release as erai-raws.info lists it, with the subtitle languages the site names.
            {data?.configured && (
              <>
                {' '}{data.known.toLocaleString()} releases read{data.complete ? '' : ', still reading back through the history'}
                {data.updated_at ? ` · updated ${timeAgo(data.updated_at)}` : ''}.
              </>
            )}
          </p>
        </div>
        <Button variant='secondary' onClick={() => void refresh()} disabled={refreshing || !data?.configured}>
          <RefreshCw data-icon='inline-start' className={refreshing ? 'animate-spin' : ''} /> Read feeds now
        </Button>
      </div>

      {data?.configured && data.error && (
        <div className='rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning'>
          {data.error}. <Link to='/a/settings' className='underline underline-offset-4'>Anime Settings</Link>
        </div>
      )}

      {data && !data.configured ? (
        <EmptyState
          icon={Subtitles}
          title='Connect your Erai-raws account'
          description='On any show page on erai-raws.info, right-click FHD under RSS Links, copy the link and paste it into Anime Settings as "Erai-raws feed token". bankai keeps only the token from it.'
          action={<Button asChild><Link to='/a/settings'>Open Anime Settings</Link></Button>}
        />
      ) : (
        <>
          <div className='flex flex-wrap items-center gap-3'>
            <div className='relative min-w-64 flex-1 sm:max-w-sm'>
              <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
              <Input ref={searchRef} className='pl-9' value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Filter by release name…' aria-label='Filter Erai-raws releases' />
            </div>
            <ToggleGroup label='German subtitles'>
              <ToggleGroupItem label='All' selected={german === 'all'} onClick={() => setGerman('all')} />
              <ToggleGroupItem label='German' selected={german === 'yes'} onClick={() => setGerman('yes')} />
              <ToggleGroupItem label='No German' selected={german === 'no'} onClick={() => setGerman('no')} />
            </ToggleGroup>
            <span className='ml-auto text-xs text-muted-foreground'>{data ? data.total.toLocaleString() + ' releases' : ''}</span>
          </div>

          <div className='table-bleed min-h-0 flex-1 overflow-auto'>
            <table className='w-full min-w-[980px] border-collapse text-sm'>
              <thead className='sticky top-0 z-10 bg-card'>
                <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                  <th className='px-3 py-2.5 font-medium'>Release</th>
                  <th className='px-3 py-2.5 font-medium'>Subtitles</th>
                  <th className='px-3 py-2.5 font-medium'>Quality</th>
                  <th className='px-3 py-2.5 text-right font-medium'>Size</th>
                  <th className='px-3 py-2.5 font-medium'>Released</th>
                  <th className='px-3 py-2.5 font-medium'>bankai</th>
                  <th className='px-3 py-2.5 text-right font-medium'>Link</th>
                </tr>
              </thead>
              <tbody>
                {loading && !data && (
                  <tr><td colSpan={7} className='px-3 py-8 text-center text-muted-foreground'>Loading…</td></tr>
                )}
                {data && data.items.length === 0 && (
                  <tr><td colSpan={7} className='px-3 py-8 text-center text-muted-foreground'>{data.known ? 'No release matches.' : 'Nothing read yet; the feeds are read every ten minutes.'}</td></tr>
                )}
                {data?.items.map((row) => {
                  const status = row.status ? STATUS[row.status] ?? { label: row.status, variant: 'muted' as const } : null;
                  return (
                    <tr key={row.info_hash} className='border-b border-border/60 last:border-b-0'>
                      <td className='max-w-[30rem] px-3 py-2'>
                        <p className='truncate text-foreground' title={row.name}>{row.name}</p>
                        {row.category && <p className='text-[0.68rem] text-muted-foreground'>{row.category}</p>}
                      </td>
                      <td className='max-w-[18rem] px-3 py-2'><SubtitleChips row={row} languages={data.languages} /></td>
                      <td className='whitespace-nowrap px-3 py-2 font-mono text-xs text-muted-foreground'>{row.resolution}</td>
                      <td className='whitespace-nowrap px-3 py-2 text-right font-mono text-xs text-muted-foreground'>{row.size}</td>
                      <td className='whitespace-nowrap px-3 py-2 text-xs text-muted-foreground'>{timeAgo(row.published)}</td>
                      <td className='px-3 py-2'>
                        {status ? <Badge variant={status.variant} title={row.reason ?? undefined}>{status.label}</Badge> : <span className='text-xs text-muted-foreground'>—</span>}
                      </td>
                      <td className='px-3 py-2 text-right'>
                        {row.page && (
                          <Button asChild size='icon' variant='ghost' title='Open on erai-raws.info'>
                            <a href={row.page} target='_blank' rel='noreferrer' aria-label={'Open ' + row.name + ' on erai-raws.info'}><ExternalLink /></a>
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {data && pages > 1 && (
            <div className='flex items-center justify-end gap-2 text-sm text-muted-foreground'>
              <Button size='icon' variant='secondary' disabled={page === 0} onClick={() => setPage((n) => Math.max(0, n - 1))} aria-label='Previous page'><ChevronLeft /></Button>
              <span>Page {page + 1} of {pages}</span>
              <Button size='icon' variant='secondary' disabled={page + 1 >= pages} onClick={() => setPage((n) => n + 1)} aria-label='Next page'><ChevronRight /></Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
