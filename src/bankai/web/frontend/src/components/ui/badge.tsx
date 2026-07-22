import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const badgeVariants = cva('inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.7rem] font-medium tracking-wide transition-colors', {
  variants: {
    variant: {
      default: 'border-white/10 bg-white/[0.06] text-foreground',
      secondary: 'border-white/10 bg-white/[0.05] text-secondary-foreground',
      success: 'border-success/30 bg-success/15 text-success',
      info: 'border-info/30 bg-info/15 text-info',
      review: 'border-foreground/45 bg-foreground/15 text-foreground',
      transfer: 'border-transfer/30 bg-transfer/15 text-transfer',
      repack: 'border-repack/30 bg-repack/15 text-repack',
      muted: 'border-white/10 bg-white/[0.03] text-muted-foreground',
      warning: 'border-warning/30 bg-warning/15 text-warning',
      destructive: 'border-destructive/30 bg-destructive/15 text-destructive',
      accent: 'border-orange-500/30 bg-orange-500/15 text-orange-200',
    },
  },
  defaultVariants: { variant: 'default' },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, title, ...props }: BadgeProps) {
  const badge = <div className={cn(badgeVariants({ variant }), className)} {...props} />;
  if (!title) return badge;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
}
