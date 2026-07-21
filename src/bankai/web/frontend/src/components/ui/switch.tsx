import * as React from 'react';
import * as SwitchPrimitives from '@radix-ui/react-switch';
import { cn } from '@/lib/utils';

export const Switch = React.forwardRef<React.ElementRef<typeof SwitchPrimitives.Root>, React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>>(
  ({ className, ...props }, ref) => (
    <SwitchPrimitives.Root
      ref={ref}
      className={cn(
        'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-white/20 bg-black/50 shadow-inner transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-primary/70 data-[state=checked]:bg-primary data-[state=unchecked]:bg-black/60',
        className,
      )}
      {...props}
    >
      <SwitchPrimitives.Thumb className='pointer-events-none block h-3.5 w-3.5 rounded-full bg-white shadow-[0_1px_4px_rgba(0,0,0,0.8)] ring-1 ring-black/20 transition-transform data-[state=checked]:translate-x-[17px] data-[state=checked]:bg-primary-foreground data-[state=unchecked]:translate-x-0.5' />
    </SwitchPrimitives.Root>
  ),
);
Switch.displayName = 'Switch';
