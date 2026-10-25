import { useEffect, useState } from 'react';
import { Check, ImageOff, RefreshCw, RotateCcw } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type ArtworkCandidate, type ArtworkSlot } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Input } from '@/zon/ui/input';
import { Spinner } from '@/zon/ui/spinner';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';

/** How a slot's pictures are shaped: posters upright, backdrops and thumbs wide, banners wider, logos as they are. */
function shape(slot: string) {
  if (slot === 'fanart' || slot === 'landscape') return { box: 'aspect-video', grid: 'grid-cols-2 sm:grid-cols-3', fit: 'object-cover' };
  if (slot === 'banner') return { box: 'aspect-[5.4/1]', grid: 'grid-cols-1 sm:grid-cols-2', fit: 'object-cover' };
  if (slot === 'logo') return { box: 'aspect-[3/1.2]', grid: 'grid-cols-2 sm:grid-cols-3', fit: 'object-contain p-3' };
  return { box: 'aspect-[2/3]', grid: 'grid-cols-3 sm:grid-cols-5', fit: 'object-cover' };
}

function Picture({ url, slot, alt, className }: { url: string | null; slot: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const { box, fit } = shape(slot);
  return (
    <div className={cn(box, 'w-full overflow-hidden rounded-md', slot === 'logo' ? 'bg-[repeating-conic-gradient(var(--muted)_0_25%,transparent_0_50%)] bg-[length:16px_16px]' : 'bg-muted', className)}>
      {url && !failed ? (
        <img src={api.posterUrl(url)} alt={alt} loading='lazy' className={cn('h-full w-full', fit)} onError={() => setFailed(true)} />
      ) : (
        <div className='flex h-full items-center justify-center text-muted-foreground'><ImageOff aria-hidden='true' className='size-6' /></div>
      )}
    </div>
  );
}

function caption(item: ArtworkCandidate) {
  const size = item.width && item.height ? item.width + '×' + item.height : null;
  const lang = item.source === 'tmdb' ? (item.lang ? item.lang.toUpperCase() : 'no text') : null;
  return [item.source === 'tmdb' ? item.label : 'AniList · ' + item.label, lang, size].filter(Boolean).join(' · ');
}

/** A collection's artwork, slot by slot: what Jellyfin shows now, and every picture it could show instead. */
export function ArtworkDialog({ collectionId, title, onClose, onChanged }: {
  collectionId: number | null;
  title: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [slots, setSlots] = useState<ArtworkSlot[] | null>(null);
  const [tmdbOn, setTmdbOn] = useState(true);
  const [tab, setTab] = useState('poster');
  const [pasted, setPasted] = useState('');
  const [saving, setSaving] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load(refresh = false) {
    if (collectionId == null) return;
    try {
      const result = await api.collectionArtwork(collectionId, refresh);
      setSlots(result.slots);
      setTmdbOn(result.tmdb);
      if (result.tmdb_error?.error) toast.warning('TMDB: ' + result.tmdb_error.error);
    } catch (error: any) {
      toast.error(error.message);
      onClose();
    }
  }

  useEffect(() => {
    setSlots(null);
    setTab('poster');
    setPasted('');
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionId]);

  async function choose(slot: ArtworkSlot, url: string | null) {
    if (collectionId == null) return;
    setSaving(url ?? 'automatic');
    try {
      await api.chooseArtwork(collectionId, slot.slot, url);
      toast.success(slot.label + (url ? ' changed' : ' back to automatic') + '; Jellyfin picks it up on its next refresh');
      setPasted('');
      await load();
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(null);
    }
  }

  async function refresh() {
    setRefreshing(true);
    try {
      await load(true);
    } finally {
      setRefreshing(false);
    }
  }

  const open = collectionId != null;
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !saving) onClose(); }}>
      <DialogContent size='xl' className='max-h-[90dvh]'>
        <DialogHeader>
          <DialogTitle>Artwork of {title}</DialogTitle>
          <DialogDescription>
            What Jellyfin shows for the show and each season. Click a picture to use it, or paste any image&apos;s address. Automatic takes
            AniList&apos;s poster and banner{tmdbOn ? ', and TMDB\'s backdrop, thumb, logo and Specials poster' : ''}.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {slots === null ? <div className='flex h-60 items-center justify-center'><Spinner label='Loading the artwork' /></div> : (
            <Tabs value={tab} onValueChange={(value) => { setTab(String(value)); setPasted(''); }} className='gap-4'>
              <TabsList className='h-auto max-w-full flex-wrap'>
                {slots.map((slot) => <TabsTab key={slot.slot} value={slot.slot}>{slot.label}</TabsTab>)}
              </TabsList>
              {slots.map((slot) => {
                const { grid } = shape(slot.slot);
                return (
                  <TabsPanel key={slot.slot} value={slot.slot} className='flex flex-col gap-4'>
                    <div className='flex flex-wrap items-start gap-4'>
                      <div className={cn(slot.slot === 'poster' || slot.slot.startsWith('season:') ? 'w-36' : 'w-full max-w-md')}>
                        <Picture url={slot.url} slot={slot.slot} alt={slot.label + ' now'} />
                      </div>
                      <div className='flex min-w-0 flex-1 flex-col gap-3'>
                        <div className='flex flex-wrap items-center gap-2'>
                          <span className='eyebrow'>Now</span>
                          {slot.url
                            ? <StatusTag variant={slot.chosen ? 'info' : 'success'}>{slot.chosen ? 'Chosen by hand' : 'Automatic'}</StatusTag>
                            : <StatusTag variant='warning'>None</StatusTag>}
                          {slot.chosen && (
                            <Button size='sm' variant='secondary' onClick={() => void choose(slot, null)} disabled={saving !== null}>
                              <RotateCcw data-icon='inline-start' /> Automatic
                            </Button>
                          )}
                        </div>
                        <form
                          className='flex gap-2'
                          onSubmit={(event) => { event.preventDefault(); if (pasted.trim()) void choose(slot, pasted.trim()); }}
                        >
                          <Input placeholder='https://… any image address' value={pasted} onChange={(event) => setPasted(event.target.value)} aria-label={'Image address for ' + slot.label} />
                          <Button type='submit' variant='secondary' disabled={!pasted.trim() || saving !== null}>Use</Button>
                        </form>
                      </div>
                    </div>
                    {slot.candidates.length ? (
                      <div className={cn('grid gap-3', grid)}>
                        {slot.candidates.map((item) => {
                          const current = item.url === slot.url;
                          return (
                            <button
                              key={item.url}
                              type='button'
                              onClick={() => { if (!current) void choose(slot, item.url); }}
                              disabled={saving !== null}
                              title={caption(item)}
                              aria-label={'Use ' + caption(item)}
                              aria-pressed={current}
                              className={cn('group relative flex flex-col gap-1 rounded-md text-left focus-ring', current ? 'cursor-default' : 'cursor-pointer')}
                            >
                              <span className='relative block'>
                                <Picture url={item.preview} slot={slot.slot} alt={caption(item)} className={cn('ring-2 ring-transparent transition-shadow', current ? 'ring-primary' : 'group-hover:ring-border')} />
                                {current && <span className='absolute right-1.5 top-1.5 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground'><Check className='size-3.5' /></span>}
                                {saving === item.url && <span className='absolute inset-0 flex items-center justify-center rounded-md bg-background/60'><Spinner /></span>}
                              </span>
                              <span className='truncate text-[0.68rem] text-muted-foreground'>{caption(item)}</span>
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className='text-sm text-muted-foreground'>
                        Nothing to choose from{tmdbOn ? ': neither AniList nor TMDB has this picture for the show' : ': AniList has none, and TMDB is off in the anime settings'}. Paste an image address above.
                      </p>
                    )}
                  </TabsPanel>
                );
              })}
            </Tabs>
          )}
        </DialogBody>
        <DialogFooter>
          {tmdbOn && (
            <Button variant='secondary' onClick={() => void refresh()} disabled={refreshing || slots === null} title='Ask TMDB again for this show&apos;s pictures'>
              <RefreshCw data-icon='inline-start' className={refreshing ? 'animate-spin' : ''} /> Reload from TMDB
            </Button>
          )}
          <Button onClick={onClose} disabled={saving !== null}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
