import { useEffect, useState } from 'react';
import { Search, Save } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeTVDBMatch } from '@/lib/api';
import { AnimePoster } from '@/components/AnimePoster';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/empty';

export function AnimeMappingDialog({ title, onClose, onSaved }: { title: string | null; onClose: () => void; onSaved: () => void }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AnimeTVDBMatch[]>([]);
  const [selected, setSelected] = useState<AnimeTVDBMatch | null>(null);
  const [season, setSeason] = useState('');
  const [offset, setOffset] = useState('0');
  const [busy, setBusy] = useState(false);
  async function find(q: string) {
    setBusy(true);
    try { setItems((await api.animeTvdb(q)).items.filter((item) => item.kind === 'show')); }
    catch (error: any) { toast.error(error.message); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    if (!title) return;
    const name = title.replace(/^(?:\s*\[[^\]]+\])+\s*/, '').replace(/\s+-\s+\d+(?:v\d+)?\s*(?:\[[^\]]*\]\s*)*$/, '').replace(/\s+Part\s+\d+$/i, '');
    setQuery(name); setSelected(null); setItems([]); setSeason(''); setOffset('0');
    void find(name);
  }, [title]);
  async function save() {
    if (!title || !selected) return;
    const seasonNumber = season.trim() ? Number(season) : undefined;
    const episodeOffset = Number(offset);
    if ((seasonNumber !== undefined && (!Number.isInteger(seasonNumber) || seasonNumber < 1)) || !Number.isInteger(episodeOffset)) { toast.error('Enter a valid season and whole-number offset'); return; }
    setBusy(true);
    try {
      await api.saveAnimeMapping(title, selected.tvdb_id, seasonNumber, episodeOffset);
      toast.success('TVDB selection saved for all releases with this source show name');
      onSaved(); onClose();
    } catch (error: any) { toast.error(error.message); }
    finally { setBusy(false); }
  }
  return <Dialog open={Boolean(title)} onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className='flex h-[90dvh] w-[92vw] max-w-5xl flex-col overflow-hidden'>
      <DialogHeader><DialogTitle>Choose the TVDB show</DialogTitle><DialogDescription>This selection is remembered for future releases with the exact same source show name.</DialogDescription></DialogHeader>
      <p className='break-words font-mono text-xs text-muted-foreground'>{title}</p>
      <form className='flex gap-2' onSubmit={(event) => { event.preventDefault(); void find(query); }}><Input aria-label='TVDB show name' value={query} onChange={(event) => setQuery(event.target.value)} className='flex-1' /><Button type='submit' disabled={busy || query.length < 2}><Search data-icon='inline-start' /> Search</Button></form>
      <div className='grid min-h-0 flex-1 gap-3 overflow-y-auto sm:grid-cols-2'>
        {busy && !items.length ? <Spinner /> : items.map((item) => <button type='button' key={item.tvdb_id} onClick={() => setSelected(item)} aria-pressed={selected?.tvdb_id === item.tvdb_id} className={'flex h-fit gap-3 rounded-md border p-3 text-left ' + (selected?.tvdb_id === item.tvdb_id ? 'border-success bg-success/10' : 'border-border hover:bg-accent')}><AnimePoster url={item.poster_url} title={item.english_title} className='w-20 shrink-0' /><span className='flex flex-col gap-2'><span className='font-semibold'>{item.english_title}{item.year ? ' (' + item.year + ')' : ''}</span><span className='text-xs text-muted-foreground'>{item.japanese_title}</span><span className='text-xs text-muted-foreground'>TVDB {item.tvdb_id}</span></span></button>)}
      </div>
      <div className='grid shrink-0 gap-3 sm:grid-cols-2'>
        <label className='flex flex-col gap-1 text-sm'>Season (optional)<Input aria-label='TVDB season' type='number' min='1' placeholder='Use automatic episode mapping' value={season} onChange={(event) => setSeason(event.target.value)} /></label>
        <label className='flex flex-col gap-1 text-sm'>Episode offset<Input aria-label='Episode offset' type='number' disabled={!season.trim()} value={offset} onChange={(event) => setOffset(event.target.value)} /></label>
        <p className='text-xs text-muted-foreground sm:col-span-2'>For a continuation such as Part 2, season 1 and offset 12 maps release episode 11 to TVDB S01E23. Leave the season empty for automatic AniDB/TheXEM or absolute numbering.</p>
      </div>
      <DialogFooter><Button variant='secondary' onClick={onClose}>Cancel</Button><Button onClick={() => void save()} disabled={busy || !selected}><Save data-icon='inline-start' /> Save show mapping</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}