import { useEffect, useState } from 'react';
import { ArrowRight, FolderInput } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type MangaEntry } from '@/lib/api';
import { anilistName } from '@/lib/anilist';
import { MangaPickDialog } from '@/components/MangaPickDialog';
import { Button } from '@/zon/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Input } from '@/zon/ui/input';

/** The name a book gets in its new series: the same, but a "Vol 00" (a prequel apart) becomes that series' "Vol 01". */
function suggested(name: string): string {
  return name.replace(/^Vol 0+(?=\.)/i, 'Vol 01');
}

/** Books of one series that are another's (Jujutsu Kaisen's "Vol 00" is Jujutsu Kaisen 0): pick that series, then their names there. */
export function MoveMangaFiles({
  from,
  files,
  onClose,
  onMoved,
}: {
  from: { id: number; name: string };
  /** The books to move; null closes. */
  files: string[] | null;
  onClose: () => void;
  onMoved: () => void;
}) {
  const [target, setTarget] = useState<MangaEntry | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [moving, setMoving] = useState(false);

  useEffect(() => {
    setTarget(null);
    setNames(Object.fromEntries((files ?? []).map((name) => [name, suggested(name)])));
  }, [files]);

  async function move() {
    if (!files || !target) return;
    setMoving(true);
    try {
      const result = await api.moveMangaFiles(from.id, files, target.anilist_id, names);
      toast.success(`${result.moved} file${result.moved === 1 ? '' : 's'} moved to ${result.folder}`);
      onMoved();
      onClose();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setMoving(false);
    }
  }

  return (
    <>
      <MangaPickDialog
        query={files && !target ? from.name : null}
        title={files ? `Which series ${files.length === 1 ? 'is ' + files[0] : 'are these ' + files.length + ' files'}?` : ''}
        description='They move into its folder, made if it has none; their names can be changed next.'
        action='Choose'
        onClose={onClose}
        onPick={async (entry) => setTarget(entry)}
      />
      <Dialog open={Boolean(files && target)} onOpenChange={(open) => { if (!open) onClose(); }}>
        <DialogContent size='lg'>
          <DialogHeader>
            <DialogTitle>Move to {target ? anilistName(target) : ''}</DialogTitle>
            <DialogDescription>Each file's name in its new series. A volume of its own numbering starts again at Vol 01.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ul className='flex flex-col gap-2'>
              {(files ?? []).map((name) => (
                <li key={name} className='flex items-center gap-2'>
                  <span className='w-40 shrink-0 truncate font-mono text-xs text-muted-foreground' title={name}>{name}</span>
                  <ArrowRight className='size-3.5 shrink-0 text-muted-foreground' />
                  <Input value={names[name] ?? name} onChange={(event) => setNames((current) => ({ ...current, [name]: event.target.value }))} aria-label={'New name of ' + name} />
                </li>
              ))}
            </ul>
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setTarget(null)} disabled={moving}>Another series</Button>
            <Button onClick={() => void move()} disabled={moving}>
              <FolderInput data-icon='inline-start' /> {moving ? 'Moving…' : `Move ${files?.length ?? 0} file${files?.length === 1 ? '' : 's'}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
