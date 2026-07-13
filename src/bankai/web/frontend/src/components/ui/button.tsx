import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 active:translate-y-px [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground shadow-[0_1px_2px_oklch(0_0_0/0.5),inset_0_1px_0_oklch(1_0_0/0.4)] hover:bg-primary/90 hover:shadow-[0_2px_16px_-4px_oklch(0.985_0_0/0.45),0_1px_2px_oklch(0_0_0/0.5)]',
        secondary:
          'border border-white/10 bg-gradient-to-b from-white/[0.08] to-white/[0.02] text-secondary-foreground shadow-[inset_0_1px_0_oklch(1_0_0/0.07)] hover:border-white/20 hover:from-white/[0.12] hover:to-white/[0.04] active:from-white/[0.05]',
        outline: 'border border-white/10 bg-transparent text-foreground hover:border-white/25 hover:bg-white/[0.04] active:bg-white/[0.02]',
        ghost: 'text-muted-foreground hover:bg-white/[0.05] hover:text-foreground active:bg-white/[0.03]',
        destructive:
          'bg-destructive text-destructive-foreground shadow-[0_1px_2px_oklch(0_0_0/0.5)] hover:bg-destructive/90 hover:shadow-[0_2px_16px_-4px_oklch(0.704_0.191_22.216/0.5)]',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-11 rounded-md px-6 text-sm',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : 'button';
  return (
    <Comp data-slot='button' data-variant={variant ?? 'default'} className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  );
});
Button.displayName = 'Button';
