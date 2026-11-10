import { useEffect, useState } from 'react';
import { Magnet } from 'lucide-react';
import { StatusTag } from '@/components/status-tag';
import { api, type MagnetMissingEpisode, type MagnetPreview } from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/utils';
import { Button } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Textarea } from '@zonsh/ui';

/**
 * Replace ticked library files with a magnet link's torrent. ``onSubmit`` queues it and returns
 * whether it went; a refusal that can be confirmed (files with German would go) is answered by
 * ``confirm`` being set, and the second press sends ``force``.
 */
export type MagnetMissing = MagnetMissingEpisode;

export function MagnetReplaceDialog({ open, count, what, paths, missing = [], scope, onClose, onSubmit }: {
  open: boolean;
  /** The ticked files. */
  count: number;
  /** Ticked anime episodes with no file: downloaded from the same torrent. */
  missing?: MagnetMissingEpisode[];
  /** The ticked files and their library: with them the dialog shows what the magnet is and what each of
   *  its files would replace, read from the swarm before anything is downloaded. */
  paths?: string[];
  scope?: 'anime' | 'show' | 'movie';
  /** "episode", "file": what is ticked, for the wording. */
  what: string;
  onClose: () => void;
  onSubmit: (magnet: string, force: boolean) => Promise<{ ok: boolean; confirm?: string }>;
}) {
  const [magnet, setMagnet] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setMagnet('');
      setConfirm(null);
    }
  }, [open]);

  const valid = /^magnet:\?.*xt=urn:btih:/i.test(magnet.trim());
  const preview = useMagnetPreview(open && valid && scope ? magnet.trim() : '', paths ?? [], scope ?? 'anime', missing);
  const filling = missing.length;
  const heading = count && filling
    ? `Replace ${count} ${what}${count === 1 ? '' : 's'} and download ${filling} with a magnet link`
    : filling
      ? `Download ${filling} missing ${what}${filling === 1 ? '' : 's'} with a magnet link`
      : `Replace ${count} ${what}${count === 1 ? '' : 's'} with a magnet link`;
  async function submit() {
    setBusy(true);
    try {
      const result = await onSubmit(magnet.trim(), confirm !== null);
      if (result.ok) onClose();
      else if (result.confirm) setConfirm(result.confirm);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !busy) onClose(); }}>
      <DialogContent size='xl'>
        <DialogHeader>
          <DialogTitle>{heading}</DialogTitle>
          <DialogDescription>
            Paste the magnet link of a torrent that holds {count + filling === 1 ? `this ${what}` : `these ${what}s`} — one episode for one
            episode, a season pack for a season. Each file of the torrent replaces the ticked one of its episode{filling ? ', or fills a ticked missing one' : ''};
            the rest of the torrent is left out. The old files go once the new ones are in the library.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          <Textarea
            value={magnet}
            onChange={(event) => { setMagnet(event.target.value); setConfirm(null); }}
            placeholder='magnet:?xt=urn:btih:…'
            className='min-h-24 font-mono text-xs'
            aria-label='Magnet link'
            autoFocus
          />
          {magnet.trim() && !valid && <p className='text-xs text-warning'>That is not a magnet link (magnet:?xt=urn:btih:…).</p>}
          {preview.state !== 'idle' && <PreviewPanel preview={preview} />}
          {confirm && <p className='text-sm text-warning'>{confirm}</p>}
        </DialogBody>
        <DialogFooter>
          <Button variant='ghost' onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant={confirm ? 'destructive' : 'primary'} onClick={() => void submit()} disabled={!valid || busy}>
            <Magnet data-icon='inline-start' /> {confirm ? 'Replace anyway' : count ? 'Replace' : 'Download'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type PreviewState =
  | { state: 'idle' }
  | { state: 'asking'; since: number; name?: string }
  | { state: 'ready'; data: MagnetPreview }
  | { state: 'failed'; error: string };

/** The magnet looked at, asked again every few seconds while the swarm has not answered (up to two minutes). */
function useMagnetPreview(magnet: string, paths: string[], scope: 'anime' | 'show' | 'movie', missing: MagnetMissingEpisode[] = []): PreviewState {
  const [result, setResult] = useState<PreviewState>({ state: 'idle' });
  const key = paths.join('\n') + '|' + missing.map((row) => `${row.anilist_id}:${row.anilist_episode}`).join(',');
  useEffect(() => {
    if (!magnet) {
      setResult({ state: 'idle' });
      return;
    }
    let alive = true;
    const since = Date.now();
    setResult({ state: 'asking', since });
    const ask = async () => {
      try {
        const data = await api.magnetPreview(magnet, paths, scope, missing);
        if (!alive) return;
        if (!data.pending) setResult({ state: 'ready', data });
        else if (Date.now() - since > 120_000) setResult({ state: 'failed', error: 'No peer answered with the torrent\'s files in two minutes: it may be dead.' });
        else {
          setResult({ state: 'asking', since, name: data.name });
          window.setTimeout(() => { if (alive) void ask(); }, 2000);
        }
      } catch (error: any) {
        if (alive) setResult({ state: 'failed', error: error.message });
      }
    };
    const timer = window.setTimeout(() => void ask(), 400);
    return () => { alive = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [magnet, key, scope]);
  return result;
}

function PreviewPanel({ preview }: { preview: PreviewState }) {
  if (preview.state === 'asking') {
    return (
      <p className='flex items-center gap-2 text-sm text-muted-foreground'>
        <Spinner className='size-4' /> Asking the swarm for the torrent's files{preview.name ? ' — ' + preview.name : ''}…
      </p>
    );
  }
  if (preview.state === 'failed') return <p className='text-sm text-warning'>{preview.error}</p>;
  if (preview.state !== 'ready') return null;
  const data = preview.data;
  const files = data.files ?? [];
  const replacing = files.filter((file) => file.replaces);
  const erai = data.known?.erai;
  return (
    <div className='flex flex-col gap-2 rounded-control surface-container p-3 text-sm'>
      <p className='break-all font-medium text-foreground'>{data.name}</p>
      <p className='flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground'>
        <span>{formatBytes(data.size ?? 0)}</span>
        <span>{files.length} file{files.length === 1 ? '' : 's'}</span>
        <span className='font-mono'>{data.info_hash.slice(0, 12)}…</span>
        {data.private && <StatusTag variant='warning'>Private torrent</StatusTag>}
        {erai && <span>Erai-raws{erai.resolution ? ' · ' + erai.resolution : ''}{erai.published ? ' · ' + timeAgo(erai.published) : ''}</span>}
        {erai && <StatusTag variant={erai.german ? 'success' : 'warning'} title={erai.subtitles.join(', ')}>{erai.german ? 'German subtitles' : 'No German subtitles'}</StatusTag>}
        {data.known?.release && <StatusTag variant='muted' title={data.known.release.title}>bankai: {data.known.release.status}</StatusTag>}
      </p>
      <p className={replacing.length ? 'text-xs text-foreground' : 'text-xs text-warning'}>
        {replacing.length
          ? [
              data.covered! - (data.filled ?? 0) > 0 && `Replaces ${data.covered! - (data.filled ?? 0)}`,
              (data.filled ?? 0) > 0 && `downloads ${data.filled} missing`,
            ].filter(Boolean).join(', ') + ` of the ${data.ticked} ticked episode${data.ticked === 1 ? '' : 's'}`
          : 'None of its files is one of the ticked episodes: nothing would be taken.'}
        {(data.uncovered?.length ?? 0) > 0 && replacing.length > 0 && <span className='text-warning'> · not in it: {data.uncovered!.join(', ')}</span>}
      </p>
      <ul className='flex max-h-56 flex-col gap-0.5 overflow-y-auto text-xs'>
        {files.map((file) => (
          <li key={file.path} className='flex items-baseline gap-2'>
            <span className={'min-w-0 flex-1 truncate font-mono ' + (file.replaces ? 'text-foreground' : 'text-muted-foreground')} title={file.path}>{file.name}</span>
            <span className='shrink-0 tabular-nums text-muted-foreground'>{formatBytes(file.size)}</span>
            <span className={'w-56 shrink-0 truncate text-right ' + (file.replaces ? 'text-success' : 'text-muted-foreground')} title={file.replaces ?? file.why}>
              {file.replaces ? (file.fills ? '→ ' + file.replaces + ' (new)' : '→ ' + file.replaces) : file.why}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
