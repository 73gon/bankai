import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const buttonVariants = cva(
  // Structure only. Every visual property -- fill, border, radius, colour,
  // transition -- is owned by the unlayered button rules in index.css, so the
  // two systems cannot disagree about what a button looks like.
  'inline-flex cursor-pointer select-none items-center justify-center gap-2 whitespace-nowrap text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: '',
        secondary: '',
        outline: '',
        ghost: '',
        destructive: '',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 px-3 text-xs',
        lg: 'h-11 px-6 text-sm',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, title, ...props }, ref) => {
  const Comp = asChild ? Slot : 'button';
  const safeProps = asChild ? props : { type: 'button' as const, ...props };
  const button = (
    <Comp data-slot='button' data-variant={variant ?? 'default'} className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...safeProps} />
  );
  if (!title) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
});
Button.displayName = 'Button';
