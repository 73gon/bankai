import { useEffect, useMemo, useState } from 'react';
import { Clock, Loader2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type BrowserAudio, type BrowserAudioFile, type BrowserAudioTrack, type TrackScopeName } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Switch } from '@/zon/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';

const CODECS: Record<string, string> = { ac3: 'AC3', eac3: 'E-AC3', dts: 'DTS', truehd: 'TrueHD', aac: 'AAC', opus: 'Opus', flac: 'FLAC', mp3: 'MP3' };

function fileName(path: string) {
  return path.split(/[\\/]/).pop() ?? path;
}

function count(value: number) {
  return value.toLocaleString('en-US');
}

function gigabytes(value: number) {
  return `${value < 0 ? '−' : '+'}${(Math.abs(value) / 1e9).toFixed(1)} GB`;
}

function layout(channels: number | null) {
  return channels === 6 ? '5.1' : channels === 8 ? '7.1' : channels === 2 ? '2.0' : channels === 1 ? '1.0' : channels ? `${channels} ch` : '';
}

function kbps(value: number | null) {
  return value ? `${Math.round(value / 1000)}k` : '';
}

function since(seconds: number) {
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

/** One audio track and what happens to it: "ger AC3 5.1 448k → AAC 448k". */
function TrackLine({ track }: { track: BrowserAudioTrack }) {
  const source = [track.lang, track.profile && track.codec === 'dts' ? track.profile : CODECS[track.codec ?? ''] ?? track.codec, layout(track.channels), kbps(track.bit_rate)]
    .filter(Boolean)
    .join(' ');
  const aacChannels = track.action === 'add' ? Math.min(track.channels ?? 2, 6) : track.channels;
  return (
    <li className='flex flex-wrap items-baseline gap-x-1.5 font-mono text-[0.7rem]'>
      <span className={track.action === 'keep' ? 'text-muted-foreground' : 'text-foreground'}>{source}</span>
      {track.name && <span className='truncate text-muted-foreground'>“{track.name}”</span>}
      {track.action === 'replace' && <span className='text-info'>→ AAC {layout(track.channels)} {kbps(track.aac_bit_rate)}</span>}
      {track.action === 'replace' && track.new_name && track.new_name !== track.name && <span className='truncate text-info'>“{track.new_name}”</span>}
      {track.action === 'add' && <span className='text-info'>kept, AAC {layout(aacChannels)} {kbps(track.aac_bit_rate)} added before it</span>}
      {track.becomes_default && !track.default && <StatusTag variant='info'>becomes default</StatusTag>}
      {track.default && !track.becomes_default && track.action !== 'add' && <StatusTag variant='muted'>default moves</StatusTag>}
    </li>
  );
}

/**
 * Tracks -> a library: Dolby and DTS audio made AAC, so browsers play the files directly. Off until
 * switched on for every library; ticked files can be tried first, even while it is off.
 */
export function BrowserAudioCard({ scope, label }: { scope: TrackScopeName; label: string }) {
  const [state, setState] = useState<BrowserAudio | null>(null);
  const [files, setFiles] = useState<BrowserAudioFile[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setState(await api.browserAudio());
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function loadFiles() {
    try {
      setFiles((await api.browserAudioFiles(scope, 60)).files);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  const preview = state?.scopes[scope];
  const totals = useMemo(() => {
    if (!state) return null;
    const rows = Object.values(state.scopes);
    return { files: rows.reduce((sum, row) => sum + row.files, 0), bytes: rows.reduce((sum, row) => sum + row.bytes, 0) };
  }, [state]);

  async function tryPicked() {
    setBusy(true);
    try {
      const answer = await api.tryBrowserAudio([...picked]);
      toast.success(`${answer.queued} file${answer.queued === 1 ? '' : 's'} queued; the next pass starts within a minute.`);
      setPicked(new Set());
      await Promise.all([load(), loadFiles()]);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function switchTo(enabled: boolean) {
    try {
      await api.setBrowserAudio(enabled);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  if (!state || !preview) return null;
  const done = state.done[scope];
  const failed = state.failed.filter((row) => row.scope === scope);
  const recent = state.recent.filter((row) => row.scope === scope).slice(-5).reverse();
  const waiting = Object.entries(state.waiting);

  return (
    <Card>
      <CardHeader className='flex flex-row flex-wrap items-start justify-between gap-3'>
        <div className='flex min-w-0 flex-col gap-1'>
          <CardTitle className='text-lg'>Audio every browser plays</CardTitle>
          <CardDescription>
            Chrome and Firefox play no AC3, E-AC3, DTS or TrueHD, so Jellyfin converts the sound on every play. Lossy Dolby and DTS
            tracks are replaced by AAC (same channels, bitrate no lower); TrueHD and DTS-HD MA are kept, and a language with nothing
            else gets an AAC beside them. The picture and every other track are copied as they are. One switch for all libraries.
          </CardDescription>
        </div>
        <Switch
          checked={state.enabled}
          aria-label='Make Dolby and DTS audio AAC in every library'
          onCheckedChange={(value) => { if (value) setConfirming(true); else void switchTo(false); }}
        />
      </CardHeader>
      <CardContent className='flex flex-col gap-2 text-sm'>
        <p>
          {preview.files ? (
            <>
              {label}: <b>{count(preview.files)}</b> file{preview.files === 1 ? '' : 's'} — <b>{count(preview.replace)}</b> track{preview.replace === 1 ? '' : 's'} made AAC
              {preview.add ? <>, <b>{count(preview.add)}</b> AAC added beside lossless</> : null}
              {preview.default ? <>, the default moved off lossless in <b>{count(preview.default)}</b></> : null}
              {' · '}about <b>{gigabytes(preview.bytes)}</b>.
            </>
          ) : <span className='text-muted-foreground'>Every file in this library already plays in browsers.</span>}
        </p>
        {preview.unsure > 0 && (
          <p className='text-xs text-muted-foreground'>
            {count(preview.unsure)} of them have DTS not read closely yet (lossy or DTS-HD MA); the counts settle once they are.
          </p>
        )}
        {state.current && (
          <p className='flex items-center gap-1.5'>
            <Loader2 className='size-3.5 animate-spin text-info' aria-hidden='true' />
            Now ({state.current.step}): <span className='truncate font-mono text-xs'>{fileName(state.current.path)}</span>
            <span className='text-muted-foreground'>started {since(state.current.started)}</span>
          </p>
        )}
        {done && done.files > 0 && (
          <p>
            Done: <b>{count(done.files)}</b> file{done.files === 1 ? '' : 's'}, {gigabytes(done.bytes)}.
          </p>
        )}
        {recent.length > 0 && (
          <ul className='flex flex-col gap-0.5 text-xs text-muted-foreground'>
            {recent.map((row) => (
              <li key={row.path + row.at} className='truncate'>
                <span className='font-mono'>{fileName(row.path)}</span>: {row.replaced} made AAC{row.added ? `, ${row.added} added` : ''}
                {row.default_moved ? ', default moved' : ''} · {since(row.at)}
              </li>
            ))}
          </ul>
        )}
        {waiting.length > 0 && (state.enabled || state.tried.length > 0) && (
          <p className='flex items-center gap-1.5 text-muted-foreground'>
            <Clock className='size-3.5' aria-hidden='true' />
            Waiting: {waiting.map(([reason, value]) => `${count(value)} ${reason}`).join(' · ')}
          </p>
        )}
        {failed.length > 0 && (
          <details className='text-xs'>
            <summary className='cursor-pointer text-warning'>{failed.length} file{failed.length === 1 ? '' : 's'} could not be written anew; each is tried again after a day</summary>
            <ul className='mt-1.5 flex flex-col gap-1'>
              {failed.map((row) => (
                <li key={row.path} className='break-all font-mono text-[0.68rem] text-muted-foreground'>{fileName(row.path)}: {row.error}</li>
              ))}
            </ul>
          </details>
        )}
        {preview.files > 0 && (
          <details className='mt-1' onToggle={(event) => { if ((event.target as HTMLDetailsElement).open && files === null) void loadFiles(); }}>
            <summary className='cursor-pointer text-sm'>Files, track by track — tick a few to try first</summary>
            {files === null ? (
              <p className='mt-2 text-muted-foreground'><Loader2 className='mr-1.5 inline size-3.5 animate-spin' aria-hidden='true' />Loading…</p>
            ) : (
              <div className='mt-2 flex flex-col gap-2'>
                <div className='flex items-center gap-2'>
                  <Button size='sm' disabled={!picked.size || busy} onClick={() => void tryPicked()}>
                    {busy && <Loader2 className='size-3.5 animate-spin' aria-hidden='true' />}
                    Try {picked.size || ''} file{picked.size === 1 ? '' : 's'} now
                  </Button>
                  <span className='text-xs text-muted-foreground'>The {files.length} with the most to add first{state.enabled ? '' : '; done one at a time even while the switch is off'}.</span>
                </div>
                <Table variant='plain' dense>
                  <TableHeader>
                    <TableRow>
                      <TableHead className='w-8'><span className='sr-only'>Pick</span></TableHead>
                      <TableHead>File</TableHead>
                      <TableHead numeric>Size change</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {files.map((file) => (
                      <TableRow key={file.path}>
                        <TableCell className='py-2 align-top'>
                          <Checkbox
                            checked={picked.has(file.path)}
                            disabled={file.tried}
                            aria-label={'Try ' + fileName(file.path)}
                            onCheckedChange={(value) => setPicked((current) => {
                              const next = new Set(current);
                              if (value) next.add(file.path); else next.delete(file.path);
                              return next;
                            })}
                          />
                        </TableCell>
                        <TableCell className='max-w-[40rem] py-2'>
                          <p className='truncate text-foreground' title={file.path}>
                            {fileName(file.path)} {file.tried && <StatusTag variant='info' className='ml-1'>Queued</StatusTag>}
                          </p>
                          <ul className='mt-0.5 flex flex-col gap-0.5'>
                            {file.tracks.map((track, index) => <TrackLine key={index} track={track} />)}
                          </ul>
                        </TableCell>
                        <TableCell numeric className='py-2 align-top text-xs text-muted-foreground'>{gigabytes(file.bytes)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </details>
        )}
      </CardContent>

      <Dialog open={confirming} onOpenChange={(open) => { if (!open) setConfirming(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Make Dolby and DTS audio AAC in every library?</DialogTitle>
            <DialogDescription>
              {count(totals?.files ?? 0)} files in anime, shows and movies will be written anew, one at a time (about {gigabytes(totals?.bytes ?? 0)} in all).
              The Dolby and DTS tracks replaced are gone for good; lossless tracks stay.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setConfirming(false)}>Cancel</Button>
            <Button onClick={() => { setConfirming(false); void switchTo(true); }}>Start</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
