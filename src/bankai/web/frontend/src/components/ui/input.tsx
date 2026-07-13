import * as React from 'react';
import { cn } from '@/lib/utils';

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, type, ...props }, ref) => (
  <input
    type={type}
    ref={ref}
    className={cn(
      'flex h-9 w-full rounded-md border border-white/[0.14] bg-black/25 px-3 py-1 text-sm shadow-[inset_0_1px_0_oklch(1_0_0/0.04),inset_0_1px_3px_oklch(0_0_0/0.4)] transition-colors placeholder:text-muted-foreground/60 hover:border-white/25 focus-visible:border-white/45 focus-visible:outline-none focus-visible:ring-0 disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  />
));
Input.displayName = 'Input';
