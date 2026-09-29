import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Replace, Search, Subtitles } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api, eraiPath, recall, type EraiPage, type EraiRelease, type EraiResolution, type ReplacementPlan } from '@/lib/api';
import { cn, timeAgo } from '@/lib/utils';

type GermanFilter = 'all' | 'yes' | 'no';

const RES_KEY = 'bankai:erai:resolution';
const RESOLUTIONS: EraiResolution[] = ['1080p', '720p', 'SD', 'all'];

// 1080p unless this browser chose otherwise: the other qualities rarely matter.
function storedResolution(): EraiResolution {
  try {
    const saved = localStorage.getItem(RES_KEY) as EraiResolution | null;
    return saved && RESOLUTIONS.includes(saved) ? saved : '1080p';
  } catch {
    return '1080p';
  }
}

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
  replaced: { label: 'Replaced', variant: 'muted' },
};

// A release bankai is already fetching or has: nothing to replace with it.
const TAKEN = new Set(['done', 'existing', 'queued', 'downloading', 'complete', 'transferring', 'deleting']);

const pad = (n: number) => String(n).padStart(2, '0');

function replaceLabel(episodes: [number, number]) {
  const [first, last] = episodes;
  return first === last ? `Replace episode ${pad(first)}` : `Replace season ${pad(first)}–${pad(last)}`;
}

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

/**
 * The Erai-raws listing. ``embedded`` drops the page header, for showing one
 * anime's releases in a dialog opened from review; ``initialQuery`` is what
 * it opens searched for. ``replaceFor`` is the review card it was opened
 * from: each row can then replace what that card holds.
 */
