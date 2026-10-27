import { useEffect, useRef, useState } from 'react';
import { Ban, BookOpen, Download, ExternalLink, Link2, RefreshCw, RotateCcw, Search, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type MangaBlacklistRow, type MangaEntry, type MangaRelease, type MangaReviewCard } from '@/lib/api';
import { anilistMangaUrl, anilistName, formatLabel } from '@/lib/anilist';
import { formatBytes, timeAgo } from '@/lib/utils';
import { AnimePoster } from '@/components/AnimePoster';
import { MangaPickDialog, mangaFacts } from '@/components/MangaPickDialog';
import { StatusTag } from '@/components/status-tag';
import { TruncatedText } from '@/components/TruncatedText';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';

/** "Vol 1–14 · Ch 138–147" for what a release says it holds. */
export function releaseSpan(release: Pick<MangaRelease, 'volumes' | 'chapters'>): string {
  const span = (label: string, pair: [number, number] | null) =>
    pair ? label + ' ' + (pair[0] === pair[1] ? pair[0] : pair[0] + '–' + pair[1]) : null;
  return [span('Vol', release.volumes), span('Ch', release.chapters)].filter(Boolean).join(' · ');
}

/** Why a series is followed, in words. */
export function whyLabels(series: { why: string[]; anime: Array<{ name: string }> }): string[] {
  return series.why.map((why) =>
    why === 'anime' ? 'Linked to ' + (series.anime.map((anime) => anime.name).slice(0, 2).join(', ') || 'an anime') :
    why === 'library' ? 'In your library' : 'Followed by you');
}

