import { useRef, useState, type ElementType } from 'react';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/**
 * Text that may be cut off -- `truncate` or a `line-clamp-*` class -- with the
 * whole of it in a tooltip, shown only when it really is cut off.
 */
export function TruncatedText({
  text,
  className,
  as: Tag = 'p',
}: {
  text: string;
  className?: string;
  as?: ElementType;
}) {
  const ref = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);
  const cut = () => {
    const node = ref.current;
    return Boolean(node && (node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1));
  };
  return (
    <Tooltip open={open} onOpenChange={(next) => setOpen(next && cut())}>
      <TooltipTrigger asChild>
        <Tag ref={ref} className={cn('min-w-0', className)}>
          {text}
        </Tag>
      </TooltipTrigger>
      <TooltipContent side='top' align='start' className='max-w-md'>
        {text}
      </TooltipContent>
    </Tooltip>
  );
}