export default function AnimeErai({
  initialQuery = '',
  embedded = false,
  replaceFor,
  onReplaced,
}: {
  initialQuery?: string;
  embedded?: boolean;
  replaceFor?: { key: string; label: string };
  onReplaced?: () => void;
} = {}) {
  const [query, setQuery] = useState(initialQuery);
  const [search, setSearch] = useState(initialQuery);
  const [german, setGerman] = useState<GermanFilter>('all');
  const [res, setRes] = useState<EraiResolution>(storedResolution);
  const [page, setPage] = useState(0);
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<EraiPage | undefined>(() => recall<EraiPage>(eraiPath(0, initialQuery, 'all', storedResolution())));
  const [loading, setLoading] = useState(() => recall(eraiPath(0, initialQuery, 'all', storedResolution())) === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  // The row being considered for a replacement, and what it would change.
  const [candidate, setCandidate] = useState<EraiRelease | null>(null);
  const [plan, setPlan] = useState<ReplacementPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  // Rows being queued in the background, so several can be replaced in a row.
  const [queueing, setQueueing] = useState<Set<string>>(() => new Set());

  async function consider(row: EraiRelease) {
    if (!replaceFor) return;
    setCandidate(row);
    setPlan(null);
    setPlanError(null);
    try {
      setPlan(await api.replacementPlan(replaceFor.key, row.info_hash));
    } catch (error: any) {
      setPlanError(error.message);
    }
  }

  async function replace() {
    if (!replaceFor || !candidate) return;
    const row = candidate;
    setCandidate(null);
    setQueueing((current) => new Set(current).add(row.info_hash));
    try {
      const result = await api.replaceFromErai(replaceFor.key, row.info_hash, replaceFor.label);
      toast.success(`Queued ${result.queued}`);
      onReplaced?.();
      void load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setQueueing((current) => {
        const next = new Set(current);
        next.delete(row.info_hash);
        return next;
      });
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(0), [search, german, res]);
  useEffect(() => {
    try {
      localStorage.setItem(RES_KEY, res);
    } catch {
      /* a remembered filter is a convenience */
    }
  }, [res]);

  const load = useCallback(async () => {
    const held = recall<EraiPage>(eraiPath(page, search, german, res));
    if (held) setData(held);
    else setLoading(true);
    try {
      setData(await api.eraiReleases(page, search, german, res));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, [page, search, german, res]);

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
    <div className={cn('flex min-h-0 flex-col gap-4', embedded && 'flex-1')}>
      {!embedded && <div className='flex flex-wrap items-end justify-between gap-3'>
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
      </div>}

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
            <ToggleGroup label='Resolution'>
              {RESOLUTIONS.map((value) => (
                <ToggleGroupItem key={value} label={value === 'all' ? 'All' : value} selected={res === value} onClick={() => setRes(value)} />
              ))}
            </ToggleGroup>
            <ToggleGroup label='German subtitles'>
              <ToggleGroupItem label='All' selected={german === 'all'} onClick={() => setGerman('all')} />
              <ToggleGroupItem label='German' selected={german === 'yes'} onClick={() => setGerman('yes')} />
              <ToggleGroupItem label='No German' selected={german === 'no'} onClick={() => setGerman('no')} />
            </ToggleGroup>
            <span className='ml-auto text-xs text-muted-foreground'>{data ? data.total.toLocaleString() + ' releases' : ''}</span>
          </div>

          <div className={embedded ? 'min-h-0 flex-1 overflow-auto rounded-md border border-border' : 'table-bleed min-h-0 flex-1 overflow-auto'}>
            <table className='w-full min-w-[980px] border-collapse text-sm'>
              <thead className='sticky top-0 z-10 bg-card'>
                <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                  <th className='px-3 py-2.5 font-medium'>Release</th>
                  <th className='px-3 py-2.5 font-medium'>Subtitles</th>
                  <th className='px-3 py-2.5 font-medium'>Quality</th>
                  <th className='px-3 py-2.5 text-right font-medium'>Size</th>
                  <th className='px-3 py-2.5 font-medium'>Released</th>
                  <th className='px-3 py-2.5 font-medium'>bankai</th>
                  {replaceFor && <th className='px-3 py-2.5 font-medium'>Use</th>}
                  <th className='px-3 py-2.5 text-right font-medium'>Link</th>
                </tr>
              </thead>
              <tbody>
                {loading && !data && (
                  <tr><td colSpan={replaceFor ? 8 : 7} className='px-3 py-8 text-center text-muted-foreground'>Loading…</td></tr>
                )}
                {data && data.items.length === 0 && (
                  <tr>
                    <td colSpan={replaceFor ? 8 : 7} className='px-3 py-8 text-center text-muted-foreground'>
                      {data.empty_show ? (
                        <>
                          Erai-raws has a page for this show, but lists no release on it: older shows are often emptied there.
                          Nyaa may still have its torrents.{' '}
                          <a href={data.empty_show} target='_blank' rel='noreferrer' className='underline underline-offset-4'>Open the page</a>
                        </>
                      ) : data.known ? 'No release matches.' : 'Nothing read yet; the feeds are read every ten minutes.'}
                    </td>
                  </tr>
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
                      {replaceFor && (
                        <td className='whitespace-nowrap px-3 py-2'>
                          {row.episodes ? (
                            <Button
                              size='sm'
                              variant={row.german ? 'default' : 'secondary'}
                              disabled={TAKEN.has(row.status ?? '') || queueing.has(row.info_hash)}
                              title={TAKEN.has(row.status ?? '') ? 'bankai already has this release' : 'Take this release in place of what the card holds'}
                              onClick={() => void consider(row)}
                            >
                              <Replace data-icon='inline-start' /> {queueing.has(row.info_hash) ? 'Queueing…' : replaceLabel(row.episodes)}
                            </Button>
                          ) : <span className='text-xs text-muted-foreground'>—</span>}
                        </td>
                      )}
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

      <Dialog open={candidate !== null} onOpenChange={(open) => { if (!open) setCandidate(null); }}>
        <DialogContent className='max-w-xl'>
          <DialogHeader>
            <DialogTitle>{candidate?.episodes ? replaceLabel(candidate.episodes) : 'Replace'}</DialogTitle>
            <DialogDescription className='break-words'>{candidate?.name}</DialogDescription>
          </DialogHeader>
          {planError ? (
            <p className='rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive'>{planError}</p>
          ) : !plan ? (
            <p className='text-sm text-muted-foreground'>Working out what it would change…</p>
          ) : (
            <ul className='flex flex-col gap-1.5 text-sm'>
              <li>
                Downloads {plan.batch ? `episodes ${pad(plan.first)}–${pad(plan.last)}` : `episode ${pad(plan.first)}`} of{' '}
                <span className='font-medium text-foreground'>{plan.anidb_title}</span>
                {plan.german ? ', with German subtitles as Erai-raws lists them.' : '. Erai-raws lists no German subtitles for it.'}
              </li>
              <li>{plan.held_count ? `Replaces ${plan.held_count} held release${plan.held_count === 1 ? '' : 's'} in review.` : 'No held release of this card falls in its episodes.'}</li>
              <li>
                {plan.file_count
                  ? `Replaces ${plan.file_count} file${plan.file_count === 1 ? '' : 's'} already in the library, removed once the new ones are published.`
                  : 'Nothing of these episodes is in the library yet.'}
              </li>
              {plan.german_dubs_kept.length > 0 && (
                <li className='text-transfer'>
                  Keeps {plan.german_dubs_kept.length === 1 ? 'episode' : 'episodes'} {plan.german_dubs_kept.map(pad).join(', ')}: German dub, never replaced.
                </li>
              )}
              <li className='text-muted-foreground'>If publishing fails for good, the held releases go back to review.</li>
            </ul>
          )}
          <DialogFooter>
            <Button variant='secondary' onClick={() => setCandidate(null)}>Cancel</Button>
            <Button onClick={() => void replace()} disabled={!plan}>
              <Replace data-icon='inline-start' /> Replace
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
