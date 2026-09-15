import { useEffect, useMemo, useState } from 'react';
import { HardDrive, RefreshCw, ArrowRight } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeLibraryEntry } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';

function formatSize(bytes: number) {
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

export default function AnimeLibrary() {
  const [entries, setEntries] = useState<AnimeLibraryEntry[]>([]);
  const [root, setRoot] = useState('');
  const [loading, setLoading] = useState(true);
  const [transferring, setTransferring] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const result = await api.animeLibrary();
      setEntries(result.entries);
      setRoot(result.root);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function transfer(entry: AnimeLibraryEntry) {
    setTransferring(entry.path);
    try {
      await api.transfer(entry.path);
      toast.success('Anime transfer started');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setTransferring(null);
    }
  }

  useEffect(() => { void load(); }, []);
  const seriesCount = useMemo(() => new Set(entries.map((entry) => entry.series)).size, [entries]);
  const totalSize = useMemo(() => entries.reduce((sum, entry) => sum + entry.size, 0), [entries]);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Library</h1>
          <p className='break-all text-sm text-muted-foreground'>{root || 'Dedicated anime destination'}</p>
        </div>
        <Button variant='secondary' onClick={() => void load()} disabled={loading}>
          <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} />
          Rescan
        </Button>
      </div>

      <div className='grid gap-3 sm:grid-cols-3'>
        <Card><CardContent className='flex flex-col gap-1 p-4'><span className='text-xs text-muted-foreground'>Series</span><strong className='font-mono text-2xl'>{seriesCount}</strong></CardContent></Card>
        <Card><CardContent className='flex flex-col gap-1 p-4'><span className='text-xs text-muted-foreground'>Episodes</span><strong className='font-mono text-2xl'>{entries.length}</strong></CardContent></Card>
        <Card><CardContent className='flex flex-col gap-1 p-4'><span className='text-xs text-muted-foreground'>Stored</span><strong className='font-mono text-2xl'>{formatSize(totalSize)}</strong></CardContent></Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>TVDB-organized Anime</CardTitle>
          <CardDescription>The separate shows_anime library and Anime files ready for transfer.</CardDescription>
        </CardHeader>
        <CardContent className='overflow-x-auto'>
          {loading ? (
            <div className='flex min-h-40 items-center justify-center'><Spinner /></div>
          ) : entries.length === 0 ? (
            <EmptyState icon={HardDrive} title='No transferred Anime yet' description='Completed downloads appear after they are organized and transferred.' />
          ) : (
            <table className='w-full min-w-[720px] border-collapse text-sm'>
              <thead><tr className='border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground'>
                <th className='px-3 py-3 font-medium'>Series</th><th className='px-3 py-3 font-medium'>Season</th><th className='px-3 py-3 font-medium'>File</th><th className='px-3 py-3 text-right font-medium'>Size</th><th className='px-3 py-3 text-right font-medium'>State</th>
              </tr></thead>
              <tbody>{entries.map((entry) => (
                <tr key={entry.path} className='border-b border-border/60 last:border-0'>
                  <td className='px-3 py-3 font-medium'>{entry.series}</td>
                  <td className='px-3 py-3'><Badge variant='secondary'>{entry.season || 'Movie'}</Badge></td>
                  <td className='px-3 py-3 font-mono text-xs text-muted-foreground'>{entry.name}</td>
                  <td className='px-3 py-3 text-right font-mono text-xs'>{formatSize(entry.size)}</td>
                  <td className='px-3 py-3 text-right'>
                    {entry.staged ? (
                      <Button size='sm' variant='secondary' onClick={() => void transfer(entry)} disabled={transferring === entry.path || entry.transfer_status === 'transferring'}>
                        <ArrowRight data-icon='inline-start' /> {entry.transfer_status === 'transferring' ? 'Transferring' : 'Transfer'}
                      </Button>
                    ) : <Badge variant='success'>In library</Badge>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}