export default function MangaReview({ blacklist = false }: { blacklist?: boolean }) {
  const [items, setItems] = useState<MangaReviewCard[]>(() => recall<{ items: MangaReviewCard[] }>(pagePaths.mangaReview)?.items ?? []);
  const [blacklisted, setBlacklisted] = useState<MangaBlacklistRow[]>([]);
  const [loading, setLoading] = useState(() => blacklist || recall(pagePaths.mangaReview) === undefined);
  const [query, setQuery] = useState('');
  // Per card: the releases ticked, and whether something is waiting on the server.
  const [ticked, setTicked] = useState<Record<number, Set<string>>>({});
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [matchFor, setMatchFor] = useState<{ card: MangaReviewCard; hashes: string[] } | null>(null);
  const [looking, setLooking] = useState(false);
  const loads = useRef(0);

  async function load() {
    const mine = ++loads.current;
    try {
      if (blacklist) {
        const answer = await api.mangaBlacklist();
        if (mine === loads.current) setBlacklisted(answer.items);
      } else {
        const answer = await api.mangaReview();
        if (mine === loads.current) setItems(answer.items);
      }
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      if (mine === loads.current) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(timer);
  }, [blacklist]);

  async function act(key: string, work: () => Promise<unknown>, done?: string) {
    setBusy((current) => new Set(current).add(key));
    try {
      await work();
      if (done) toast.success(done);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  }

  function toggle(card: MangaReviewCard, infoHash: string, on: boolean) {
    setTicked((current) => {
      const next = new Set(current[card.anilist_id] ?? []);
      if (on) next.add(infoHash);
      else next.delete(infoHash);
      return { ...current, [card.anilist_id]: next };
    });
  }

  async function lookNow() {
    setLooking(true);
    try {
      const answer = await api.runManga();
      toast.success(answer.started ? 'Looking for releases now; new ones show here as they are found' : 'Already looking');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLooking(false);
    }
  }

  const needle = query.trim().toLocaleLowerCase();
  const shown = items.filter((card) =>
    !needle || [card.name, card.romaji, card.english, ...card.releases.map((release) => release.title)]
      .some((text) => (text ?? '').toLocaleLowerCase().includes(needle)));
  const shownBlacklist = blacklisted.filter((row) => !needle || (row.title ?? '').toLocaleLowerCase().includes(needle));

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>{blacklist ? 'Manga blacklist' : 'Manga review'}</h1>
        <p className='text-sm text-muted-foreground'>
          {blacklist
            ? 'Series bankai never looks for again. Restored, the releases blacklisting discarded wait for review once more.'
            : 'Manga and light novels found on nyaa for the series you follow. Nothing downloads by itself: check each match, then download it or discard it. A discarded release is never offered again; a blacklisted series is never looked for again.'}
        </p>
      </div>

      <div className='flex flex-wrap items-center gap-2'>
        <InputGroup className='w-full max-w-sm'>
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput placeholder={blacklist ? 'Filter the blacklist' : 'Filter series and releases'} value={query} onChange={(event) => setQuery(event.target.value)} />
        </InputGroup>
        {!blacklist && (
          <Button variant='secondary' onClick={() => void lookNow()} disabled={looking}>
            <RefreshCw data-icon='inline-start' className={cn(looking && 'animate-spin')} /> Look now
          </Button>
        )}
      </div>

      {loading ? (
        <div className='flex flex-col gap-4'>
          <Skeleton className='h-48 w-full' />
          <Skeleton className='h-48 w-full' />
        </div>
      ) : blacklist ? (
        shownBlacklist.length === 0 ? (
          <Empty>
            <EmptyMedia><Ban /></EmptyMedia>
            <EmptyTitle>Nothing blacklisted</EmptyTitle>
            <EmptyDescription>A series blacklisted from the review shows here.</EmptyDescription>
          </Empty>
        ) : (
          <div className='grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-4'>
            {shownBlacklist.map((row) => (
              <Card key={row.anilist_id} className='overflow-hidden p-0'>
                <div className='relative'>
                  <AnimePoster url={row.cover} title={row.title} />
                  <div className='pointer-events-none absolute inset-x-0 bottom-0 bg-linear-to-t from-black/95 via-black/70 to-transparent px-2 pb-2 pt-10'>
                    <p className='line-clamp-2 text-[11px] font-medium leading-tight text-white'>{row.title || 'AniList ' + row.anilist_id}</p>
                    <p className='font-mono text-[10px] text-white/70'>{formatLabel(row.format)}</p>
                  </div>
                  <div className='absolute right-1.5 top-1.5 flex gap-1'>
                    <Button
                      size='icon-sm'
                      variant='secondary'
                      nativeButton={false}
                      render={<a href={anilistMangaUrl(row.anilist_id)} target='_blank' rel='noreferrer' title='On AniList' aria-label={row.title + ' on AniList'} />}
                    >
                      <ExternalLink />
                    </Button>
                    <Button
                      size='icon-sm'
                      variant='secondary'
                      title='Restore: look for it again'
                      aria-label={'Restore ' + row.title}
                      disabled={busy.has('series:' + row.anilist_id)}
                      onClick={() => void act('series:' + row.anilist_id, () => api.restoreMangaSeries(row.anilist_id), row.title + ' restored')}
                    >
                      <RotateCcw />
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )
      ) : shown.length === 0 ? (
        <Empty>
          <EmptyMedia><BookOpen /></EmptyMedia>
          <EmptyTitle>Nothing to review</EmptyTitle>
          <EmptyDescription>
            New releases of the manga and light novels you follow show here. bankai follows those AniList links to the anime in your
            library, the series in your manga folders, and the ones you follow from Search.
          </EmptyDescription>
        </Empty>
      ) : (
        <div className='flex flex-col gap-4'>
          {shown.map((card) => {
            const picked = ticked[card.anilist_id] ?? new Set<string>();
            const seriesBusy = busy.has('series:' + card.anilist_id);
            const name = anilistName(card);
            return (
              <Card key={card.anilist_id} className='flex flex-col gap-4 p-4 lg:flex-row'>
                <div className='flex shrink-0 gap-3 lg:w-64 lg:flex-col'>
                  <AnimePoster url={card.cover} title={name} className='w-20 shrink-0 lg:w-28' />
                  <div className='flex min-w-0 flex-col gap-1.5'>
                    <TruncatedText text={name} className='line-clamp-2 text-sm font-medium' />
                    <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{mangaFacts(card)}</p>
                    <div className='flex flex-wrap gap-1'>
                      {whyLabels(card).map((label) => <StatusTag key={label} variant='muted'>{label}</StatusTag>)}
                    </div>
                    <p className='text-xs text-muted-foreground'>{card.owned ? 'You have ' + card.owned : 'Nothing of it in your library yet'}</p>
                    <div className='flex flex-wrap gap-1.5'>
                      <Button
                        size='sm'
                        variant='secondary'
                        nativeButton={false}
                        render={<a href={anilistMangaUrl(card.anilist_id)} target='_blank' rel='noreferrer' title='This series on AniList' />}
                      >
                        <ExternalLink data-icon='inline-start' /> AniList
                      </Button>
                      <Button
                        size='sm'
                        variant='secondary'
                        title={picked.size ? 'The ticked releases are another series' : 'These releases are another series'}
                        onClick={() => setMatchFor({ card, hashes: picked.size ? [...picked] : card.releases.map((release) => release.info_hash) })}
                        disabled={seriesBusy}
                      >
                        <Link2 data-icon='inline-start' /> {picked.size ? `Change series (${picked.size})` : 'Change series'}
                      </Button>
                      <Button
                        size='sm'
                        variant='destructive'
                        title='Never look for this series again; its releases here are discarded'
                        onClick={() => void act('series:' + card.anilist_id, () => api.blacklistMangaSeries(card.anilist_id), name + ' blacklisted')}
                        disabled={seriesBusy}
                      >
                        <Ban data-icon='inline-start' /> Blacklist series
                      </Button>
                    </div>
                  </div>
                </div>
                <div className='min-w-0 flex-1 overflow-x-auto'>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className='w-7 pr-2' />
                        <TableHead>Release</TableHead>
                        <TableHead>Adds</TableHead>
                        <TableHead className='text-right'>Size</TableHead>
                        <TableHead className='text-right'>Seeders</TableHead>
                        <TableHead>Found</TableHead>
                        <TableHead className='text-right'>Decide</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {card.releases.map((release) => {
                        const releaseBusy = seriesBusy || busy.has(release.info_hash);
                        return (
                          <TableRow key={release.info_hash}>
                            <TableCell className='w-7 pr-2'>
                              <Checkbox
                                nativeButton
                                render={<button type='button' />}
                                className='align-middle'
                                aria-label={'Tick ' + release.title}
                                checked={picked.has(release.info_hash)}
                                onCheckedChange={(on) => toggle(card, release.info_hash, Boolean(on))}
                              />
                            </TableCell>
                            <TableCell className='max-w-md'>
                              <TruncatedText text={release.title} className='line-clamp-2 break-words text-sm' />
                              <div className='mt-1 flex flex-wrap items-center gap-1'>
                                {releaseSpan(release) && <StatusTag variant='secondary'>{releaseSpan(release)}</StatusTag>}
                                {release.kind && <StatusTag variant='muted'>{release.kind === 'novel' ? 'Light novel' : 'Manga'}</StatusTag>}
                                {release.trusted && <StatusTag variant='success'>Trusted</StatusTag>}
                                {release.guess && (
                                  <StatusTag variant='warning' title='The name is only the start of the series&apos; title: check it is this series'>
                                    Check the match
                                  </StatusTag>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className='text-xs'>
                              {release.adds === null ? <span className='text-muted-foreground'>Does not say</span>
                                : release.adds ? release.adds.split(' · ').map((part) => <span key={part} className='block whitespace-nowrap text-success'>{part}</span>)
                                : <span className='text-muted-foreground'>Nothing new</span>}
                            </TableCell>
                            <TableCell className='whitespace-nowrap text-right font-mono text-xs tabular-nums'>{formatBytes(release.size_bytes)}</TableCell>
                            <TableCell className='text-right font-mono text-xs tabular-nums'>{release.seeders}</TableCell>
                            <TableCell className='whitespace-nowrap text-xs text-muted-foreground'>{timeAgo(release.found_at)}</TableCell>
                            <TableCell>
                              <div className='flex justify-end gap-1'>
                                <Button
                                  size='icon-sm'
                                  variant='ghost'
                                  className='text-muted-foreground'
                                  nativeButton={false}
                                  render={<a href={release.detail_url} target='_blank' rel='noreferrer' title='The release on nyaa' aria-label={release.title + ' on nyaa'} />}
                                >
                                  <ExternalLink />
                                </Button>
                                <Button
                                  size='sm'
                                  variant='primary'
                                  disabled={releaseBusy}
                                  onClick={() => void act(release.info_hash, () => api.downloadMangaRelease(release.info_hash), 'Downloading ' + release.title)}
                                >
                                  <Download data-icon='inline-start' /> Download
                                </Button>
                                <Button
                                  size='icon-sm'
                                  variant='ghost'
                                  title='Discard: never offered again'
                                  aria-label={'Discard ' + release.title}
                                  disabled={releaseBusy}
                                  onClick={() => void act(release.info_hash, () => api.discardMangaRelease(release.info_hash))}
                                >
                                  <Trash2 />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <MangaPickDialog
        query={matchFor ? anilistName(matchFor.card) : null}
        kind={matchFor?.card.kind ?? null}
        title='Which series are these releases?'
        description={matchFor ? `${matchFor.hashes.length} release${matchFor.hashes.length === 1 ? '' : 's'} move to the series you choose, to be reviewed there.` : ''}
        action='Move here'
        onClose={() => setMatchFor(null)}
        onPick={async (entry: MangaEntry) => {
          if (!matchFor) return;
          const { card, hashes } = matchFor;
          await act('series:' + card.anilist_id, () => api.matchMangaReleases(hashes, entry.anilist_id), 'Moved to ' + anilistName(entry));
          setTicked((current) => ({ ...current, [card.anilist_id]: new Set() }));
          setMatchFor(null);
        }}
      />
    </div>
  );
}
