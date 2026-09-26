import { useEffect, useState } from 'react';
import { Check, Search } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AniDBAnime } from '@/lib/api';
import { AnimePoster } from '@/components/AnimePoster';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';

/** Pick the AniDB anime a name belongs to, searched through Shoko. */
export function AniDBLinkDialog({
  name,
  onClose,
  onPick,
}: {
  /** What to search for first; null closes the dialog. */
  name: string | null;
  onClose: () => void;
  onPick: (anime: AniDBAnime) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<AniDBAnime[]>([]);
  const [searching, setSearching] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);

  async function search(value: string) {
    if (!value.trim()) return;
    setSearching(true);
    try {
      setResults((await api.anidbSearch(value.trim())).items);
    } catch (error: any) {
      toast.error(error.message);
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  useEffect(() => {
    if (name === null) return;
    setQuery(name);
    setResults([]);
    void search(name);
  }, [name]);

  async function pick(anime: AniDBAnime) {
    setPicking(anime.anidb_id);
    try {
      await onPick(anime);
    } finally {
      setPicking(null);
    }
  }

  return (
    <Dialog open={name !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='flex max-h-[85dvh] max-w-2xl flex-col'>
        <DialogHeader>
          <DialogTitle>Link to AniDB</DialogTitle>
          <DialogDescription>
            Search by romaji or English title, or paste an AniDB id or anidb.net link.
            Every release named after any of the anime&apos;s AniDB titles is matched from then on.
          </DialogDescription>
        </DialogHeader>
        <form
          className='flex items-center gap-2'
          onSubmit={(event) => { event.preventDefault(); void search(query); }}
        >
          <Input aria-label='Search AniDB' placeholder='Title, AniDB id or anidb.net link' value={query} onChange={(event) => setQuery(event.target.value)} className='flex-1' />
          <Button type='submit' variant='secondary' size='icon' aria-label='Search AniDB' disabled={searching}><Search /></Button>
        </form>
        <div className='min-h-0 flex-1 overflow-y-auto'>
          {searching ? (
            <div className='flex justify-center py-10'><Spinner /></div>
          ) : results.length === 0 ? (
            <EmptyState icon={Search} title='No AniDB anime found' description='Try the romaji title Erai-raws uses, or the English one.' />
          ) : (
            <ul className='flex flex-col gap-1'>
              {results.map((anime) => (
                <li key={anime.anidb_id} className='flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-accent/40'>
                  <AnimePoster url={anime.poster_url} title={anime.title} className='w-10 shrink-0 rounded' />
                  <div className='min-w-0 flex-1'>
                    <p className='truncate text-sm font-medium' title={anime.title}>{anime.title}</p>
                    {anime.english_title && anime.english_title.toLocaleLowerCase() !== anime.title.toLocaleLowerCase() && (
                      <p className='truncate text-xs text-muted-foreground' title='English title'>{anime.english_title}</p>
                    )}
                    <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                      {/* Outside the Shoko collection only the title list is known,
                          and the type reads "Unknown", which says nothing. */}
                      {[anime.type === 'Unknown' ? null : anime.type, anime.year, anime.episode_count ? anime.episode_count + ' eps' : null, 'aid ' + anime.anidb_id]
                        .filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <Button size='sm' onClick={() => void pick(anime)} disabled={picking !== null}>
                    <Check data-icon='inline-start' /> {picking === anime.anidb_id ? 'Linking…' : 'Link'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
