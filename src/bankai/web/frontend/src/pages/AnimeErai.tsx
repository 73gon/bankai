import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Replace, Search, Subtitles } from 'lucide-react';
import { toast } from '@/lib/toast';

import { StatusTag } from '@/components/status-tag';
import { ReplaceDialog, replaceLabel, type ReplaceCandidate } from '@/components/ReplaceDialog';
import { Button } from '@/zon/ui/button';
import { Empty, EmptyActions, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';
import { api, eraiPath, recall, type EraiCodec, type EraiPage, type EraiRelease, type EraiResolution } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import { cn } from '@/zon/lib/cn';

type GermanFilter = 'all' | 'yes' | 'no';

const RES_KEY = 'bankai:erai:resolution';
const CODEC_KEY = 'bankai:erai:codec';
const CODECS: Array<[EraiCodec, string]> = [['all', 'All'], ['hevc', 'HEVC'], ['avc', 'AVC']];
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

function storedCodec(): EraiCodec {
  try {
    const saved = localStorage.getItem(CODEC_KEY) as EraiCodec | null;
    return saved && CODECS.some(([value]) => value === saved) ? saved : 'all';
  } catch {
    return 'all';
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
            'rounded-item px-1.5 py-0.5 font-mono text-[0.65rem] uppercase',
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
  const [codec, setCodec] = useState<EraiCodec>(storedCodec);
  const [page, setPage] = useState(0);
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<EraiPage | undefined>(() => recall<EraiPage>(eraiPath(0, initialQuery, 'all', storedResolution(), storedCodec())));
  const [loading, setLoading] = useState(() => recall(eraiPath(0, initialQuery, 'all', storedResolution(), storedCodec())) === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  // The row being considered for a replacement.
  const [candidate, setCandidate] = useState<ReplaceCandidate | null>(null);
  // Rows being queued in the background, so several can be replaced in a row.
  const [queueing, setQueueing] = useState<Set<string>>(() => new Set());
  const settle = (infoHash: string) =>
    setQueueing((current) => {
      const next = new Set(current);
      next.delete(infoHash);
      return next;
    });

  function consider(row: EraiRelease) {
    if (replaceFor && row.episodes) setCandidate({ info_hash: row.info_hash, name: row.name, episodes: row.episodes });
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(0), [search, german, res, codec]);
  useEffect(() => {
    try {
      localStorage.setItem(RES_KEY, res);
      localStorage.setItem(CODEC_KEY, codec);
    } catch {
      /* a remembered filter is a convenience */
    }
  }, [res, codec]);

  const load = useCallback(async () => {
    const held = recall<EraiPage>(eraiPath(page, search, german, res, codec));
    if (held) setData(held);
    else setLoading(true);
    try {
      setData(await api.eraiReleases(page, search, german, res, codec));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, [page, search, german, res, codec]);

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
          <h1 className='font-heading text-3xl font-light'>Erai-raws</h1>
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
        <div className='rounded-control border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning'>
          {data.error}. <Link to='/a/settings' className='underline underline-offset-4'>Anime Settings</Link>
        </div>
      )}

      {data && !data.configured ? (
        <Empty>
          <EmptyMedia><Subtitles /></EmptyMedia>
          <EmptyTitle>Connect your Erai-raws account</EmptyTitle>
          <EmptyDescription>On any show page on erai-raws.info, right-click FHD under RSS Links, copy the link and paste it into Anime Settings as "Erai-raws feed token". bankai keeps only the token from it.</EmptyDescription>
          <EmptyActions>
            <Button nativeButton={false} render={<Link to='/a/settings' />}>Open Anime Settings</Button>
          </EmptyActions>
        </Empty>
      ) : (
        <>
          <div className='flex flex-wrap items-center gap-3'>
            <InputGroup className='min-w-64 flex-1 sm:max-w-sm'>
              <InputGroupAddon>
                <Search aria-hidden='true' />
              </InputGroupAddon>
              <InputGroupInput ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Filter by release name…' aria-label='Filter Erai-raws releases' />
            </InputGroup>
            <ToggleGroup<EraiResolution> aria-label='Resolution' value={[res]} onValueChange={(values) => values[0] && setRes(values[0])}>
              {RESOLUTIONS.map((value) => (
                <ToggleGroupItem<EraiResolution> key={value} value={value}>{value === 'all' ? 'All' : value}</ToggleGroupItem>
              ))}
            </ToggleGroup>
            <ToggleGroup<EraiCodec> aria-label='Codec' value={[codec]} onValueChange={(values) => values[0] && setCodec(values[0])}>
              {CODECS.map(([value, label]) => (
                <ToggleGroupItem<EraiCodec> key={value} value={value}>{label}</ToggleGroupItem>
              ))}
            </ToggleGroup>
            <ToggleGroup<GermanFilter> aria-label='German subtitles' value={[german]} onValueChange={(values) => values[0] && setGerman(values[0])}>
              <ToggleGroupItem<GermanFilter> value='all'>All</ToggleGroupItem>
              <ToggleGroupItem<GermanFilter> value='yes'>German</ToggleGroupItem>
              <ToggleGroupItem<GermanFilter> value='no'>No German</ToggleGroupItem>
            </ToggleGroup>
            <span className='ml-auto text-xs text-muted-foreground'>{data ? data.total.toLocaleString() + ' releases' : ''}</span>
          </div>

          <Table variant='plain' dense className='min-w-[980px]' containerProps={{ className: 'min-h-0 flex-1 overflow-auto' }}>
              <TableHeader className='sticky top-0 z-10 bg-(--secondary)'>
                <TableRow>
                  <TableHead>Release</TableHead>
                  <TableHead>Subtitles</TableHead>
                  <TableHead>Quality</TableHead>
                  <TableHead numeric>Size</TableHead>
                  <TableHead>Released</TableHead>
                  <TableHead>bankai</TableHead>
                  {replaceFor && <TableHead>Use</TableHead>}
                  <TableHead numeric>Link</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && !data && (
                  <TableRow><TableCell colSpan={replaceFor ? 8 : 7} className='py-8 text-center text-muted-foreground'>Loading…</TableCell></TableRow>
                )}
                {data && data.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={replaceFor ? 8 : 7} className='py-8 text-center text-muted-foreground'>
                      {data.empty_show ? (
                        <>
                          Erai-raws has a page for this show, but lists no release on it: older shows are often emptied there.
                          Nyaa may still have its torrents.{' '}
                          <a href={data.empty_show} target='_blank' rel='noreferrer' className='link'>Open the page</a>
                        </>
                      ) : data.known ? 'No release matches.' : 'Nothing read yet; the feeds are read every ten minutes.'}
                    </TableCell>
                  </TableRow>
                )}
                {data?.items.map((row) => {
                  const status = row.status ? STATUS[row.status] ?? { label: row.status, variant: 'muted' as const } : null;
                  return (
                    <TableRow key={row.info_hash}>
                      <TableCell className='max-w-[30rem] py-2'>
                        <p className='truncate text-foreground' title={row.name}>{row.name}</p>
                        {row.category && <p className='text-[0.68rem] text-muted-foreground'>{row.category}</p>}
                      </TableCell>
                      <TableCell className='max-w-[18rem] py-2'><SubtitleChips row={row} languages={data.languages} /></TableCell>
                      <TableCell className='whitespace-nowrap py-2 font-mono text-xs text-muted-foreground'>
                        {row.resolution}{' '}
                        <StatusTag variant={row.codec === 'hevc' ? 'success' : 'warning'}>{row.codec.toUpperCase()}</StatusTag>
                      </TableCell>
                      <TableCell numeric className='py-2 text-xs text-muted-foreground'>{row.size}</TableCell>
                      <TableCell className='whitespace-nowrap py-2 text-xs text-muted-foreground'>{timeAgo(row.published)}</TableCell>
                      <TableCell className='py-2'>
                        {status ? <StatusTag variant={status.variant} title={row.reason ?? undefined}>{status.label}</StatusTag> : <span className='text-xs text-muted-foreground'>—</span>}
                      </TableCell>
                      {replaceFor && (
                        <TableCell className='whitespace-nowrap py-2'>
                          {row.episodes ? (
                            <Button
                              size='sm'
                              variant={row.german ? 'primary' : 'secondary'}
                              disabled={TAKEN.has(row.status ?? '') || queueing.has(row.info_hash)}
                              title={TAKEN.has(row.status ?? '') ? 'bankai already has this release' : 'Take this release in place of what the card holds'}
                              onClick={() => consider(row)}
                            >
                              <Replace data-icon='inline-start' /> {queueing.has(row.info_hash) ? 'Queueing…' : replaceLabel(row.episodes)}
                            </Button>
                          ) : <span className='text-xs text-muted-foreground'>—</span>}
                        </TableCell>
                      )}
                      <TableCell className='py-2 text-right'>
                        {row.page && (
                          <Button
                            size='icon-sm'
                            variant='ghost'
                            title='Open on erai-raws.info'
                            nativeButton={false}
                            render={<a href={row.page} target='_blank' rel='noreferrer' aria-label={'Open ' + row.name + ' on erai-raws.info'} />}
                          >
                            <ExternalLink />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
          </Table>

          {data && pages > 1 && (
            <div className='flex items-center justify-end gap-2 text-sm text-muted-foreground'>
              <Button size='icon' variant='secondary' disabled={page === 0} onClick={() => setPage((n) => Math.max(0, n - 1))} aria-label='Previous page'><ChevronLeft /></Button>
              <span>Page {page + 1} of {pages}</span>
              <Button size='icon' variant='secondary' disabled={page + 1 >= pages} onClick={() => setPage((n) => n + 1)} aria-label='Next page'><ChevronRight /></Button>
            </div>
          )}
        </>
      )}

      {replaceFor && (
        <ReplaceDialog
          cardKey={replaceFor.key}
          label={replaceFor.label}
          candidate={candidate}
          onClose={() => setCandidate(null)}
          onStart={(infoHash) => setQueueing((current) => new Set(current).add(infoHash))}
          onSettled={settle}
          onQueued={() => { onReplaced?.(); void load(); }}
        />
      )}
    </div>
  );
}
