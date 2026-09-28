import { useEffect, useRef, useState } from 'react';
import { Ban, Check, ExternalLink, Layers, LayoutGrid, Link2, RefreshCw, RotateCcw, Rows3, Search, ShieldCheck, Subtitles, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api, pagePaths, recall, type AnimeReviewItem, type HeldRelease } from '@/lib/api';
import { AnimeMappingDialog } from '@/components/AnimeMappingDialog';
import { AniDBLinkDialog } from '@/components/AniDBLinkDialog';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import AnimeErai from '@/pages/AnimeErai';

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
  // A held show whose AniDB anime the user picks, for review rather than the blacklist.
  const [anidbFor, setAnidbFor] = useState<AnimeReviewItem | null>(null);
  // The show whose Erai-raws releases are open in a dialog, by the name Erai gives it.
  // The card the Erai-raws dialog was opened from, and the name it searches for.
  const [eraiFor, setEraiFor] = useState<{ query: string; item: AnimeReviewItem } | null>(null);
  // Blacklist only: every card, those tied to an AniDB anime, or those not yet.
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
  const [mappingTitle, setMappingTitle] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<AnimeReviewItem | null>(null);
  const [releasesFor, setReleasesFor] = useState<AnimeReviewItem | null>(null);
  const [releases, setReleases] = useState<HeldRelease[]>([]);
  const [loadingReleases, setLoadingReleases] = useState(false);

  const visible = !blacklist || linkFilter === 'all'
    ? items
    : items.filter((item) => Boolean(item.linked ?? item.anidb_id) === (linkFilter === 'linked'));

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

  async function chooseAnidb(anime: { anidb_id: number; title: string }) {
    if (!anidbFor?.release_title) return;
    try {
      const result = await api.saveAnidbMapping(anidbFor.release_title, anime.anidb_id);
      toast.success(anidbFor.source_title + ' is ' + anime.title + ' · ' + result.requested + ' releases rechecking');
      setAnidbFor(null);
      void load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function link(anime: { anidb_id: number; title: string }) {
    if (!linkTarget) return;
    try {
      const result = await api.linkAnimeBlacklist(linkTarget.key, anime.anidb_id);
      toast.success('Linked to ' + anime.title + (result.caught ? ' · ' + result.caught + ' more releases blacklisted' : ''));
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
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>{blacklist ? 'Blacklist' : 'Held for review'}</h1>
          <p className='text-sm text-muted-foreground'>{blacklist
            ? 'Shows here are ignored permanently until you restore them.'
            : 'One card per AniDB entry, which is one season or cour. A decision applies to that entry only.'}</p>
        </div>
        {!blacklist && <Button variant='secondary' onClick={() => void retryAll()} disabled={pending.has('all') || items.length === 0}>
          <RefreshCw data-icon='inline-start' /> Recheck everything
        </Button>}
        {blacklist && (
          <div className='flex flex-wrap items-center gap-2'>
            <ToggleGroup label='Show blacklist entries'>
              {([['all', 'All'], ['linked', 'Linked'], ['unlinked', 'Not linked']] as const).map(([value, label]) => (
                <ToggleGroupItem key={value} label={label} selected={linkFilter === value} onClick={() => setLinkFilter(value)} />
              ))}
            </ToggleGroup>
            <ToggleGroup label='Blacklist view'>
              {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
                <ToggleGroupItem key={value} icon={Icon} label={label} selected={view === value} onClick={() => setView(value)} />
              ))}
            </ToggleGroup>
          </div>
        )}
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : items.length === 0 ? (
        <EmptyState
          icon={blacklist ? Ban : ShieldCheck}
          title={blacklist ? 'No blacklisted Anime' : 'Nothing needs review'}
          description={blacklist ? 'Discarded source shows will appear here with their cover.' : 'All indexed releases currently pass the automatic checks.'}
        />
      ) : blacklist && view === 'table' ? (
        <div className='table-bleed overflow-auto'>
          <table className='w-full min-w-[720px] border-collapse text-sm'>
            <thead className='sticky top-0 z-10 bg-card'>
              <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                <th className='px-3 py-2.5 font-medium'>Show</th>
                <th className='px-3 py-2.5 font-medium'>Erai name</th>
                <th className='px-3 py-2.5 font-medium'>AniDB</th>
                <th className='px-3 py-2.5 text-right font-medium'>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <tr key={item.key} className='border-b border-border/70 last:border-0'>
                  <td className='px-3 py-2'>
                    <div className='flex items-center gap-2.5'>
                      <AnimePoster url={item.poster_url} title={item.title} className='w-8 shrink-0 rounded' />
                      <div className='min-w-0'>
                        <p className='truncate font-medium' title={item.title}>{item.title}</p>
                        {item.year ? <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{item.year}</p> : null}
                      </div>
                    </div>
                  </td>
                  <td className='px-3 py-2 font-mono text-xs text-muted-foreground'>{item.source_title}</td>
                  <td className='px-3 py-2'>
                    {item.linked
                      ? <a className='text-xs underline underline-offset-4' href={'https://anidb.net/anime/' + item.anidb_id} target='_blank' rel='noreferrer'>{item.anidb_title || 'aid ' + item.anidb_id}</a>
                      : <Badge variant='warning'>Not linked</Badge>}
                  </td>
                  <td className='px-3 py-2'>
                    <div className='flex justify-end gap-1.5'>
                      <Button size='sm' variant='secondary' onClick={() => setLinkTarget(item)} disabled={busyFor(item.key)}>
                        <Link2 data-icon='inline-start' /> {item.linked ? 'Relink' : 'Link'}
                      </Button>
                      <Button size='sm' variant='secondary' onClick={() => void restore(item)} disabled={busyFor(item.key)}>
                        <RotateCcw data-icon='inline-start' /> Restore
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
            const tvdb = reasons.some((reason) => reason.includes('TVDB'));
            const anidb = reasons.some((reason) => reason.includes('AniDB'));
            const count = item.release_count || 1;
            return (
              <Card key={item.key} className='flex flex-col overflow-hidden'>
                {/* The cover carries the name, so the card is mostly poster. */}
                <div className='relative'>
                  <AnimePoster url={item.poster_url} title={item.title} />
                  <div className='pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/95 via-black/70 to-transparent px-2 pb-2 pt-10'>
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
                        size='icon'
                        variant={item.linked ? 'secondary' : 'default'}
                        title={item.linked ? 'Linked to AniDB: ' + (item.anidb_title || item.anidb_id) + '. Relink' : 'Not recognised: link to AniDB'}
                        aria-label={(item.linked ? 'Relink ' : 'Link ') + item.title + ' to AniDB'}
                        onClick={() => setLinkTarget(item)}
                        disabled={busyFor(item.key)}
                      >
                        <Link2 />
                      </Button>
                      {item.anidb_id && (
                        <Button asChild size='icon' variant='secondary' title={'On AniDB: ' + (item.anidb_title || 'aid ' + item.anidb_id)}>
                          <a href={'https://anidb.net/anime/' + item.anidb_id} target='_blank' rel='noreferrer' aria-label={item.title + ' on AniDB'}>
                            <ExternalLink />
                          </a>
                        </Button>
                      )}
                    </div>
                  )}
                  {blacklist && (
                    <div className='absolute right-1.5 top-1.5'>
                      <Button
                        size='icon'
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
                      {/* Nyaa, AniDB when the card knows its entry, then Recheck. */}
                      <div className='col-span-2 flex gap-1.5'>
                        {item.detail_url && (
                          <Button asChild variant='outline' className='min-w-0 flex-1 px-1.5 text-xs'>
                            <a href={item.detail_url} target='_blank' rel='noreferrer' title='The release on Nyaa'>
                              Nyaa
                            </a>
                          </Button>
                        )}
                        {item.anidb_id && (
                          <Button asChild variant='outline' className='min-w-0 flex-1 px-1.5 text-xs'>
                            <a
                              href={'https://anidb.net/anime/' + item.anidb_id}
                              target='_blank'
                              rel='noreferrer'
                              title={'On AniDB: ' + (item.anidb_title || 'aid ' + item.anidb_id)}
                            >
                              AniDB
                            </a>
                          </Button>
                        )}
                        <Button
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
                          title='Always allow German subtitles for this show'
                          onClick={() => void decide(item, 'allow_german')}
                          disabled={busyFor(item.key)}
                        >
                          <ShieldCheck data-icon='inline-start' /> <span className='truncate'>Allow</span>
                        </Button>
                      )}
                      <Button
                        variant='secondary'
                        className={cn(!german && 'col-span-2')}
                        onClick={() => void markOwned({ key: item.key }, item.title)}
                        disabled={busyFor(item.key)}
                      >
                        <Check data-icon='inline-start' /> <span className='truncate'>Already downloaded</span>
                      </Button>

                      <Button
                        variant='outline'
                        className='col-span-2'
                        onClick={() => setEraiFor({ query: eraiName(item), item })}
                        title='What Erai-raws lists for this anime: every release, with its subtitles'
                      >
                        <Subtitles data-icon='inline-start' /> <span className='truncate'>Erai-raws releases</span>
                      </Button>

                      {(anidb || item.anidb_id) && item.release_title && (
                        <Button
                          variant='outline'
                          className='col-span-2'
                          onClick={() => setAnidbFor(item)}
                          disabled={busyFor(item.key)}
                        >
                          <Search data-icon='inline-start' /> {item.anidb_id ? 'Change AniDB anime' : 'Choose AniDB anime'}
                        </Button>
                      )}

                      {tvdb && item.release_title && (
                        <Button
                          variant='outline'
                          className='col-span-2'
                          onClick={() => setMappingTitle(item.release_title || null)}
                          disabled={busyFor(item.key)}
                        >
                          <Search data-icon='inline-start' /> Choose TVDB show
                        </Button>
                      )}

                      <Button variant='destructive' onClick={() => void decide(item, 'blacklist')} disabled={busyFor(item.key)}>
                        <Ban data-icon='inline-start' /> <span className='truncate'>Discard</span>
                      </Button>
                      <Button
                        variant='destructive'
                        onClick={() => setPurgeTarget(item)}
                        disabled={busyFor(item.key) || !item.anidb_id}
                        title={item.anidb_id ? undefined : 'Choose the AniDB anime first, so only its own files are deleted'}
                      >
                        <Trash2 data-icon='inline-start' /> <span className='truncate'>Discard and delete</span>
                      </Button>

                      {count > 1 && (
                        <Button variant='ghost' className='col-span-2' onClick={() => void openReleases(item)} disabled={busyFor(item.key)}>
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
      <AnimeMappingDialog title={mappingTitle} onClose={() => setMappingTitle(null)} onSaved={() => void load()} />
      <Dialog open={eraiFor !== null} onOpenChange={(open) => { if (!open) setEraiFor(null); }}>
        <DialogContent className='flex max-h-[90dvh] w-[min(80rem,calc(100vw-2rem))] max-w-6xl flex-col'>
          <DialogHeader>
            <DialogTitle>Erai-raws: {eraiFor?.query}</DialogTitle>
            <DialogDescription>Every release erai-raws.info lists under this name. Replace takes one of them, a whole-season batch or a single episode, in place of what this card holds.</DialogDescription>
          </DialogHeader>
          {eraiFor !== null && (
            <AnimeErai
              key={eraiFor.item.key}
              embedded
              initialQuery={eraiFor.query}
              replaceFor={{ key: eraiFor.item.key, label: eraiFor.item.anidb_title || eraiFor.item.title }}
              onReplaced={() => { setEraiFor(null); void load(); }}
            />
          )}
        </DialogContent>
      </Dialog>
      <AniDBLinkDialog
        name={linkTarget ? linkTarget.source_title || linkTarget.title : null}
        onClose={() => setLinkTarget(null)}
        onPick={link}
      />
      <AniDBLinkDialog
        name={anidbFor ? anidbFor.source_title || anidbFor.title : null}
        onClose={() => setAnidbFor(null)}
        onPick={chooseAnidb}
      />

      {/* Sixty-three releases would have made one card taller than the page,
          so they open beside it rather than inside it. */}
      <Dialog open={releasesFor !== null} onOpenChange={(open) => { if (!open) setReleasesFor(null); }}>
        <DialogContent className='max-w-3xl'>
          <DialogHeader>
            <DialogTitle>{releasesFor?.title}</DialogTitle>
            <DialogDescription>
              Every release held for this show. Dismiss the ones already in the library, or read a
              description on Nyaa.
            </DialogDescription>
          </DialogHeader>
          <div className='max-h-[60vh] overflow-y-auto'>
            {loadingReleases ? (
              <div className='flex justify-center py-8'><Spinner /></div>
            ) : releases.length === 0 ? (
              <p className='py-4 text-xs text-muted-foreground'>No held releases left for this show.</p>
            ) : (
              <ul className='flex flex-col gap-1'>
                {releases.map((release) => (
                  <li key={release.info_hash} className='flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent/40'>
                    <span className='w-14 shrink-0 font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                      {release.season != null ? 'S' + release.season + ' ' : ''}
                      {release.episode != null ? 'E' + release.episode : '—'}
                    </span>
                    <span className='min-w-0 flex-1 truncate font-mono text-[0.68rem]' title={release.title}>
                      {release.title}
                    </span>
                    {release.german_in_title && <Badge variant='success'>GER</Badge>}
                    {release.hevc && <Badge variant='secondary'>HEVC</Badge>}
                    {release.detail_url && (
                      <Button asChild size='icon' variant='ghost' title='Nyaa description'>
                        <a href={release.detail_url} target='_blank' rel='noreferrer' aria-label='Nyaa description'><ExternalLink /></a>
                      </Button>
                    )}
                    <Button
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
          </div>
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
