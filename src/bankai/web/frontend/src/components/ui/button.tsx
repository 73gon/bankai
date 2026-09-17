import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const buttonVariants = cva(
  // Structure only. Every visual property -- fill, border, radius, colour,
  // transition -- is owned by the unlayered button rules in index.css, so the
  // two systems cannot disagree about what a button looks like.
  'inline-flex cursor-pointer select-none items-center justify-center gap-2 whitespace-nowrap text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: '',
        secondary: '',
        outline: '',
        ghost: '',
        destructive: '',
      },
      // The CRM's control metrics: 30px tall with 9px of padding, which is
      // noticeably lighter than the 36px these were.
      size: {
        default: 'h-[30px] px-[11px]',
        sm: 'h-[26px] px-2.5',
        lg: 'h-9 px-4',
        icon: 'size-[30px] px-0',
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
    <Comp data-slot='button' data-variant={variant ?? 'default'} data-size={size ?? 'default'} className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...safeProps} />
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
