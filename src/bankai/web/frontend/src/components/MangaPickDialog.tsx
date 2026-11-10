import { useEffect, useRef, useState } from 'react';
import { Check, ExternalLink, Search } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type MangaEntry, type MangaKind } from '@/lib/api';
import { anilistMangaUrl, anilistName, formatLabel } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { TruncatedText } from '@/components/TruncatedText';
import { Button } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';

/** How long typing pauses before the search runs. */
const DEBOUNCE_MS = 400;

/** What a manga entry is, in one line: "Manga · 16 vols · 2020 · id 117195". */
export function mangaFacts(entry: Pick<MangaEntry, 'format' | 'volumes' | 'chapters' | 'year' | 'anilist_id'>): string {
  return [
    formatLabel(entry.format),
    entry.volumes ? entry.volumes + ' vols' : entry.chapters ? entry.chapters + ' ch' : null,
    entry.year,
    'id ' + entry.anilist_id,
  ].filter(Boolean).join(' · ');
}

/** Pick a manga or light novel on AniList: searched by title, or found from a pasted anilist.co link or id. */
export function MangaPickDialog({
  query,
  kind: initialKind = null,
  onClose,
  onPick,
  title = 'Choose the series',
  description = '',
  action = 'Choose',
}: {
  /** What to search for first; null closes the dialog. */
  query: string | null;
  /** Which kind to search first; null searches both. */
  kind?: MangaKind | null;
  onClose: () => void;
  onPick: (entry: MangaEntry) => Promise<void>;
  title?: string;
  description?: string;
  action?: string;
}) {
  const [input, setInput] = useState('');
  const [kind, setKind] = useState<MangaKind | 'all'>('all');
  const [results, setResults] = useState<MangaEntry[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
  // Only the newest search may answer: an older one finishing late is dropped.
  const latest = useRef(0);
  const lastQuery = useRef('');

  async function search(value: string, which: MangaKind | 'all' = kind) {
    const q = value.trim();
    if (!q) return;
    lastQuery.current = q;
    const mine = ++latest.current;
    setSearching(true);
    try {
      const found = (await api.mangaAnilistSearch(q, which === 'all' ? null : which)).items;
      if (mine === latest.current) setResults(found);
    } catch (error: any) {
      if (mine === latest.current) {
        toast.error(error.message);
        setResults([]);
      }
    } finally {
      if (mine === latest.current) {
        setSearching(false);
        setSearched(true);
      }
    }
  }

  // Opened: the given name is searched at once.
  useEffect(() => {
    if (query === null) return;
    const which = initialKind ?? 'all';
    setInput(query);
    setKind(which);
    setResults([]);
    setSearched(false);
    void search(query, which);
  }, [query]);

  // Typed: searched once typing pauses.
  useEffect(() => {
    if (query === null) return;
    const q = input.trim();
    if (!q || q === lastQuery.current) return;
    const timer = window.setTimeout(() => void search(q), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [input]);

  async function pick(entry: MangaEntry) {
    setPicking(entry.anilist_id);
    try {
      await onPick(entry);
    } finally {
      setPicking(null);
    }
  }

  return (
    <Dialog open={query !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent size='xl' className='max-h-[85dvh]'>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Search by romaji or English title, or paste an AniList id or anilist.co/manga link. {description}
          </DialogDescription>
        </DialogHeader>
        <form
          className='flex flex-wrap items-center gap-2'
          onSubmit={(event) => { event.preventDefault(); void search(input); }}
        >
          <Input
            aria-label='Search AniList'
            placeholder='Title, AniList id or anilist.co link'
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className='min-w-48 flex-1'
          />
          <ToggleGroup
            value={[kind]}
            onValueChange={(values) => {
              if (!values[0]) return;
              const next = values[0] as MangaKind | 'all';
              setKind(next);
              void search(input, next);
            }}
            aria-label='Manga or light novels'
          >
            <ToggleGroupItem value='all'>Both</ToggleGroupItem>
            <ToggleGroupItem value='manga'>Manga</ToggleGroupItem>
            <ToggleGroupItem value='novel'>Light novels</ToggleGroupItem>
          </ToggleGroup>
          <Button type='submit' variant='secondary' size='icon' aria-label='Search AniList' disabled={searching}>
            {searching ? <Spinner label='Searching AniList' /> : <Search />}
          </Button>
        </form>
        <DialogBody>
          {searching && results.length === 0 ? (
            <div className='flex justify-center py-10'><Spinner label='Searching AniList' /></div>
          ) : results.length === 0 ? (
            <Empty>
              <EmptyMedia><Search /></EmptyMedia>
              <EmptyTitle>{searched ? 'Nothing found on AniList' : 'Search AniList'}</EmptyTitle>
              <EmptyDescription>Try the romaji title, the English one, or paste the anilist.co link.</EmptyDescription>
            </Empty>
          ) : (
            <ul className='flex flex-col gap-1'>
              {results.map((entry) => {
                const shown = anilistName(entry);
                const romaji = entry.romaji && entry.romaji.toLocaleLowerCase() !== shown.toLocaleLowerCase() ? entry.romaji : null;
                return (
                  <li key={entry.anilist_id} className='flex items-center gap-3 rounded-button px-2 py-2 transition-colors hover:bg-control-hover'>
                    <AnimePoster url={entry.cover} title={shown} className='w-10 shrink-0 rounded' />
                    <div className='min-w-0 flex-1'>
                      <TruncatedText text={shown} className='line-clamp-2 break-words text-sm font-medium' />
                      {romaji && <TruncatedText text={romaji} className='line-clamp-2 break-words text-xs text-muted-foreground' />}
                      <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{mangaFacts(entry)}</p>
                    </div>
                    <Button
                      size='icon-sm'
                      variant='ghost'
                      className='shrink-0 text-muted-foreground'
                      nativeButton={false}
                      render={<a href={anilistMangaUrl(entry.anilist_id)} target='_blank' rel='noreferrer' title='Open on AniList' aria-label={'Open ' + shown + ' on AniList'} />}
                    >
                      <ExternalLink />
                    </Button>
                    <Button size='sm' variant='secondary' onClick={() => void pick(entry)} disabled={picking !== null}>
                      <Check data-icon='inline-start' /> {picking === entry.anilist_id ? 'Working…' : action}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
