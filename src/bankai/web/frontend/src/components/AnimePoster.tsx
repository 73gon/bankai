import { useState } from 'react';
import { Film } from 'lucide-react';
import { api } from '@/lib/api';
import { POSTER_IMG, posterSources } from '@/lib/posters';
import { cn } from '@/lib/utils';

export function AnimePoster({ url, title, className }: { url?: string | null; title: string; className?: string }) {
  // How far this address got: its sized versions failed (a size AniList does not have for it),
  // then the address as given. Kept by address, so a new one is tried afresh -- without an effect
  // per poster, which ran again for every cover each time a kept page was shown.
  const [failed, setFailed] = useState<{ url: string; plain: boolean } | null>(null);
  const sizedFailed = failed?.url === url;
  const gone = sizedFailed && Boolean(failed?.plain);
  return (
    <div className={cn('aspect-[2/3] overflow-hidden rounded-md bg-muted', className)}>
      {url && !gone ? (
        // Its size picked by the browser from how big it is drawn (lib/posters), decoded off the main path.
        <img
          key={sizedFailed ? 'plain' : 'sized'}
          {...(sizedFailed ? { src: api.posterUrl(url) } : posterSources(url))}
          {...POSTER_IMG}
          alt={title + ' cover'}
          className='h-full w-full object-cover'
          onError={() => setFailed({ url, plain: sizedFailed })}
        />
      ) : (
        <div className='flex h-full items-center justify-center text-muted-foreground'><Film aria-hidden='true' className='size-8' /></div>
      )}
    </div>
  );
}