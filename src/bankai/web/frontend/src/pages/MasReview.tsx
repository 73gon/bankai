import { useEffect, useMemo, useState } from 'react';
import { Download, ExternalLink, Film, Link2, RefreshCw, Search, ShieldCheck, Trash2, Tv, UserPlus, Users, X } from 'lucide-react';
import { api, type DiscoverItem, type MasReview as Review, type MasReviewRelease, type MasUploaders } from '@/lib/api';
import { inBackground, toast } from '@/lib/toast';
import { AnimePoster } from '@/components/AnimePoster';
import { StatusTag } from '@/components/status-tag';
import { formatSize } from '@/components/library/library-page';
import {
  Button, Card, Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle,
  Empty, EmptyDescription, EmptyMedia, EmptyTitle, InputGroup, InputGroupAddon, InputGroupInput, Spinner,
  Tabs, TabsList, TabsTab, ToggleGroup, ToggleGroupItem,
} from '@zonsh/ui';

type Kind = 'all' | 'movie' | 'show';

/**
 * German-dubbed films and shows found on the indexers (web/mas_releases.py), waiting for a word:
 * Download takes one into the library's German folder, Remove offers it no more. Releases no TMDB
 * title answered for wait under Unmatched, to be linked by hand, downloaded or removed.
 */
/**
 * Uploaders followed for German audio their names do not say: QxR's dual-audio encodes name only
 * "English", the German track is in the description's MediaInfo. Read a slice at a time.
 */
