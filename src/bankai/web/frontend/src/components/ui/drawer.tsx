import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

/** A right-hand detail panel, after the Kargul Studio CRM.
 *
 *  A row opens its detail beside the table rather than on top of it: the list
 *  stays where it was, dimmed and blurred, so the panel reads as a deeper look
 *  at the row you clicked instead of a new place you have travelled to.
 *
 *  The panel takes the page's own background with a single hairline down its
 *  left edge -- no shadow, no radius. Depth comes from the backdrop, which is
 *  the one thing that actually recedes.
 */
export const Drawer = DialogPrimitive.Root;
export const DrawerClose = DialogPrimitive.Close;

export const DrawerContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className='drawer-overlay fixed inset-0 z-50' />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'drawer-panel fixed inset-y-0 right-0 z-50 flex w-full max-w-[560px] flex-col border-l border-line-strong bg-background',
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close
        data-slot='button'
        data-variant='ghost'
        data-size='icon'
        aria-label='Close'
        className='absolute right-3 top-3 flex size-7 items-center justify-center'
      >
        <X className='size-4' />
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DrawerContent.displayName = 'DrawerContent';

export function DrawerHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('shrink-0 border-b border-border px-5 py-4', className)} {...props} />;
}

export function DrawerBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-4', className)} {...props} />;
}

export function DrawerFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('shrink-0 border-t border-border px-5 py-3', className)}
      {...props}
    />
  );
}

export const DrawerTitle = DialogPrimitive.Title;
export const DrawerDescription = DialogPrimitive.Description;

/** Small uppercase section label, as the CRM uses between drawer sections. */
export function DrawerSection({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('py-3', className)}>
      <h3 className='mb-2 text-[0.65rem] font-medium uppercase tracking-[0.14em] text-muted-foreground'>{label}</h3>
      {children}
    </section>
  );
}
