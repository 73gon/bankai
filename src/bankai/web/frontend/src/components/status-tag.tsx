import type * as React from 'react';
import { cn } from '@/zon/lib/cn';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/zon/ui/tooltip';

/* zon's Tag geometry (22px, Licht Mono 11px, 7px radius) with the status
   tints bankai keeps: a job's phase, an encode, a transfer. Counts use zon's
   Badge; plain metadata uses zon's Tag. */
const tints = {
  default: 'border-field-edge bg-control-surface text-foreground',
  secondary: 'border-field-edge bg-control-surface text-foreground-soft',
  muted: 'border-transparent bg-control-surface/60 text-muted-foreground',
  outline: 'border-field-edge bg-transparent text-foreground-soft',
  queue: 'border-field-edge bg-control-surface text-foreground',
  review: 'border-foreground/40 bg-foreground/10 text-foreground',
  success: 'border-success/30 bg-success/15 text-success',
  info: 'border-info/30 bg-info/15 text-info',
  warning: 'border-warning/30 bg-warning/15 text-warning',
  destructive: 'border-destructive/30 bg-destructive/15 text-destructive',
  transfer: 'border-transfer/30 bg-transfer/15 text-transfer',
  starting: 'border-starting/40 bg-starting/20 text-starting',
  extract: 'border-extract/40 bg-extract/20 text-extract',
  torrent: 'border-torrent/40 bg-torrent/20 text-torrent',
  repack: 'border-repack/30 bg-repack/15 text-repack',
  accent: 'border-orange-500/30 bg-orange-500/15 text-orange-200',
} as const;

export type StatusVariant = keyof typeof tints;

export interface StatusTagProps extends React.ComponentProps<'span'> {
  variant?: StatusVariant | null;
  /** A leading dot in the current ink. */
  dot?: boolean;
}

/** A status label. A `title` becomes a tooltip rather than the native one. */
export function StatusTag({ variant, dot, title, className, children, ...props }: StatusTagProps) {
  const tag = (
    <span
      data-slot='status-tag'
      data-variant={variant ?? 'default'}
      className={cn(
        'inline-flex min-h-[22px] w-fit shrink-0 items-center gap-1.5 rounded-item border px-2 font-mono text-[11px] leading-none tracking-[0.02em] whitespace-nowrap',
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3",
        tints[variant ?? 'default'],
        className,
      )}
      {...props}
    >
      {dot && <span aria-hidden className='size-1.5 shrink-0 rounded-full bg-current' />}
      {children}
    </span>
  );
  if (!title) return tag;
  return (
    <Tooltip>
      <TooltipTrigger render={tag} />
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
}
