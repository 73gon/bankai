import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Download, ExternalLink, Magnet, RefreshCw, Replace, Subtitles } from 'lucide-react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/clipboard';
import { SuggestField, type Suggestion } from '@/components/suggest-field';

import { StatusTag } from '@/components/status-tag';
import { ReplaceDialog, replaceLabel, type ReplaceCandidate } from '@/components/ReplaceDialog';
import { Button } from '@zonsh/ui';
import { Empty, EmptyActions, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';
import { api, eraiPath, recall, type EraiCodec, type EraiKind, type EraiPage, type EraiRelease, type EraiResolution } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import { cn } from '@zonsh/ui';

type GermanFilter = 'all' | 'yes' | 'no';

const RES_KEY = 'bankai:erai:resolution';
const CODEC_KEY = 'bankai:erai:codec';
const KIND_KEY = 'bankai:erai:kind';
const CODECS: Array<[EraiCodec, string]> = [['all', 'All'], ['hevc', 'HEVC'], ['avc', 'AVC']];
const RESOLUTIONS: EraiResolution[] = ['1080p', '720p', 'SD', 'all'];
// What Erai's category says a release is. A finale counts as airing; a batch of encodes is both.
const KINDS: Array<{ value: EraiKind; label: string }> = [
  { value: 'all', label: 'All releases' },
  { value: 'airing', label: 'Airing' },
  { value: 'encoded', label: 'Encoded' },
  { value: 'batch', label: 'Batch' },
  { value: 'special', label: 'Movies & specials' },
];

// 1080p unless this browser chose otherwise: the other qualities rarely matter.
function storedResolution(): EraiResolution {
  try {
    const saved = localStorage.getItem(RES_KEY) as EraiResolution | null;
    return saved && RESOLUTIONS.includes(saved) ? saved : '1080p';
  } catch {
    return '1080p';
  }
}

// The search, the German filter and the page, kept for this browser tab: leaving the page
// and coming back finds them as they were.
const VIEW_KEY = 'bankai:erai:view';

/** The search's suggestions: Erai-raws' show names, another name that led to one beside it. */
async function suggestShows(query: string): Promise<Suggestion[]> {
  const { items } = await api.eraiSuggest(query);
  return items.map((item) => ({
    value: item.name,
    label: item.name,
    hint: item.via ? item.via : item.releases + ' release' + (item.releases === 1 ? '' : 's'),
  }));
}

async function copyMagnet(row: EraiRelease) {
  try {
    await copyText(row.magnet!);
    toast.success('Magnet link copied');
  } catch (error: any) {
    toast.error(error.message);
  }
}

function storedView(): { query: string; german: GermanFilter; page: number } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(VIEW_KEY) ?? '{}');
    return {
      query: typeof saved.query === 'string' ? saved.query : '',
      german: ['all', 'yes', 'no'].includes(saved.german) ? saved.german : 'all',
      page: Number.isInteger(saved.page) && saved.page >= 0 ? saved.page : 0,
    };
  } catch {
    return { query: '', german: 'all', page: 0 };
  }
}