function UploadersButton() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MasUploaders | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const load = () => api.masUploaders().then((next) => live && setData(next)).catch((error) => toast.error(error.message));
    void load();
    const timer = window.setInterval(load, 15000);
    return () => { live = false; window.clearInterval(timer); };
  }, [open]);
  async function act(run: () => Promise<MasUploaders>) {
    setBusy(true);
    try {
      const next = await run();
      setData((old) => ({ ...old, ...next }));
      setName('');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }
  const [site, setSite] = useState('kickass');
  const sites = data?.sites ?? [{ id: 'kickass', label: 'kickasstorrents' }];
  return (
    <>
      <Button variant='secondary' onClick={() => setOpen(true)} title='Uploaders whose releases are read for German audio their names do not say'>
        <Users data-icon='inline-start' /> Uploaders
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size='lg'>
          <DialogHeader>
            <DialogTitle>Uploaders followed</DialogTitle>
            <DialogDescription>
              Some uploaders put a German track in releases whose names say only English (QxR on kickasstorrents) or
              just "MULTi" (Freddy1714 on The Pirate Bay, which lists only an uploader's newest 50 and asks to be read slowly). Every upload of an uploader followed
              here is opened once and its description&apos;s MediaInfo read; those with a German audio track come into this
              review. A German word in the name alone does not count: QxR names German audio-description files among the extras.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className='flex flex-col gap-3'>
            {data === null ? <Spinner /> : data.uploaders.length === 0 ? (
              <p className='text-sm text-muted-foreground'>Nobody followed yet.</p>
            ) : (
              <ul className='flex flex-col divide-y divide-border/60'>
                {data.uploaders.map((row) => (
                  <li key={row.site + row.name} className='flex items-center gap-3 py-2 text-sm'>
                    <span className='font-medium text-foreground'>{row.name}</span>
                    <span className='text-xs text-muted-foreground'>{row.label}</span>
                    <span className='ml-auto text-xs text-muted-foreground'>
                      {row.read.toLocaleString()} read · <span className='text-success'>{row.german} German</span> ·{' '}
                      {row.site === 'tpb'
                        ? row.blocked_until
                          ? <span className='text-warning' title='The Pirate Bay answered "too many requests": bankai waits before asking again'>{row.unread} waiting, The Pirate Bay asked to wait until {new Date(row.blocked_until * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                          : row.unread ? `${row.unread} descriptions to read` : 'newest 50 read'
                        : row.done ? 'every upload read' : `reading back, page ${row.back_to_page ?? 1}`}
                    </span>
                    <Button size='icon-sm' variant='ghost' aria-label={`Stop following ${row.name}`} disabled={busy}
                      onClick={() => void act(() => api.masUnfollowUploader(row.site, row.name))}>
                      <X />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <form className='flex gap-2' onSubmit={(event) => { event.preventDefault(); if (name.trim()) void act(() => api.masFollowUploader(site, name.trim())); }}>
              <ToggleGroup aria-label='Site' value={[site]} onValueChange={(values) => values[0] && setSite(values[0] as string)}>
                {sites.map((item) => <ToggleGroupItem key={item.id} value={item.id}>{item.label}</ToggleGroupItem>)}
              </ToggleGroup>
              <InputGroup className='flex-1'>
                <InputGroupInput value={name} onChange={(event) => setName(event.target.value)} placeholder={`Uploader name on ${sites.find((item) => item.id === site)?.label ?? site}`} aria-label='Uploader name' />
              </InputGroup>
              <Button type='submit' disabled={busy || !name.trim()}><UserPlus data-icon='inline-start' /> Follow</Button>
            </form>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function MasReview() {
  const [review, setReview] = useState<Review | null>(null);
  const [kind, setKind] = useState<Kind>('all');
  const [looking, setLooking] = useState(false);
  // Releases being linked by hand, and of which kind.
  const [linking, setLinking] = useState<{ ids: string[]; kind: 'movie' | 'show'; query: string } | null>(null);
  // Hidden at once on Download or Remove, before the server has answered.
  const [gone, setGone] = useState<Set<string>>(new Set());

  async function load() {
    try {
      setReview(await api.masReview());
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  function hide(ids: string[]) {
    setGone((current) => new Set([...current, ...ids]));
  }

  function download(release: MasReviewRelease) {
    hide([release.id]);
    void inBackground(api.masReviewDownload(release.id), {
      loading: 'Queuing ' + release.title + '…',
      success: 'Queued: it goes into the German folder once downloaded',
    }).then(() => load(), () => setGone((current) => { const next = new Set(current); next.delete(release.id); return next; }));
  }

  function remove(ids: string[]) {
    hide(ids);
    void inBackground(api.masReviewDiscard(ids), {
      loading: 'Removing…',
      success: (result) => `${result.discarded} release${result.discarded === 1 ? '' : 's'} removed from the review`,
    }).then(() => load(), () => undefined);
  }

  async function lookNow() {
    setLooking(true);
    try {
      const result = await api.masReviewRefresh();
      toast.success(`${result.added} new release${result.added === 1 ? '' : 's'} for review (${result.found} German releases looked at)`);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLooking(false);
    }
  }

  const titles = useMemo(
    () => (review?.titles ?? [])
      .filter((title) => kind === 'all' || title.kind === kind)
      .map((title) => ({ ...title, releases: title.releases.filter((release) => !gone.has(release.id)) }))
      .filter((title) => title.releases.length > 0),
    [review, kind, gone],
  );
  const unmatched = useMemo(
    () => (review?.unmatched ?? []).filter((release) => !gone.has(release.id) && (kind === 'all' || release.kind === kind)),
    [review, kind, gone],
  );

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex max-w-3xl flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Review</h1>
          <p className='text-sm text-muted-foreground'>
            Films and shows released with German audio, found on the indexers every half hour. Nothing is downloaded until
            you say so: Download takes a release into the German folder as it is, nothing to sync.
          </p>
        </div>
        <div className='flex items-center gap-2'>
          <Tabs value={kind} onValueChange={(value) => setKind(value as Kind)}>
            <TabsList>
              <TabsTab value='all'>All</TabsTab>
              <TabsTab value='movie'><Film data-icon='inline-start' /> Films</TabsTab>
              <TabsTab value='show'><Tv data-icon='inline-start' /> Shows</TabsTab>
            </TabsList>
          </Tabs>
          <UploadersButton />
          <Button variant='secondary' onClick={() => void lookNow()} disabled={looking}>
            <RefreshCw data-icon='inline-start' className={looking ? 'animate-spin' : ''} /> {looking ? 'Looking…' : 'Look now'}
          </Button>
        </div>
      </div>

      {review === null ? (
        <div className='flex min-h-40 items-center justify-center'><Spinner /></div>
      ) : titles.length === 0 && unmatched.length === 0 ? (
        <Empty>
          <EmptyMedia><ShieldCheck /></EmptyMedia>
          <EmptyTitle>Nothing to review</EmptyTitle>
          <EmptyDescription>German releases the indexers list turn up here; the next look is within half an hour.</EmptyDescription>
        </Empty>
      ) : (
        <>
          <div className='flex flex-col gap-3'>
            {titles.map((title) => (
              <Card key={title.key} className='flex-row gap-4 p-4'>
                <AnimePoster url={title.poster_url} title={title.name} className='w-20 shrink-0 self-start' />
                <div className='flex min-w-0 flex-1 flex-col gap-3'>
                  <div className='flex flex-wrap items-start justify-between gap-2'>
                    <div className='flex min-w-0 flex-col gap-0.5'>
                      <div className='flex flex-wrap items-baseline gap-2'>
                        <span className='font-heading text-lg font-light'>{title.name}</span>
                        {title.year && <span className='font-mono text-xs tabular-nums text-muted-foreground'>{title.year}</span>}
                        <StatusTag variant='outline'>{title.kind === 'movie' ? 'Film' : 'Show'}</StatusTag>
                      </div>
                      {title.german_title && title.german_title.toLowerCase() !== title.name.toLowerCase() && (
                        <span className='text-xs text-muted-foreground'>{title.german_title}</span>
                      )}
                    </div>
                    <div className='flex items-center gap-1'>
                      <Button
                        size='sm'
                        variant='ghost'
                        render={<a href={`https://www.themoviedb.org/${title.kind === 'movie' ? 'movie' : 'tv'}/${title.tmdb_id}`} target='_blank' rel='noreferrer' />}
                        nativeButton={false}
                      >
                        <ExternalLink data-icon='inline-start' /> TMDB
                      </Button>
                      <Button
                        size='sm'
                        variant='ghost'
                        onClick={() => setLinking({ ids: title.releases.map((r) => r.id), kind: title.kind, query: title.name })}
                      >
                        <Link2 data-icon='inline-start' /> Wrong title?
                      </Button>
                      {title.releases.length > 1 && (
                        <Button size='sm' variant='ghost' onClick={() => remove(title.releases.filter((r) => r.status === 'review').map((r) => r.id))}>
                          <Trash2 data-icon='inline-start' /> Remove all
                        </Button>
                      )}
                    </div>
                  </div>
                  <ReleaseList
                    releases={title.releases}
                    kind={title.kind}
                    onDownload={download}
                    onRemove={(r) => remove([r.id])}
                    onRelink={(r) => setLinking({ ids: [r.id], kind: title.kind, query: r.guess?.title || title.name })}
                  />
                </div>
              </Card>
            ))}
          </div>

          {unmatched.length > 0 && (
            <section className='flex flex-col gap-3'>
              <div className='flex flex-col gap-0.5'>
                <h2 className='eyebrow'>Unmatched</h2>
                <p className='text-sm text-muted-foreground'>No TMDB title answered for these names. Link one to its title, or download it as the name says.</p>
              </div>
              <Card className='p-4'>
                <ul className='flex flex-col divide-y divide-border/60'>
                  {unmatched.map((release) => (
                    <ReleaseRow
                      key={release.id}
                      release={release}
                      kind={release.kind}
                      guess={`${release.guess.title}${release.guess.year ? ` (${release.guess.year})` : ''}`}
                      onLink={(r) => setLinking({ ids: [r.id], kind: release.kind, query: release.guess.title })}
                      onDownload={download}
                      onRemove={(r) => remove([r.id])}
                    />
                  ))}
                </ul>
              </Card>
            </section>
          )}
        </>
      )}

      <LinkDialog
        target={linking}
        onClose={() => setLinking(null)}
        onPick={(item, kindPicked) => {
          const target = linking;
          if (!target) return;
          setLinking(null);
          void inBackground(api.masReviewLink(target.ids, kindPicked, item), {
            loading: 'Linking to ' + item.name + '…',
            success: 'Linked to ' + item.name + (item.year ? ` (${item.year})` : ''),
          }).then(() => load(), () => undefined);
        }}
      />
    </div>
  );
}

// A title's releases shown before "Show N more": a show's episodes ran to 30 rows a card.
const FIRST_RELEASES = 4;

/** A title's releases, best seeded first; past the first few folded away. */
function ReleaseList({
  releases,
  kind,
  onDownload,
  onRemove,
  onRelink,
}: {
  releases: MasReviewRelease[];
  kind: 'movie' | 'show';
  onDownload: (release: MasReviewRelease) => void;
  onRemove: (release: MasReviewRelease) => void;
  onRelink: (release: MasReviewRelease) => void;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? releases : releases.slice(0, FIRST_RELEASES);
  const more = releases.length - shown.length;
  return (
    <div className='flex flex-col'>
      <ul className='flex flex-col divide-y divide-border/60'>
        {shown.map((release) => (
          <ReleaseRow key={release.id} release={release} kind={kind} onDownload={onDownload} onRemove={onRemove} onRelink={onRelink} />
        ))}
      </ul>
      {(more > 0 || all) && releases.length > FIRST_RELEASES && (
        <Button size='sm' variant='ghost' className='self-start' onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show ${more} more`}
        </Button>
      )}
    </div>
  );
}

/** One release: its name (linked to its page on the indexer), what it is, and what can be done with it. */
function ReleaseRow({
  release,
  kind,
  guess,
  onDownload,
  onRemove,
  onLink,
  onRelink,
}: {
  release: MasReviewRelease;
  kind: 'movie' | 'show';
  /** For an unmatched release: the title and year its name gives. */
  guess?: string;
  onDownload: (release: MasReviewRelease) => void;
  onRemove: (release: MasReviewRelease) => void;
  onLink?: (release: MasReviewRelease) => void;
  /** For a matched release: link it to another title, when the match is wrong. */
  onRelink?: (release: MasReviewRelease) => void;
}) {
  const queued = release.status === 'queued';
  const held = kind === 'show' && release.held?.german_episodes ? `${release.held.german_episodes} German episodes of it in the library` : null;
  return (
    <li className='flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5 first:pt-0 last:pb-0'>
      <div className='flex min-w-0 flex-1 basis-80 flex-col gap-1'>
        {release.info_url ? (
          <a href={release.info_url} target='_blank' rel='noreferrer' className='break-all font-mono text-xs text-foreground hover:underline' title='Open the torrent on the indexer'>
            {release.title} <ExternalLink className='inline size-3 text-muted-foreground' />
          </a>
        ) : (
          <span className='break-all font-mono text-xs text-foreground'>{release.title}</span>
        )}
        <div className='flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground'>
          {guess && <span>Reads as <span className='text-foreground'>{guess}</span> · {kind === 'movie' ? 'film' : 'show'}</span>}
          {release.resolution && <StatusTag variant='muted'>{release.resolution}</StatusTag>}
          {release.season != null && <span>Season {release.season}</span>}
          <span className='font-mono tabular-nums'>{formatSize(release.size_bytes)}</span>
          <span className='font-mono tabular-nums'>{release.seeders} seeders</span>
          {release.indexer && <span>{release.indexer}</span>}
          {held && <span>{held}</span>}
        </div>
      </div>
      <div className='flex shrink-0 items-center gap-1.5'>
        {queued ? (
          <StatusTag variant='transfer'>Queued</StatusTag>
        ) : (
          <>
            {onLink && (
              <Button size='sm' variant='secondary' onClick={() => onLink(release)}>
                <Link2 data-icon='inline-start' /> Link
              </Button>
            )}
            <Button size='sm' variant='secondary' onClick={() => onDownload(release)} title='Download it into the German folder, as it is'>
              <Download data-icon='inline-start' /> Download
            </Button>
            {onRelink && (
              <Button size='icon-sm' variant='ghost' onClick={() => onRelink(release)} aria-label={'Link ' + release.title + ' to another title'} title='Wrong title? Link it to another'>
                <Link2 />
              </Button>
            )}
            <Button size='icon-sm' variant='ghost' onClick={() => onRemove(release)} aria-label={'Remove ' + release.title} title='Remove it from the review'>
              <X />
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Pick the TMDB title an unmatched release is: searched by name, as a film or a show. */
function LinkDialog({
  target,
  onClose,
  onPick,
}: {
  target: { ids: string[]; kind: 'movie' | 'show'; query: string } | null;
  onClose: () => void;
  onPick: (item: DiscoverItem, kind: 'movie' | 'show') => void;
}) {
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<'movie' | 'show'>('movie');
  const [results, setResults] = useState<DiscoverItem[] | null>(null);

  async function search(text: string, which: 'movie' | 'show') {
    if (!text.trim()) return;
    setResults(null);
    try {
      setResults((await api.discoverSearch(text.trim(), which, 'title', 0, 20)).items.filter((item) => item.tmdb_id));
    } catch (error: any) {
      toast.error(error.message);
      setResults([]);
    }
  }

  useEffect(() => {
    if (!target) return;
    setQuery(target.query);
    setKind(target.kind);
    void search(target.query, target.kind);
  }, [target]);

  return (
    <Dialog open={target !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent size='lg' className='max-h-[85dvh]'>
        <DialogHeader>
          <DialogTitle>Which title is this release?</DialogTitle>
          <DialogDescription>Releases of this name are matched to the title you pick from now on.</DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          <form className='flex w-full items-center gap-2' onSubmit={(event) => { event.preventDefault(); void search(query, kind); }}>
            <InputGroup className='min-w-0 flex-1'>
              <InputGroupAddon><Search /></InputGroupAddon>
              <InputGroupInput aria-label='Search TMDB' value={query} onChange={(event) => setQuery(event.target.value)} />
            </InputGroup>
            <ToggleGroup
              aria-label='Film or show'
              value={[kind]}
              onValueChange={(values) => {
                const next = values[0] as 'movie' | 'show' | undefined;
                if (!next) return;
                setKind(next);
                void search(query, next);
              }}
            >
              <ToggleGroupItem value='movie'>Film</ToggleGroupItem>
              <ToggleGroupItem value='show'>Show</ToggleGroupItem>
            </ToggleGroup>
          </form>
          {results === null ? (
            <div className='flex min-h-24 items-center justify-center'><Spinner /></div>
          ) : results.length === 0 ? (
            <p className='text-sm text-muted-foreground'>TMDB knows no title of that name.</p>
          ) : (
            <ul className='flex flex-col gap-1'>
              {results.map((item) => (
                <li key={item.tmdb_id}>
                  <button type='button' onClick={() => onPick(item, kind)} className='flex w-full items-center gap-3 rounded-item p-1.5 text-left hover:bg-control-hover focus-ring'>
                    <AnimePoster url={item.poster_url} title={item.name} className='w-10 shrink-0' />
                    <span className='min-w-0 flex-1'>
                      <span className='block truncate text-sm text-foreground'>{item.name}</span>
                      <span className='font-mono text-xs text-muted-foreground'>{item.year ?? '—'}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
