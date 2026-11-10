import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type LinkedManga as Linked } from '@/lib/api';
import { formatLabel } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@zonsh/ui';

function state(item: Linked): { label: string; tone: string } {
  if (item.blacklisted) return { label: 'Blacklisted', tone: 'text-destructive' };
  if (item.on_disk) return { label: item.owned ? 'You have ' + item.owned : 'In your library', tone: 'text-success' };
  if (item.in_review) return { label: 'Releases to review', tone: 'text-warning' };
  if (item.followed) return { label: 'Followed', tone: 'text-info' };
  return { label: 'Not followed', tone: 'text-muted-foreground' };
}

/** The manga and light novels AniList links an anime's entries to; each opens in the manga library. */
export function LinkedManga({ ids }: { ids: number[] }) {
  const [items, setItems] = useState<Linked[]>([]);
  const navigate = useNavigate();
  const key = ids.join(',');

  useEffect(() => {
    let alive = true;
    setItems([]);
    if (key) {
      api.mangaLinked(ids)
        .then((answer) => { if (alive) setItems(answer.items); })
        .catch(() => { /* no manga row then */ });
    }
    return () => { alive = false; };
  }, [key]);

  if (items.length === 0) return null;
  return (
    <div className='flex flex-col gap-1.5'>
      <span className='text-xs text-muted-foreground'>Manga and light novels</span>
      <div className='flex flex-wrap gap-2'>
        {items.map((item) => {
          const shown = state(item);
          return (
            <button
              key={item.anilist_id}
              type='button'
              onClick={() => navigate('/m/library?series=' + item.anilist_id)}
              className='flex items-center gap-2 rounded-control border border-border/70 bg-control-surface px-2 py-1 text-left transition-colors hover:bg-control-hover'
              title={'Open ' + item.title + ' in the manga library'}
            >
              <AnimePoster url={item.cover} title={item.title} className='w-7 shrink-0 rounded' />
              <span className='flex min-w-0 flex-col'>
                <span className='max-w-56 truncate text-xs font-medium'>{item.title}</span>
                <span className='font-mono text-[0.62rem] text-muted-foreground'>
                  {formatLabel(item.format)} · <span className={cn(shown.tone)}>{shown.label}</span>
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
