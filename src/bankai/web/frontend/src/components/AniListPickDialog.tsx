import { useEffect, useRef, useState } from 'react';
import { Check, ExternalLink, Search } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AniListAnime } from '@/lib/api';
import { anilistName, anilistUrl, formatLabel } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { TruncatedText } from '@/components/TruncatedText';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';

/** How long typing pauses before the search runs. */
const DEBOUNCE_MS = 400;

/** Pick an AniList entry: searched by title, or found from a pasted anilist.co link or id. */
export function AniListPickDialog({
  query,
  onClose,
  onPick,
  title = 'Link to AniList',
  description = "Every release named after any of the entry's titles is matched from then on.",
  action = 'Link',
}: {
  /** What to search for first; null closes the dialog. */
  query: string | null;
  onClose: () => void;
  onPick: (anime: AniListAnime) => Promise<void>;
  title?: string;
  description?: string;
  /** The pick button's label. */
  action?: string;
}) {
  const [input, setInput] = useState('');
  const [results, setResults] = useState<AniListAnime[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
  // Only the newest search may answer: an older one finishing late is dropped.
  const latest = useRef(0);
  const lastQuery = useRef('');

  async function search(value: string) {
    const q = value.trim();
    if (!q) return;
    lastQuery.current = q;
    const mine = ++latest.current;
    setSearching(true);
    try {
      const found = (await api.anilistSearch(q)).items;
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
    setInput(query);
    setResults([]);
    setSearched(false);
    void search(query);
  }, [query]);

  // Typed: searched once typing pauses.
  useEffect(() => {
    if (query === null) return;
    const q = input.trim();
    if (!q || q === lastQuery.current) return;
    const timer = window.setTimeout(() => void search(q), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [input]);

  async function pick(anime: AniListAnime) {
    setPicking(anime.anilist_id);
    try {
      await onPick(anime);
    } finally {
      setPicking(null);
    }
  }

  return (
    <Dialog open={query !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      {/* Wide: titles run long, and the whole of one is often what tells two entries apart. */}
      <DialogContent className='flex max-h-[85dvh] w-[min(56rem,calc(100vw-2rem))] max-w-4xl flex-col'>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Search by romaji or English title, or paste an AniList id or anilist.co link. {description}
          </DialogDescription>
        </DialogHeader>
        <form
          className='flex items-center gap-2'
          onSubmit={(event) => { event.preventDefault(); void search(input); }}
        >
          <Input
            aria-label='Search AniList'
            placeholder='Title, AniList id or anilist.co link'
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className='flex-1'
          />
          <Button type='submit' variant='secondary' size='icon' aria-label='Search AniList' disabled={searching}>
            {searching ? <Spinner /> : <Search />}
          </Button>
        </form>
        <div className='min-h-0 flex-1 overflow-y-auto'>
          {searching && results.length === 0 ? (
            <div className='flex justify-center py-10'><Spinner /></div>
          ) : results.length === 0 ? (
            <EmptyState
              icon={Search}
              title={searched ? 'Nothing found on AniList' : 'Search AniList'}
              description='Try the romaji title Erai-raws uses, the English one, or paste the anilist.co link.'
            />
          ) : (
            <ul className='flex flex-col gap-1'>
              {results.map((anime) => {
                const shown = anilistName(anime);
                const romaji = anime.romaji && anime.romaji.toLocaleLowerCase() !== shown.toLocaleLowerCase() ? anime.romaji : null;
                return (
                  <li key={anime.anilist_id} className='flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-accent/40'>
                    <AnimePoster url={anime.cover} title={shown} className='w-10 shrink-0 rounded' />
                    <div className='min-w-0 flex-1'>
                      {/* Two lines before they are cut; hovering shows the rest. */}
                      <TruncatedText text={shown} className='line-clamp-2 break-words text-sm font-medium' />
                      {romaji && <TruncatedText text={romaji} className='line-clamp-2 break-words text-xs text-muted-foreground' />}
                      <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                        {[formatLabel(anime.format), anime.episodes ? anime.episodes + ' eps' : null, anime.year, 'id ' + anime.anilist_id]
                          .filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    <Button asChild size='icon' variant='ghost' className='shrink-0 text-muted-foreground'>
                      <a href={anilistUrl(anime.anilist_id)} target='_blank' rel='noreferrer' title='Open on AniList' aria-label={'Open ' + shown + ' on AniList'}>
                        <ExternalLink />
                      </a>
                    </Button>
                    <Button size='sm' onClick={() => void pick(anime)} disabled={picking !== null}>
                      <Check data-icon='inline-start' /> {picking === anime.anilist_id ? 'Working…' : action}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
