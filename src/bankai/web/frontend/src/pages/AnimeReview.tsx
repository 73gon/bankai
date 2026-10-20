import { useEffect, useRef, useState } from 'react';
import { Ban, Check, Download, ExternalLink, Layers, LayoutGrid, Link2, RefreshCw, RotateCcw, Rows3, Search, ShieldCheck, Subtitles, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type AniListAnime, type AnimeReviewItem, type HeldRelease } from '@/lib/api';
import { anilistName, anilistUrl } from '@/lib/anilist';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { AnimePoster } from '@/components/AnimePoster';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Spinner } from '@/zon/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';
import AnimeErai from '@/pages/AnimeErai';
import { ReplaceDialog, episodesLabel, type ReplaceCandidate } from '@/components/ReplaceDialog';

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

type View = 'grid' | 'table';
const BLACKLIST_VIEW_KEY = 'bankai.anime.blacklist.view';

function storedView(): View {
  try {
    return localStorage.getItem(BLACKLIST_VIEW_KEY) === 'table' ? 'table' : 'grid';
  } catch {
    return 'grid';
  }
}

export default function AnimeReview({ blacklist = false }: { blacklist?: boolean }) {
  const [view, setView] = useState<View>(storedView);
  const [linkTarget, setLinkTarget] = useState<AnimeReviewItem | null>(null);
  // A held show whose AniList entry the user picks, for review rather than the blacklist.
  const [entryFor, setEntryFor] = useState<AnimeReviewItem | null>(null);
  // The show whose Erai-raws releases are open in a dialog, by the name Erai gives it.
  // The card the Erai-raws dialog was opened from, and the name it searches for.
  const [eraiFor, setEraiFor] = useState<{ query: string; item: AnimeReviewItem } | null>(null);
  // A batch held for a card, being considered for download.
  const [batchFor, setBatchFor] = useState<{ item: AnimeReviewItem; candidate: ReplaceCandidate } | null>(null);
  // Blacklist only: every card, those tied to an AniList entry, or those not yet.
  const [linkFilter, setLinkFilter] = useState<'all' | 'linked' | 'unlinked'>('all');
  const listPath = blacklist ? pagePaths.animeBlacklist : pagePaths.animeReview;
  // Started from the last answer this browser saw, refreshed straight after.
  const [items, setItems] = useState<AnimeReviewItem[]>(() => recall<{ items: AnimeReviewItem[] }>(listPath)?.items ?? []);
  const [loading, setLoading] = useState(() => recall(listPath) === undefined);
  // What is waiting on the server, per card: one card's action no longer
  // greys out every other card on the page.
  const [pending, setPending] = useState<Set<string>>(() => new Set());
  const busyFor = (key: string) => pending.has(key) || pending.has('all');
  function markBusy(key: string, on: boolean) {
    setPending((current) => {
      const next = new Set(current);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }
  // Taken off the page at once; the server finishes in the background, and a
  // failure puts the card back by reloading.
  // Kept off the page until the server has finished with them, so a reload
  // started by another action cannot bring one back in the meantime.
  const dropped = useRef<Set<string>>(new Set());
  function dropCard(key: string) {
    dropped.current.add(key);
    setItems((current) => current.filter((row) => row.key !== key));
  }
  function settle(key: string) {
    dropped.current.delete(key);
    markBusy(key, false);
    void load();
  }
  const [purgeTarget, setPurgeTarget] = useState<AnimeReviewItem | null>(null);
  const [releasesFor, setReleasesFor] = useState<AnimeReviewItem | null>(null);
  const [releases, setReleases] = useState<HeldRelease[]>([]);
  const [loadingReleases, setLoadingReleases] = useState(false);

  const visible = !blacklist || linkFilter === 'all'
    ? items
    : items.filter((item) => Boolean(item.linked ?? item.anilist_id) === (linkFilter === 'linked'));

  async function openReleases(item: AnimeReviewItem) {
    setReleasesFor(item);
    setReleases([]);
    setLoadingReleases(true);
    try {
      const result = await api.animeReviewReleases(item.key);
      setReleases(result.items);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoadingReleases(false);
    }
  }

  const isBatch = (release: HeldRelease) =>
    Boolean(release.episodes && release.episodes[0] !== release.episodes[1]);

  function considerBatch(item: AnimeReviewItem, release: HeldRelease) {
    if (!release.episodes) return;
    setReleasesFor(null);
    setBatchFor({ item, candidate: { info_hash: release.info_hash, name: release.title, episodes: release.episodes } });
  }

  // The card's batch to offer: German in its name first, then HEVC.
  async function openBatch(item: AnimeReviewItem) {
    try {
      const batches = (await api.animeReviewReleases(item.key)).items.filter(isBatch);
      const score = (release: HeldRelease) => Number(release.german_in_title) * 2 + Number(release.hevc);
      const best = batches.sort((a, b) => score(b) - score(a))[0];
      if (best) considerBatch(item, best);
      else toast.error('No batch is held for this card any more');
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function markOwned(payload: { key?: string; info_hashes?: string[] }, label: string) {
    const key = payload.key ?? payload.info_hashes?.[0] ?? '';
    markBusy(key, true);
    if (payload.key) dropCard(payload.key);
    try {
      const result = await api.markAnimeOwned(payload, label);
      toast.success('Dismissed ' + result.cleared + ' release' + (result.cleared === 1 ? '' : 's') + ' of ' + label);
      setReleasesFor(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      settle(key);
    }
  }

  async function load() {
    try {
      const rows = (blacklist ? await api.animeBlacklist() : await api.animeReview()).items;
      setItems(rows.filter((row) => !dropped.current.has(row.key)));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Review and blacklist share this page: switching shows the other's held answer first.
    const held = recall<{ items: AnimeReviewItem[] }>(listPath);
    setItems(held?.items ?? []);
    setLoading(held === undefined);
    void load();
  }, [blacklist]);

  useEffect(() => {
    try {
      localStorage.setItem(BLACKLIST_VIEW_KEY, view);
    } catch {
      /* a remembered view is a convenience */
    }
  }, [view]);

  async function chooseEntry(anime: AniListAnime) {
    if (!entryFor?.release_title) return;
    try {
      const result = await api.saveAnilistMapping(entryFor.release_title, anime.anilist_id);
      toast.success(entryFor.source_title + ' is ' + anilistName(anime) + ' · ' + result.requested + ' releases rechecking');
      setEntryFor(null);
      void load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function link(anime: AniListAnime) {
    if (!linkTarget) return;
    try {
      const result = await api.linkAnimeBlacklist(linkTarget.key, anime.anilist_id);
      toast.success('Linked to ' + anilistName(anime) + (result.caught ? ' · ' + result.caught + ' more releases blacklisted' : ''));
      setLinkTarget(null);
      void load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function decide(item: AnimeReviewItem, action: 'recheck' | 'allow_german' | 'blacklist') {
    if (!item.info_hash) return;
    markBusy(item.key, true);
    // A discarded card leaves the page straight away; its releases are marked
    // on the server in the background.
    if (action === 'blacklist') dropCard(item.key);
    try {
      const result = await api.reviewAnime(item.info_hash, action, item.title);
      toast.success(action === 'blacklist'
        ? 'Discarded ' + item.title + (result.blacklisted ? ' (' + result.blacklisted + ' releases)' : '')
        : result.requested + ' releases scheduled for a fresh check');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      settle(item.key);
    }
  }

  async function confirmPurge() {
    const target = purgeTarget;
    if (!target?.info_hash) return;
    setPurgeTarget(null);
    markBusy(target.key, true);
    dropCard(target.key);
    toast.info('Discarding ' + target.title + ' and deleting its files…');
    try {
      const result = await api.purgeAnimeSeries(target.info_hash, true, target.title);
      toast.success(
        'Discarded ' + target.title + ' — removed ' + result.deleted_files + ' file'
        + (result.deleted_files === 1 ? '' : 's')
        + (result.freed_bytes ? ' (' + formatBytes(result.freed_bytes) + ')' : '')
        + ' and ' + result.removed_torrents + ' torrent'
        + (result.removed_torrents === 1 ? '' : 's'),
      );
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      settle(target.key);
    }
  }

  async function restore(item: AnimeReviewItem) {
    markBusy(item.key, true);
    dropCard(item.key);
    try {
      const result = await api.removeAnimeBlacklist(item.key, item.title);
      toast.success('Restored ' + item.title + '; ' + result.requested + ' releases scheduled');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      settle(item.key);
    }
  }

  async function retryAll() {
    markBusy('all', true);
    try {
      const result = await api.retryHeldAnime();
      toast.success(result.requested + ' held releases scheduled for fresh checks');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      settle('all');
    }
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='eyebrow'>Anime</p>
          <h1 className='font-heading text-3xl font-light'>{blacklist ? 'Blacklist' : 'Held for review'}</h1>
          <p className='text-sm text-muted-foreground'>{blacklist
            ? 'Shows here are ignored permanently until you restore them.'
            : 'One card per AniList entry, which is one season or cour. A decision applies to that entry only.'}</p>
        </div>
        {!blacklist && <Button variant='secondary' onClick={() => void retryAll()} disabled={pending.has('all') || items.length === 0}>
          <RefreshCw data-icon='inline-start' /> Recheck everything
        </Button>}
        {blacklist && (
          <div className='flex flex-wrap items-center gap-2'>
            <ToggleGroup
              aria-label='Show blacklist entries'
              value={[linkFilter]}
              onValueChange={(values) => { if (values[0]) setLinkFilter(values[0] as typeof linkFilter); }}
            >
              {([['all', 'All'], ['linked', 'Linked'], ['unlinked', 'Not linked']] as const).map(([value, label]) => (
                <ToggleGroupItem key={value} value={value}>{label}</ToggleGroupItem>
              ))}
            </ToggleGroup>
            <ToggleGroup
              aria-label='Blacklist view'
              value={[view]}
              onValueChange={(values) => { if (values[0]) setView(values[0] as View); }}
            >
              {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
                <ToggleGroupItem key={value} value={value} icon={<Icon />}>{label}</ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        )}
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : items.length === 0 ? (
        <Empty>
          <EmptyMedia>{blacklist ? <Ban /> : <ShieldCheck />}</EmptyMedia>
          <EmptyTitle>{blacklist ? 'No blacklisted Anime' : 'Nothing needs review'}</EmptyTitle>
          <EmptyDescription>{blacklist ? 'Discarded source shows will appear here with their cover.' : 'All indexed releases currently pass the automatic checks.'}</EmptyDescription>
        </Empty>
      ) : blacklist && view === 'table' ? (
        <Table className='min-w-[720px]' containerProps={{ className: 'overflow-auto' }}>
            <TableHeader className='sticky top-0 z-10 bg-control-surface'>
              <TableRow>
                <TableHead>Show</TableHead>
                <TableHead>Erai name</TableHead>
                <TableHead>AniList</TableHead>
                <TableHead numeric>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((item) => (
                <TableRow key={item.key}>
                  <TableCell>
                    <div className='flex items-center gap-2.5'>
                      <AnimePoster url={item.poster_url} title={item.title} className='w-8 shrink-0 rounded' />
                      <div className='min-w-0'>
                        <p className='truncate font-medium text-foreground' title={item.title}>{item.title}</p>
                        {item.year ? <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{item.year}</p> : null}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className='font-mono text-xs text-muted-foreground'>{item.source_title}</TableCell>
                  <TableCell>
                    {item.linked
                      ? <a className='link text-xs' href={anilistUrl(item.anilist_id!)} target='_blank' rel='noreferrer'>{item.anilist_title || 'id ' + item.anilist_id}</a>
                      : <StatusTag variant='warning'>Not linked</StatusTag>}
                  </TableCell>
                  <TableCell>
                    <div className='flex justify-end gap-1.5'>
                      <Button size='sm' variant='secondary' onClick={() => setLinkTarget(item)} disabled={busyFor(item.key)}>
                        <Link2 data-icon='inline-start' /> {item.linked ? 'Relink' : 'Link'}
                      </Button>
                      <Button size='sm' variant='secondary' onClick={() => void restore(item)} disabled={busyFor(item.key)}>
                        <RotateCcw data-icon='inline-start' /> Restore
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
        </Table>
      ) : (
        <div
          className={cn(
            'grid gap-3',
            // A blacklist card is a cover and one button, so it packs far
            // tighter than a review card carrying six decisions.
            blacklist
              ? 'grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 2xl:grid-cols-10'
              : 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5',
          )}
        >
          {visible.map((item) => {
            const reasons = (item.reasons || [item.reason]).filter(Boolean) as string[];
            const german = reasons.some((reason) => reason.includes('German subtitles'));
            const batch = reasons.some((reason) => reason.startsWith('Whole-season batch'));
            const count = item.release_count || 1;
            return (
              <Card key={item.key} className='gap-0 overflow-hidden [--card-padding:0]'>
                {/* The cover carries the name, so the card is mostly poster. */}
                <div className='relative'>
                  <AnimePoster url={item.poster_url} title={item.title} />
                  <div className='pointer-events-none absolute inset-x-0 bottom-0 bg-linear-to-t from-black/95 via-black/70 to-transparent px-2 pb-2 pt-10'>
                    <p
                      className='line-clamp-2 text-[11px] font-medium leading-tight text-white'
                      title={blacklist ? item.source_title : item.title}
                    >
                      {item.title}{item.year ? ' (' + item.year + ')' : ''}
                    </p>
                  </div>
                  {blacklist && (
                    <div className='absolute left-1.5 top-1.5 flex gap-1'>
                      <Button
                        size='icon-sm'
                        variant={item.linked ? 'secondary' : 'primary'}
                        title={item.linked ? 'Linked to AniList: ' + (item.anilist_title || item.anilist_id) + '. Relink' : 'Not recognised: link to AniList'}
                        aria-label={(item.linked ? 'Relink ' : 'Link ') + item.title + ' to AniList'}
                        onClick={() => setLinkTarget(item)}
                        disabled={busyFor(item.key)}
                      >
                        <Link2 />
                      </Button>
                      {item.anilist_id && (
                        <Button
                          size='icon-sm'
                          variant='secondary'
                          nativeButton={false}
                          render={<a href={anilistUrl(item.anilist_id)} target='_blank' rel='noreferrer' title={'On AniList: ' + (item.anilist_title || 'id ' + item.anilist_id)} aria-label={item.title + ' on AniList'} />}
                        >
                          <ExternalLink />
                        </Button>
                      )}
                    </div>
                  )}
                  {blacklist && (
                    <div className='absolute right-1.5 top-1.5'>
                      <Button
                        size='icon-sm'
                        variant='secondary'
                        title='Restore and recheck'
                        aria-label={'Restore and recheck ' + item.title}
                        onClick={() => void restore(item)}
                        disabled={busyFor(item.key)}
                      >
                        <RotateCcw />
                      </Button>
                    </div>
                  )}
                </div>

                {!blacklist && (
                  <div className='flex flex-col gap-2 p-2'>
                    {(item.seasons?.length ?? 0) > 1 && (
                      <p className='font-mono text-[11px] tabular-nums text-muted-foreground'>
                        {item.seasons!.map((season) => 'S' + season).join(' · ')}
                      </p>
                    )}
                    {reasons.length > 0 && (
                      <p className='line-clamp-2 text-[11px] leading-snug text-warning' title={reasons.join(' · ')}>
                        {reasons.join(' · ')}
                      </p>
                    )}
                    <div className='grid grid-cols-2 gap-1.5 [&>button]:min-w-0'>
                      {/* Nyaa, AniList when the card knows its entry, then Recheck. */}
                      <div className='col-span-2 flex gap-1.5'>
                        {item.detail_url && (
                          <Button
                            size='sm'
                            variant='secondary'
                            className='min-w-0 flex-1 px-1.5 text-xs'
                            nativeButton={false}
                            render={<a href={item.detail_url} target='_blank' rel='noreferrer' title='The release on Nyaa' />}
                          >
                            Nyaa
                          </Button>
                        )}
                        {item.anilist_id && (
                          <Button
                            size='sm'
                            variant='secondary'
                            className='min-w-0 flex-1 px-1.5 text-xs'
                            nativeButton={false}
                            render={
                              <a
                                href={anilistUrl(item.anilist_id)}
                                target='_blank'
                                rel='noreferrer'
                                title={'On AniList: ' + (item.anilist_title || 'id ' + item.anilist_id)}
                              />
                            }
                          >
                            AniList
                          </Button>
                        )}
                        <Button
                          size='sm'
                          variant='secondary'
                          className='min-w-0 flex-1 px-1.5 text-xs'
                          onClick={() => void decide(item, 'recheck')}
                          disabled={busyFor(item.key)}
                          title='Check these releases again'
                        >
                          Recheck
                        </Button>
                      </div>

                      {german && (
                        <Button
                          size='sm'
                          variant='secondary'
                          title='Always allow German subtitles for this show'
                          onClick={() => void decide(item, 'allow_german')}
                          disabled={busyFor(item.key)}
                        >
                          <ShieldCheck data-icon='inline-start' /> <span className='truncate'>Allow</span>
                        </Button>
                      )}
                      <Button
                        size='sm'
                        variant='secondary'
                        className={cn(!german && 'col-span-2')}
                        onClick={() => void markOwned({ key: item.key }, item.title)}
                        disabled={busyFor(item.key)}
                      >
                        <Check data-icon='inline-start' /> <span className='truncate'>Already downloaded</span>
                      </Button>

                      {batch && (
                        <Button
                          size='sm'
                          variant='secondary'
                          className='col-span-2'
                          onClick={() => void openBatch(item)}
                          disabled={busyFor(item.key)}
                          title='Download the whole-season batch held for this card, in place of its single episodes'
                        >
                          <Download data-icon='inline-start' /> <span className='truncate'>Download batch</span>
                        </Button>
                      )}
                      <Button
                        size='sm'
                        variant='secondary'
                        className='col-span-2'
                        onClick={() => setEraiFor({ query: eraiName(item), item })}
                        title='What Erai-raws lists for this anime: every release, with its subtitles'
                      >
                        <Subtitles data-icon='inline-start' /> <span className='truncate'>Erai-raws releases</span>
                      </Button>

                      {item.release_title && (
                        <Button
                          size='sm'
                          variant='secondary'
                          className='col-span-2'
                          onClick={() => setEntryFor(item)}
                          disabled={busyFor(item.key)}
                        >
                          <Search data-icon='inline-start' /> {item.anilist_id ? 'Change AniList entry' : 'Choose AniList entry'}
                        </Button>
                      )}

                      <Button size='sm' variant='destructive' onClick={() => void decide(item, 'blacklist')} disabled={busyFor(item.key)}>
                        <Ban data-icon='inline-start' /> <span className='truncate'>Discard</span>
                      </Button>
                      <Button
                        size='sm'
                        variant='destructive'
                        onClick={() => setPurgeTarget(item)}
                        disabled={busyFor(item.key) || !item.anilist_id}
                        title={item.anilist_id ? undefined : 'Choose the AniList entry first, so only its own files are deleted'}
                      >
                        <Trash2 data-icon='inline-start' /> <span className='truncate'>Discard and delete</span>
                      </Button>

                      {count > 1 && (
                        <Button size='sm' variant='ghost' className='col-span-2' onClick={() => void openReleases(item)} disabled={busyFor(item.key)}>
                          <Layers data-icon='inline-start' /> Show {count} releases
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
      <Dialog open={eraiFor !== null} onOpenChange={(open) => { if (!open) setEraiFor(null); }}>
        <DialogContent className='h-[90dvh] max-h-[90dvh] w-[90vw] max-w-none shrink-0'>
          <DialogHeader>
            <DialogTitle>Erai-raws: {eraiFor?.query}</DialogTitle>
            <DialogDescription>Every release erai-raws.info lists under this name. Replace takes one of them, a whole-season batch or a single episode, in place of what this card holds.</DialogDescription>
          </DialogHeader>
          {eraiFor !== null && (
            <AnimeErai
              key={eraiFor.item.key}
              embedded
              initialQuery={eraiFor.query}
              replaceFor={{ key: eraiFor.item.key, label: eraiFor.item.anilist_title || eraiFor.item.title }}
              onReplaced={() => void load()}
            />
          )}
        </DialogContent>
      </Dialog>
      {batchFor && (
        <ReplaceDialog
          cardKey={batchFor.item.key}
          label={batchFor.item.anilist_title || batchFor.item.title}
          candidate={batchFor.candidate}
          verb='Download batch'
          onClose={() => setBatchFor(null)}
          onStart={() => markBusy(batchFor.item.key, true)}
          onSettled={() => settle(batchFor.item.key)}
          onQueued={() => void load()}
        />
      )}
      <AniListPickDialog
        query={linkTarget ? linkTarget.source_title || linkTarget.title : null}
        onClose={() => setLinkTarget(null)}
        onPick={link}
      />
      <AniListPickDialog
        query={entryFor ? entryFor.source_title || entryFor.title : null}
        onClose={() => setEntryFor(null)}
        onPick={chooseEntry}
        title='Choose the AniList entry'
        description="Releases of this name are filed under the entry you pick from then on; the held ones are rechecked now."
        action='Choose'
      />

      {/* Sixty-three releases would have made one card taller than the page,
          so they open beside it rather than inside it. */}
      <Dialog open={releasesFor !== null} onOpenChange={(open) => { if (!open) setReleasesFor(null); }}>
        <DialogContent size='xl' className='max-w-3xl'>
          <DialogHeader>
            <DialogTitle>{releasesFor?.title}</DialogTitle>
            <DialogDescription>
              Every release held for this show. Dismiss the ones already in the library, or read a
              description on Nyaa.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className='max-h-[60vh]'>
            {loadingReleases ? (
              <div className='flex justify-center py-8'><Spinner /></div>
            ) : releases.length === 0 ? (
              <p className='py-4 text-xs text-muted-foreground'>No held releases left for this show.</p>
            ) : (
              <ul className='flex flex-col gap-1'>
                {releases.map((release) => (
                  <li key={release.info_hash} className='flex items-center gap-2 rounded-button px-2 py-1.5 transition-colors hover:bg-control-hover'>
                    <span className='w-14 shrink-0 font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                      {isBatch(release) && release.episodes
                        ? (release.episodes[1] >= 999 ? 'Season' : release.episodes[0] + '–' + release.episodes[1])
                        : <>{release.season != null ? 'S' + release.season + ' ' : ''}{release.episode != null ? 'E' + release.episode : '—'}</>}
                    </span>
                    <span className='min-w-0 flex-1 truncate font-mono text-[0.68rem]' title={release.title}>
                      {release.title}
                    </span>
                    {release.german_in_title && <StatusTag variant='success'>GER</StatusTag>}
                    {release.hevc && <StatusTag variant='secondary'>HEVC</StatusTag>}
                    {release.detail_url && (
                      <Button
                        size='icon-sm'
                        variant='ghost'
                        nativeButton={false}
                        render={<a href={release.detail_url} target='_blank' rel='noreferrer' title='Nyaa description' aria-label='Nyaa description' />}
                      >
                        <ExternalLink />
                      </Button>
                    )}
                    {isBatch(release) && releasesFor && (
                      <Button
                        size='sm'
                        variant='secondary'
                        title={'Download ' + (release.episodes ? episodesLabel(release.episodes) : 'it') + ' with this batch'}
                        onClick={() => considerBatch(releasesFor, release)}
                      >
                        <Download data-icon='inline-start' /> Download
                      </Button>
                    )}
                    <Button
                      size='sm'
                      variant='ghost'
                      disabled={busyFor(release.info_hash)}
                      onClick={() => void markOwned({ info_hashes: [release.info_hash] }, release.title)}
                    >
                      <Check data-icon='inline-start' /> Have it
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>

      <Dialog open={purgeTarget !== null} onOpenChange={(open) => { if (!open) setPurgeTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Discard {purgeTarget?.title} and delete its files?</DialogTitle>
            <DialogDescription>
              Every season of this show stops being searched for, its torrents are removed from
              qBittorrent, and every episode already in the library is deleted along with its
              folders. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setPurgeTarget(null)}>Cancel</Button>
            <Button variant='destructive' onClick={() => void confirmPurge()}>
              <Trash2 data-icon='inline-start' /> <span className='truncate'>Discard and delete</span>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** The name Erai-raws gives a card's show: its release name without tags and episode. */
function eraiName(item: AnimeReviewItem): string {
  const raw = item.release_title || item.source_title || item.title;
  return raw.replace(/^(?:\s*\[[^\]]*\])+\s*/, '').split(/\s+-\s+\d/)[0].trim() || item.source_title || item.title;
}