function storedKind(): EraiKind {
  try {
    const saved = localStorage.getItem(KIND_KEY) as EraiKind | null;
    return saved && KINDS.some((kind) => kind.value === saved) ? saved : 'all';
  } catch {
    return 'all';
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
  // Downloaded once, its episodes another release's since: it can be taken again.
  superseded: { label: 'Replaced since', variant: 'muted' },
  transferring: { label: 'Publishing', variant: 'info' },
  held: { label: 'Held', variant: 'warning' },
  blacklisted: { label: 'Blacklisted', variant: 'destructive' },
  failed: { label: 'Failed', variant: 'destructive' },
  filtered: { label: 'Filtered', variant: 'muted' },
  replaced: { label: 'Replaced', variant: 'muted' },
  removed: { label: 'Removed', variant: 'muted' },
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
  // Embedded (a review card's search), it starts from the card's title; on its own page, as it was left.
  const [start] = useState(() => (embedded ? { query: initialQuery, german: 'all' as GermanFilter, page: 0 } : storedView()));
  const [query, setQuery] = useState(start.query);
  const [search, setSearch] = useState(start.query);
  const [german, setGerman] = useState<GermanFilter>(start.german);
  const [res, setRes] = useState<EraiResolution>(storedResolution);
  const [codec, setCodec] = useState<EraiCodec>(storedCodec);
  const [kind, setKind] = useState<EraiKind>(storedKind);
  const [page, setPage] = useState(start.page);
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<EraiPage | undefined>(() => recall<EraiPage>(eraiPath(start.page, start.query, start.german, storedResolution(), storedCodec(), storedKind())));
  const [loading, setLoading] = useState(() => recall(eraiPath(start.page, start.query, start.german, storedResolution(), storedCodec(), storedKind())) === undefined);
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
    if (row.episodes) setCandidate({ info_hash: row.info_hash, name: row.name, episodes: row.episodes });
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  // A changed search or filter starts at its first page; the page restored on arrival stays.
  const filters = useRef(JSON.stringify([start.query, start.german, res, codec, kind]));
  useEffect(() => {
    const now = JSON.stringify([search, german, res, codec, kind]);
    if (now !== filters.current) setPage(0);
    filters.current = now;
  }, [search, german, res, codec, kind]);
  useEffect(() => {
    if (embedded) return;
    try {
      sessionStorage.setItem(VIEW_KEY, JSON.stringify({ query, german, page }));
    } catch {
      /* a remembered search is a convenience */
    }
  }, [embedded, query, german, page]);
  useEffect(() => {
    try {
      localStorage.setItem(RES_KEY, res);
      localStorage.setItem(CODEC_KEY, codec);
      localStorage.setItem(KIND_KEY, kind);
    } catch {
      /* a remembered filter is a convenience */
    }
  }, [res, codec, kind]);

  const load = useCallback(async () => {
    const held = recall<EraiPage>(eraiPath(page, search, german, res, codec, kind));
    if (held) setData(held);
    else setLoading(true);
    try {
      setData(await api.eraiReleases(page, search, german, res, codec, kind));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, [page, search, german, res, codec, kind]);

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
            <SuggestField
              className='min-w-64 flex-1 sm:max-w-sm'
              inputRef={searchRef}
              value={query}
              onValueChange={setQuery}
              load={suggestShows}
              placeholder='Filter by show or release name…'
              label='Filter Erai-raws releases'
            />
            <Select items={KINDS} value={kind} onValueChange={(value) => value !== null && setKind(value as EraiKind)}>
              <SelectTrigger aria-label='Kind of release' className='w-44'><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {KINDS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
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
                  <TableHead align='end'>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && !data && (
                  <TableRow><TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>Loading…</TableCell></TableRow>
                )}
                {data && data.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>
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
                        {row.category && <p className='text-[0.68rem] text-muted-foreground'>{row.category.split('][').join(' · ')}</p>}
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
                      <TableCell className='whitespace-nowrap py-2'>
                        {/* Download (or Use, for a review card) and the release's page, side by side. */}
                        <div className='flex items-center justify-end gap-1'>
                          {row.episodes ? (
                            <Button
                              size='sm'
                              variant={row.german ? 'primary' : 'secondary'}
                              disabled={TAKEN.has(row.status ?? '') || queueing.has(row.info_hash)}
                              title={TAKEN.has(row.status ?? '')
                                ? 'bankai already has this release'
                                : replaceFor ? 'Take this release in place of what the card holds' : 'Download it for the AniList entry its name says; what it changes is shown first'}
                              onClick={() => consider(row)}
                            >
                              {replaceFor ? <Replace data-icon='inline-start' /> : <Download data-icon='inline-start' />}{' '}
                              {queueing.has(row.info_hash) ? 'Queueing…' : replaceFor ? replaceLabel(row.episodes) : 'Download'}
                            </Button>
                          ) : <span className='px-2 text-xs text-muted-foreground' title='Its episode numbers cannot be read from the name'>—</span>}
                          {row.magnet && (
                            <Button
                              size='icon-sm'
                              variant='ghost'
                              title='Copy magnet link'
                              aria-label={'Copy the magnet link of ' + row.name}
                              onClick={() => void copyMagnet(row)}
                            >
                              <Magnet />
                            </Button>
                          )}
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
                        </div>
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

      <ReplaceDialog
        cardKey={replaceFor?.key ?? ''}
        label={replaceFor?.label ?? candidate?.name ?? ''}
        verb={replaceFor ? 'Replace' : 'Download'}
        candidate={candidate}
        onClose={() => setCandidate(null)}
        onStart={(infoHash) => setQueueing((current) => new Set(current).add(infoHash))}
        onSettled={settle}
        onQueued={() => { onReplaced?.(); void load(); }}
      />
    </div>
  );
}
