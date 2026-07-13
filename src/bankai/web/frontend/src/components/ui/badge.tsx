import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva('inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.7rem] font-medium tracking-wide transition-colors', {
  variants: {
    variant: {
      default: 'border-white/10 bg-white/[0.06] text-foreground',
      secondary: 'border-white/10 bg-white/[0.05] text-secondary-foreground',
      success: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
      info: 'border-sky-500/30 bg-sky-500/15 text-sky-300',
      review: 'border-violet-500/30 bg-violet-500/15 text-violet-300',
      muted: 'border-white/10 bg-white/[0.03] text-muted-foreground',
      warning: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
      destructive: 'border-red-500/30 bg-destructive/15 text-red-300',
      accent: 'border-orange-500/30 bg-orange-500/15 text-orange-200',
    },
  },
  defaultVariants: { variant: 'default' },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
