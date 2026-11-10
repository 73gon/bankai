import { useState, type FormEvent } from 'react';
import { BookOpen, Download, ExternalLink, Search, Star } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import { api, type MangaEntry, type MangaSearchRow } from '@/lib/api';
import { anilistName } from '@/lib/anilist';
import { formatBytes } from '@/lib/utils';
import { MangaPickDialog } from '@/components/MangaPickDialog';
import { StatusTag } from '@/components/status-tag';
import { TruncatedText } from '@/components/TruncatedText';
import { releaseSpan } from '@/pages/MangaReview';
import { Button } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';

const KNOWN: Record<string, string> = {
  review: 'In the review',
  queued: 'Downloading',
  downloading: 'Downloading',
  publishing: 'Filing',
  done: 'In the library',
  discarded: 'Discarded',
  owned: 'Had it all',
  failed: 'Failed',
};

export default function MangaSearch() {
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<MangaSearchRow[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [downloadFor, setDownloadFor] = useState<MangaSearchRow | null>(null);
  const [follow, setFollow] = useState<string | null>(null);

  async function search(event?: FormEvent) {
    event?.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    try {
      setRows((await api.mangaSearch(q)).items);
      setSearched(true);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Manga search</h1>
          <p className='text-sm text-muted-foreground'>
            nyaa’s English literature, searched by hand. Downloading a release here approves it at once; to have bankai look for a series by
            itself, follow it.
          </p>
        </div>
        <Button variant='secondary' onClick={() => setFollow(query.trim() || ' ')}>
          <Star data-icon='inline-start' /> Follow a series
        </Button>
      </div>
      <form onSubmit={(event) => void search(event)} className='flex items-center gap-2'>
        <InputGroup className='w-full max-w-xl'>
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput placeholder='A series, as releases name it: Frieren, Oshi no Ko, Mushoku Tensei' value={query} onChange={(event) => setQuery(event.target.value)} />
        </InputGroup>
        <Button type='submit' disabled={searching || !query.trim()}>{searching ? <Spinner label='Searching nyaa' /> : 'Search'}</Button>
      </form>
      {rows.length === 0 ? (
        <Empty>
          <EmptyMedia><BookOpen /></EmptyMedia>
          <EmptyTitle>{searched ? 'Nothing found on nyaa' : 'Search nyaa'}</EmptyTitle>
          <EmptyDescription>Fewer words find more: nyaa wants every word of the search in the release’s name.</EmptyDescription>
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Release</TableHead>
              <TableHead>Series</TableHead>
              <TableHead className='text-right'>Size</TableHead>
              <TableHead className='text-right'>Seeders</TableHead>
              <TableHead className='text-right' />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.info_hash}>
                <TableCell className='max-w-xl'>
                  <TruncatedText text={row.title} className='line-clamp-2 break-words text-sm' />
                  <div className='mt-1 flex flex-wrap gap-1'>
                    {releaseSpan(row) && <StatusTag variant='secondary'>{releaseSpan(row)}</StatusTag>}
                    {row.kind && <StatusTag variant='muted'>{row.kind === 'novel' ? 'Light novel' : 'Manga'}</StatusTag>}
                    {row.audiobook && <StatusTag variant='muted'>Audiobook</StatusTag>}
                    {row.trusted && <StatusTag variant='success'>Trusted</StatusTag>}
                    {row.status && <StatusTag variant='info'>{KNOWN[row.status] ?? row.status}</StatusTag>}
                  </div>
                </TableCell>
                <TableCell className='max-w-56 text-sm'>
                  {row.match ? <TruncatedText text={anilistName(row.match)} className='line-clamp-2' /> : <span className='text-muted-foreground'>Not followed</span>}
                </TableCell>
                <TableCell className='whitespace-nowrap text-right font-mono text-xs tabular-nums'>{formatBytes(row.size_bytes)}</TableCell>
                <TableCell className='text-right font-mono text-xs tabular-nums'>{row.seeders}</TableCell>
                <TableCell>
                  <div className='flex justify-end gap-1'>
                    <Button
                      size='icon-sm'
                      variant='ghost'
                      className='text-muted-foreground'
                      nativeButton={false}
                      render={<a href={row.detail_url} target='_blank' rel='noreferrer' title='The release on nyaa' aria-label={row.title + ' on nyaa'} />}
                    >
                      <ExternalLink />
                    </Button>
                    <Button size='sm' variant='secondary' disabled={['queued', 'downloading', 'publishing', 'done'].includes(row.status ?? '')} onClick={() => setDownloadFor(row)}>
                      <Download data-icon='inline-start' /> Download
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <MangaPickDialog
        query={downloadFor ? (downloadFor.match ? anilistName(downloadFor.match) : query.trim()) : null}
        kind={downloadFor?.kind ?? null}
        title='Which series is this release?'
        description='It is downloaded and filed into that series’ folder.'
        action='Download'
        onClose={() => setDownloadFor(null)}
        onPick={async (entry: MangaEntry) => {
          const release = downloadFor;
          if (!release) return;
          setDownloadFor(null);
          void inBackground(api.downloadManga(release, entry.anilist_id), {
            loading: 'Queuing ' + release.title + '…',
            success: 'Downloading ' + release.title,
          }).then(() => setRows((current) => current.map((row) => row.info_hash === release.info_hash ? { ...row, status: 'queued' } : row)), () => undefined);
        }}
      />
      <MangaPickDialog
        query={follow}
        title='Follow a series'
        description='bankai searches nyaa for it from the next pass, and every release it finds waits in the review.'
        action='Follow'
        onClose={() => setFollow(null)}
        onPick={async (entry: MangaEntry) => {
          setFollow(null);
          void inBackground(api.followMangaSeries(entry.anilist_id), {
            loading: 'Following ' + anilistName(entry) + '…',
            success: 'Following ' + anilistName(entry),
          }).catch(() => undefined);
        }}
      />
    </div>
  );
}
