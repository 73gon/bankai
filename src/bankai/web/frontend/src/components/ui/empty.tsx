import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center rounded-lg border border-dashed border-border/60 py-16 text-center', className)}>
      {Icon && <Icon className='mb-4 h-10 w-10 text-muted-foreground/60' />}
      <p className='text-base font-medium'>{title}</p>
      {description && <p className='mt-1 max-w-sm text-sm text-muted-foreground'>{description}</p>}
      {action && <div className='mt-4'>{action}</div>}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('h-4 w-4 animate-spin', className)} />;
}
