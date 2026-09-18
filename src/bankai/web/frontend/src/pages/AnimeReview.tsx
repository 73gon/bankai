import { useEffect, useState } from 'react';
import { Ban, Check, ChevronDown, ExternalLink, RefreshCw, RotateCcw, Search, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeReviewItem, type HeldRelease } from '@/lib/api';
import { AnimeMappingDialog } from '@/components/AnimeMappingDialog';
import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

export default function AnimeReview({ blacklist = false }: { blacklist?: boolean }) {
  const [items, setItems] = useState<AnimeReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [mappingTitle, setMappingTitle] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<AnimeReviewItem | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [releases, setReleases] = useState<HeldRelease[]>([]);
  const [loadingReleases, setLoadingReleases] = useState(false);

  async function toggleReleases(item: AnimeReviewItem) {
    if (expanded === item.key) {
      setExpanded(null);
      return;
    }
    setExpanded(item.key);
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
    setBusy(payload.key ?? payload.info_hashes?.[0] ?? '');
    try {
      const result = await api.markAnimeOwned(payload);
      toast.success('Dismissed ' + result.cleared + ' release' + (result.cleared === 1 ? '' : 's') + ' of ' + label);
      setExpanded(null);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  async function load() {
    setLoading(true);
    try {
      setItems((blacklist ? await api.animeBlacklist() : await api.animeReview()).items);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [blacklist]);

  async function decide(item: AnimeReviewItem, action: 'recheck' | 'allow_german' | 'blacklist') {
    if (!item.info_hash) return;
    setBusy(item.key);
    try {
      const result = await api.reviewAnime(item.info_hash, action);
      toast.success(action === 'blacklist'
        ? 'Show discarded' + (result.blacklisted ? ' (' + result.blacklisted + ' releases)' : '')
        : result.requested + ' releases scheduled for a fresh check');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  async function confirmPurge() {
    if (!purgeTarget?.info_hash) return;
    setBusy(purgeTarget.key);
    try {
      const result = await api.purgeAnimeSeries(purgeTarget.info_hash, true);
      toast.success(
        'Discarded ' + purgeTarget.title + ' — removed ' + result.deleted_files + ' file'
        + (result.deleted_files === 1 ? '' : 's')
        + (result.freed_bytes ? ' (' + formatBytes(result.freed_bytes) + ')' : '')
        + ' and ' + result.removed_torrents + ' torrent'
        + (result.removed_torrents === 1 ? '' : 's'),
      );
      setPurgeTarget(null);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  async function restore(item: AnimeReviewItem) {
    setBusy(item.key);
    try {
      const result = await api.removeAnimeBlacklist(item.key);
      toast.success('Show removed from blacklist; ' + result.requested + ' releases scheduled');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  async function retryAll() {
    setBusy('all');
    try {
      const result = await api.retryHeldAnime();
      toast.success(result.requested + ' held releases scheduled for fresh checks');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
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
            : 'One decision applies to every release with the exact same Erai source-show name.'}</p>
        </div>
        {!blacklist && <Button variant='secondary' onClick={() => void retryAll()} disabled={Boolean(busy) || items.length === 0}>
          <RefreshCw data-icon='inline-start' /> Recheck everything
        </Button>}
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : items.length === 0 ? (
        <EmptyState
          icon={blacklist ? Ban : ShieldCheck}
          title={blacklist ? 'No blacklisted Anime' : 'Nothing needs review'}
          description={blacklist ? 'Discarded source shows will appear here with their cover.' : 'All indexed releases currently pass the automatic checks.'}
        />
      ) : (
        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4'>
          {items.map((item) => {
            const german = (item.reasons || [item.reason || '']).some((reason) => reason.includes('German subtitles'));
            const tvdb = (item.reasons || [item.reason || '']).some((reason) => reason.includes('TVDB'));
            return (
              <Card key={item.key} className='overflow-hidden'>
                <AnimePoster url={item.poster_url} title={item.title} />
                <CardHeader>
                  <CardTitle>{item.title}{item.year ? ' (' + item.year + ')' : ''}</CardTitle>
                  <CardDescription>{blacklist ? item.source_title : (item.release_count || 1) + ' held release' + ((item.release_count || 1) === 1 ? '' : 's')}</CardDescription>
                </CardHeader>
                {!blacklist && <CardContent className='flex flex-col gap-2'>
                  {(item.reasons || [item.reason]).filter(Boolean).map((reason) => <p key={reason} className='text-sm text-warning'>{reason}</p>)}
                  {item.detail_url && <Button asChild variant='outline' size='sm'><a href={item.detail_url} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> Nyaa description</a></Button>}
                </CardContent>}
                <CardFooter className='flex flex-wrap gap-2'>
                  {blacklist ? (
                    <Button variant='secondary' onClick={() => void restore(item)} disabled={busy === item.key}><RotateCcw data-icon='inline-start' /> Restore and recheck</Button>
                  ) : (
                    <>
                      <Button variant='secondary' onClick={() => void decide(item, 'recheck')} disabled={Boolean(busy)}><RefreshCw data-icon='inline-start' /> Recheck</Button>
                      {german && <Button onClick={() => void decide(item, 'allow_german')} disabled={Boolean(busy)}><ShieldCheck data-icon='inline-start' /> Always allow German</Button>}
                      {tvdb && item.release_title && <Button variant='outline' onClick={() => setMappingTitle(item.release_title || null)} disabled={Boolean(busy)}><Search data-icon='inline-start' /> Choose TVDB show</Button>}
                      <Button variant='secondary' onClick={() => void markOwned({ key: item.key }, item.title)} disabled={Boolean(busy)}><Check data-icon='inline-start' /> Already downloaded</Button>
                      {(item.release_count || 1) > 1 && (
                        <Button variant='ghost' onClick={() => void toggleReleases(item)} disabled={Boolean(busy)}>
                          <ChevronDown data-icon='inline-start' className={expanded === item.key ? 'rotate-180' : ''} />
                          {expanded === item.key ? 'Hide' : 'Show'} {item.release_count} releases
                        </Button>
                      )}
                      <Button variant='destructive' onClick={() => void decide(item, 'blacklist')} disabled={Boolean(busy)}><Ban data-icon='inline-start' /> Discard show</Button>
                      <Button variant='destructive' onClick={() => setPurgeTarget(item)} disabled={Boolean(busy)}><Trash2 data-icon='inline-start' /> Discard and delete files</Button>
                    </>
                  )}
                </CardFooter>
                {expanded === item.key && (
                  <div className='border-t border-border px-4 py-3'>
                    {loadingReleases ? (
                      <div className='flex justify-center py-4'><Spinner /></div>
                    ) : releases.length === 0 ? (
                      <p className='py-2 text-xs text-muted-foreground'>No held releases left for this show.</p>
                    ) : (
                      <ul className='flex flex-col gap-1'>
                        {releases.map((release) => (
                          <li key={release.info_hash} className='flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent/40'>
                            <span className='w-8 shrink-0 font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                              {release.episode ?? '—'}
                            </span>
                            <span className='min-w-0 flex-1 truncate font-mono text-[0.68rem]' title={release.title}>
                              {release.title}
                            </span>
                            {release.german_in_title && <Badge variant='success'>GER</Badge>}
                            {release.hevc && <Badge variant='secondary'>HEVC</Badge>}
                            {release.detail_url && (
                              <Button asChild size='sm' variant='ghost'>
                                <a href={release.detail_url} target='_blank' rel='noreferrer' aria-label='Nyaa description'><ExternalLink /></a>
                              </Button>
                            )}
                            <Button
                              size='sm'
                              variant='ghost'
                              disabled={Boolean(busy)}
                              onClick={() => void markOwned({ info_hashes: [release.info_hash] }, release.title)}
                            >
                              <Check data-icon='inline-start' /> Have it
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
      <AnimeMappingDialog title={mappingTitle} onClose={() => setMappingTitle(null)} onSaved={() => void load()} />

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
            <Button variant='secondary' disabled={busy !== null} onClick={() => setPurgeTarget(null)}>Cancel</Button>
            <Button variant='destructive' disabled={busy !== null} onClick={() => void confirmPurge()}>
              <Trash2 data-icon='inline-start' /> Discard and delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}