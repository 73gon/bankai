import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type TrackScopeName, type UnknownTrackFile, type UnknownTrackTitle } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Input } from '@/zon/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Spinner } from '@/zon/ui/spinner';

/** The languages offered first; any other code works too. */
const LANGUAGES: [string, string][] = [
  ['ger', 'German'], ['eng', 'English'], ['jpn', 'Japanese'], ['fre', 'French'], ['spa', 'Spanish'], ['ita', 'Italian'],
  ['por', 'Portuguese'], ['rus', 'Russian'], ['kor', 'Korean'], ['chi', 'Chinese'], ['ara', 'Arabic'], ['tur', 'Turkish'],
  ['pol', 'Polish'], ['dut', 'Dutch'], ['swe', 'Swedish'], ['dan', 'Danish'], ['nor', 'Norwegian'], ['fin', 'Finnish'],
  ['hin', 'Hindi'], ['zxx', 'No language (music, effects)'], ['__other', 'Other code…'],
];
const LANGUAGE_ITEMS = LANGUAGES.map(([value, label]) => ({ value, label }));

function size(bytes: number | null) {
  if (!bytes) return null;
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(1) + ' GiB' : Math.round(bytes / 1024 ** 2) + ' MiB';
}

/** Tracks that look alike in one title -- same place, name and codec -- set together. */
type Group = { key: string; position: number; name: string | null; codec: string | null; forced: boolean; files: UnknownTrackFile[] };

function groups(files: UnknownTrackFile[]): Group[] {
  const found = new Map<string, Group>();
  for (const file of files) {
    for (const track of file.tracks) {
      const key = [track.position, track.name ?? '', track.codec ?? '', track.forced].join('|');
      const group = found.get(key) ?? { key, position: track.position, name: track.name, codec: track.codec, forced: track.forced, files: [] };
      group.files.push(file);
      found.set(key, group);
    }
  }
  return [...found.values()].sort((a, b) => b.files.length - a.files.length || a.position - b.position);
}

