import * as React from 'react';
import { cn } from '@/lib/utils';

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, type, ...props }, ref) => (
  <input
    type={type}
    ref={ref}
    className={cn(
      'flex h-9 w-full rounded-md border border-white/10 bg-gradient-to-b from-white/[0.02] to-transparent px-3 py-1 text-sm shadow-[inset_0_1px_2px_oklch(0_0_0/0.35)] transition-colors placeholder:text-muted-foreground/70 hover:border-white/20 focus-visible:border-white/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/15 disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  />
));
Input.displayName = 'Input';
