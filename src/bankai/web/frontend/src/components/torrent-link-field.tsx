import { useState } from 'react';
import { FileDrop, FileDropItem, Input, Spinner, Textarea, cn } from '@zonsh/ui';
import { api } from '@/lib/api';
import { toast } from '@/lib/toast';

/** A file's bytes as base64, for the JSON upload. */
async function base64Of(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let at = 0; at < bytes.length; at += 0x8000) binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  return btoa(binary);
}

/**
 * Where a magnet link goes: pasted, or a .torrent file dropped, clicked for or pasted beside it.
 * AnimeZ (a private tracker) gives only .torrent files. The file is kept by bankai and stands
 * behind the magnet ``onChange`` is given (``torrent.uploads``), so whatever takes the magnet
 * downloads the file.
 */
export function TorrentLinkField({
  value,
  onChange,
  multiline = false,
  disabled = false,
  autoFocus = false,
  className,
}: {
  value: string;
  onChange: (magnet: string) => void;
  /** A textarea for the magnet (dialogs); a single line beside a button otherwise. */
  multiline?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  className?: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  async function take(files: File[]) {
    const picked = files[0];
    if (!picked) return;
    setUploading(true);
    try {
      const kept = await api.uploadTorrent(await base64Of(picked), picked.name);
      setFile(picked);
      onChange(kept.magnet);
      toast.success(`${kept.name}: ${kept.files} file${kept.files === 1 ? '' : 's'}${kept.private ? ', private tracker' : ''}`);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setUploading(false);
    }
  }

  function clear() {
    setFile(null);
    onChange('');
  }

  const field = multiline ? (
    <Textarea
      value={value}
      onChange={(event) => { setFile(null); onChange(event.target.value); }}
      placeholder='magnet:?xt=urn:btih:…'
      className='min-h-20 font-mono text-xs'
      aria-label='Magnet link'
      autoFocus={autoFocus}
      disabled={disabled || file !== null}
    />
  ) : (
    <Input
      value={value}
      onChange={(event) => { setFile(null); onChange(event.target.value); }}
      placeholder='Paste a magnet link, or drop a .torrent below'
      className='min-w-0 flex-1 font-mono text-xs'
      aria-label='Magnet link'
      disabled={disabled || file !== null}
    />
  );

  return (
    <div className={cn('flex min-w-0 flex-col gap-2', className)}>
      {file ? (
        <FileDropItem file={file} onRemove={disabled ? undefined : clear} labels={{ remove: (name) => `Use a magnet link instead of ${name}` }} />
      ) : (
        <>
          {field}
          <FileDrop
            accept='.torrent,application/x-bittorrent'
            multiple={false}
            maxSize={20 * 1024 * 1024}
            disabled={disabled || uploading}
            onFiles={(files) => void take(files)}
            onReject={() => toast.error('Only one .torrent file, up to 20 MB.')}
            title={uploading ? <span className='flex items-center gap-2'><Spinner className='size-4' /> Reading the torrent…</span> : 'Or drop a .torrent file'}
            hint='.torrent · AnimeZ and other private trackers give no magnet'
            className={multiline ? undefined : 'py-4'}
          />
        </>
      )}
    </div>
  );
}