function TrackGroup({ group, kind, current, onDone, onBackground }: {
  group: Group;
  kind: 'audio' | 'subtitles';
  /** The language these tracks have now: setting the same one again does nothing. */
  current: string;
  onDone: () => void;
  onBackground: (files: number) => void;
}) {
  const [language, setLanguage] = useState(current === 'ger' ? 'eng' : 'ger');
  const [other, setOther] = useState('');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const usable = group.files.filter((file) => file.mkv);
  const code = language === '__other' ? other.trim().toLowerCase() : language;

  async function remove() {
    setSaving(true);
    try {
      const result = await api.removeTracks(group.files.map((file) => ({ path: file.path, kind, position: group.position })));
      toast.success(`Queued: ${result.queued} track${result.queued === 1 ? '' : 's'} to be taken out; the cleanup writes each file anew, one at a time`);
      setRemoving(false);
      onDone();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  async function apply() {
    setSaving(true);
    try {
      const result = await api.setTrackLanguage(usable.map((file) => ({ path: file.path, kind, position: group.position })), code);
      if (result.background) {
        // Many files: written in the background; the dialog shows how far it is.
        onBackground(result.files);
        return;
      }
      toast.success(`${result.name} written into ${result.files} file${result.files === 1 ? '' : 's'}`);
      for (const failure of (result.failed ?? []).slice(0, 3)) toast.error(`${failure.path.split('/').pop()}: ${failure.error}`);
      onDone();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className='flex flex-col gap-2 rounded-control border border-border/60 p-3'>
      <div className='flex flex-wrap items-center gap-2'>
        <button type='button' className='flex min-w-0 items-center gap-1.5 text-left focus-ring' onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? <ChevronDown className='size-4 shrink-0' /> : <ChevronRight className='size-4 shrink-0' />}
          <span className='font-medium'>{kind === 'audio' ? 'Audio' : 'Subtitle'} track {group.position}</span>
          <span className='truncate text-sm text-muted-foreground' title={group.name ?? undefined}>
            {group.name ? `“${group.name}”` : 'no name'}
          </span>
        </button>
        {group.codec && <StatusTag variant='muted'>{group.codec}</StatusTag>}
        {group.forced && <StatusTag variant='warning'>forced</StatusTag>}
        <span className='text-xs text-muted-foreground'>in {group.files.length} file{group.files.length === 1 ? '' : 's'}</span>
        <span className='ml-auto flex items-center gap-2'>
          <Select items={LANGUAGE_ITEMS} value={language} onValueChange={(value) => value !== null && setLanguage(String(value))}>
            <SelectTrigger size='sm' aria-label='Language' className='w-44'><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {LANGUAGE_ITEMS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
              </SelectGroup>
            </SelectContent>
          </Select>
          {language === '__other' && (
            <Input className='w-24' value={other} onChange={(event) => setOther(event.target.value)} placeholder='e.g. hun' aria-label='Language code' />
          )}
          <Button size='sm' onClick={() => void apply()} disabled={saving || usable.length === 0 || !code || code === '__other' || code === current} title={usable.length < group.files.length ? 'Only MKV files can be tagged here' : undefined}>
            {saving ? <Spinner /> : null} Set {usable.length > 1 ? `all ${usable.length}` : ''}
          </Button>
          <Button size='sm' variant='ghost' onClick={() => setRemoving(true)} disabled={saving} title='Take this track out of the file'>
            <Trash2 data-icon='inline-start' /> Remove
          </Button>
        </span>
      </div>
      {removing && (
        <div className='flex flex-wrap items-center gap-2 rounded-control bg-destructive/10 px-3 py-2 text-sm'>
          <span className='min-w-0 flex-1'>
            Take {kind === 'audio' ? 'audio' : 'subtitle'} track {group.position} out of {group.files.length === 1 ? 'this file' : `these ${group.files.length} files`}?
            The cleanup writes each file anew without it (lossless, checked before it replaces the original). A file never loses its last audio track.
          </span>
          <Button size='sm' variant='ghost' onClick={() => setRemoving(false)} disabled={saving}>Cancel</Button>
          <Button size='sm' variant='destructive' onClick={() => void remove()} disabled={saving}>Remove</Button>
        </div>
      )}
      {open && (
        <ul className='flex max-h-48 flex-col gap-0.5 overflow-y-auto pl-6'>
          {group.files.map((file) => {
            const track = file.tracks.find((row) => row.position === group.position);
            return (
              <li key={file.path} className='flex items-center gap-2 font-mono text-[0.68rem] text-muted-foreground'>
                <span className='min-w-0 flex-1 truncate' title={file.path}>{file.relative}</span>
                {track?.default && <span>default</span>}
                {size(track?.bytes ?? null) && <span>{size(track?.bytes ?? null)}</span>}
                {!file.mkv && <span className='text-warning'>not MKV</span>}
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

/**
 * The tracks of a library in one language, title by title: each grouped by place, name and codec --
 * the name is what Jellyfin and ZonTV show too -- to set a (another) language for, or take out.
 * Without ``language``, the tracks that name none ("Unknown").
 */
export function UnknownTracksDialog({ scope, kind, language = { code: 'und', name: 'Unknown' }, onClose, onChanged }: {
  scope: TrackScopeName | null;
  kind: 'audio' | 'subtitles';
  language?: { code: string; name: string };
  onClose: () => void;
  onChanged: () => void;
}) {
  const unknown = language.code === 'und';
  const [titles, setTitles] = useState<UnknownTrackTitle[] | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [files, setFiles] = useState<UnknownTrackFile[] | null>(null);
  // A language being written into many files in the background.
  const [writing, setWriting] = useState<{ done: number; total: number } | null>(null);

  useEffect(() => {
    if (!writing) return;
    const timer = window.setInterval(async () => {
      try {
        const now = await api.trackLanguageProgress();
        if (now.total) setWriting({ done: now.done ?? 0, total: now.total });
        else {
          setWriting(null);
          toast.success('The language is written into every file');
          if (title) void loadFiles(title);
          onChanged();
        }
      } catch {
        /* asked again on the next tick */
      }
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writing !== null, title]);

  async function loadTitles() {
    if (!scope) return;
    try {
      setTitles((await api.unknownTrackTitles(scope, kind, language.code)).titles);
    } catch (error: any) {
      toast.error(error.message);
    }
  }
  async function loadFiles(name: string) {
    if (!scope) return;
    setFiles(null);
    try {
      setFiles((await api.unknownTrackFiles(scope, kind, name, language.code)).files);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    setTitles(null);
    setTitle(null);
    setFiles(null);
    void loadTitles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, kind, language.code]);

  const grouped = useMemo(() => groups(files ?? []), [files]);
  const what = kind === 'audio' ? 'audio' : 'subtitle';

  return (
    <Dialog open={scope !== null} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent size='xl' className='max-h-[90dvh]'>
        <DialogHeader>
          <DialogTitle>{title ?? `${language.name} ${what} tracks`}</DialogTitle>
          <DialogDescription>
            {title
              ? unknown
                ? `Tracks that name no language, grouped where they look alike. The name is what Jellyfin and ZonTV show; play one there, then set its language here. It is written into the file (MKV, in seconds) and Jellyfin reads it again.`
                : `${language.name} ${what} tracks, grouped where they look alike. One tagged wrongly gets its right language here, written into the file (MKV, in seconds); Remove takes a track out of its files.`
              : unknown
                ? `Every title with ${what} tracks that name no language. Pick one to see them.`
                : `Every title with ${language.name} ${what} tracks. Pick one to see its files and tracks.`}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          {writing && (
            <p className='flex items-center gap-2 rounded-control bg-control-surface px-3 py-2 text-sm'>
              <Spinner /> Writing the language into the files: <span className='font-mono tabular-nums'>{writing.done} of {writing.total}</span>
              <span className='text-muted-foreground'>— it goes on if you close this.</span>
            </p>
          )}
          {title !== null && (
            <div>
              <Button size='sm' variant='ghost' onClick={() => { setTitle(null); setFiles(null); void loadTitles(); }}>← All titles</Button>
            </div>
          )}
          {title === null ? (
            titles === null ? <div className='flex h-40 items-center justify-center'><Spinner /></div>
              : titles.length === 0 ? <p className='text-sm text-muted-foreground'>{unknown ? `No ${what} track without a language left.` : `No ${language.name} ${what} track left.`}</p>
              : (
                <ul className='flex flex-col gap-1'>
                  {titles.map((row) => (
                    <li key={row.title}>
                      <button
                        type='button'
                        onClick={() => { setTitle(row.title); void loadFiles(row.title); }}
                        className='flex w-full items-center gap-3 rounded-item px-2 py-1.5 text-left material-row focus-ring'
                      >
                        <span className='min-w-0 flex-1 truncate'>{row.title}</span>
                        {row.names.length > 0 && <span className='hidden max-w-64 truncate text-xs text-muted-foreground sm:inline'>{row.names.map((name) => `“${name}”`).join(', ')}</span>}
                        <span className='font-mono text-xs tabular-nums text-muted-foreground'>{row.files} file{row.files === 1 ? '' : 's'}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )
          ) : files === null ? <div className='flex h-40 items-center justify-center'><Spinner /></div>
            : grouped.length === 0 ? <p className='text-sm text-muted-foreground'>{unknown ? `All set: no ${what} track of ${title} is unknown now.` : `${title} has no ${language.name} ${what} track now.`}</p>
            : (
              <ul className='flex flex-col gap-2'>
                {grouped.map((group) => (
                  <TrackGroup
                    key={group.key}
                    group={group}
                    kind={kind}
                    current={language.code}
                    onDone={() => { void loadFiles(title); onChanged(); }}
                    onBackground={(count) => setWriting({ done: 0, total: count })}
                  />
                ))}
              </ul>
            )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
