import { useEffect, useState } from 'react';
import { Film } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

export function AnimePoster({ url, title, className }: { url?: string | null; title: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  return (
    <div className={cn('aspect-[2/3] overflow-hidden rounded-md bg-muted', className)}>
      {url && !failed ? (
        <img src={api.posterUrl(url)} alt={title + ' cover'} loading='lazy' className='h-full w-full object-cover' onError={() => setFailed(true)} />
      ) : (
        <div className='flex h-full items-center justify-center text-muted-foreground'><Film aria-hidden='true' className='size-8' /></div>
      )}
    </div>
  );
}