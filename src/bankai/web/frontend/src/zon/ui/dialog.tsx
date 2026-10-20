import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { XIcon } from "lucide-react";
import type * as React from "react";
import { cn, sx, triggerSlot } from "@/zon/lib/cn";
import {
  overlayBackdrop,
  overlayCard,
  overlayCloseButton,
  overlayDescription,
  overlayFooter,
  overlayHeader,
  overlayTitle,
} from "@/zon/ui/_overlay";

/**
 * Dialog — a focused task on top of the page: a short form, a decision, a detail view.
 * A dialog is an *object*, so its popup takes the card geometry (20px, the card material).
 *
 * Compose `DialogTrigger` → `DialogContent` → `DialogHeader` / `DialogBody` / `DialogFooter`.
 * The rule people break: using a dialog for content that deserves a page. Keep it short; for a
 * confirmation use `AlertDialog`, for a panel with gestures use `Drawer`.
 */
export const Dialog = BaseDialog.Root;

/** Opens the dialog. Compose with a button: `render={<Button variant="secondary" />}`. */
export function DialogTrigger(props: BaseDialog.Trigger.Props) {
  return <BaseDialog.Trigger {...triggerSlot("dialog-trigger", props.render)} {...props} />;
}

const dialogSizes = {
  sm: "max-w-[26rem]",
  default: "max-w-[32rem]",
  lg: "max-w-[44rem]",
  xl: "max-w-[56rem]",
} as const;

export type DialogSize = keyof typeof dialogSizes;

export interface DialogContentProps extends BaseDialog.Popup.Props {
  /** Max width: `sm` 416px · `default` 512px · `lg` 704px · `xl` 896px. Always full width minus 16px gutters on mobile. */
  size?: DialogSize;
  /** Render the circular close button in the top-right corner. */
  showCloseButton?: boolean;
  /** Accessible name of the close button. */
  closeLabel?: string;
  /** Props for the portal (e.g. `container`, `keepMounted`). */
  portal?: Omit<BaseDialog.Portal.Props, "children">;
}

/**
 * The dialog itself: Portal + Backdrop + Viewport + Popup. Enters and exits with opacity and a
 * 0.97 scale; the viewport keeps a 16px gutter on every side so it never touches the screen edge.
 */
export function DialogContent({
  size = "default",
  showCloseButton = true,
  closeLabel = "Close",
  portal,
  className,
  children,
  ...props
}: DialogContentProps) {
  return (
    <BaseDialog.Portal {...portal}>
      <BaseDialog.Backdrop data-slot="dialog-backdrop" className={overlayBackdrop} />
      <BaseDialog.Viewport
        data-slot="dialog-viewport"
        className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
      >
        <BaseDialog.Popup
          data-slot="dialog-content"
          data-size={size}
          className={sx(
            [
              "relative flex max-h-full w-full min-w-0 flex-col gap-6 overflow-hidden rounded-card p-6 font-body outline-none sm:p-8",
              overlayCard,
              "popup-motion",
              dialogSizes[size],
            ],
            className,
          )}
          {...props}
        >
          {children}
          {showCloseButton && (
            <BaseDialog.Close
              data-slot="dialog-close-button"
              aria-label={closeLabel}
              className={overlayCloseButton}
            >
              <XIcon aria-hidden />
            </BaseDialog.Close>
          )}
        </BaseDialog.Popup>
      </BaseDialog.Viewport>
    </BaseDialog.Portal>
  );
}

/** Title + description block. Leaves room on the right for the close button. */
export function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn(overlayHeader, "pr-10", className)} {...props} />;
}

/** The dialog's name, in Licht 300 at 26px. Lead with the outcome: "Rename this project". */
export function DialogTitle({ className, ...props }: BaseDialog.Title.Props) {
  return <BaseDialog.Title data-slot="dialog-title" className={sx(overlayTitle, className)} {...props} />;
}

/** One or two specific sentences under the title. */
export function DialogDescription({ className, ...props }: BaseDialog.Description.Props) {
  return (
    <BaseDialog.Description
      data-slot="dialog-description"
      className={sx(overlayDescription, className)}
      {...props}
    />
  );
}

/** The scrollable middle. Header and footer stay put while long content scrolls here. */
export function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-body"
      className={cn(
        "-mx-6 -my-0.5 min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 py-0.5 text-[15px] leading-relaxed text-foreground-soft sm:-mx-8 sm:px-8",
        className,
      )}
      {...props}
    />
  );
}

/** Actions, right-aligned on desktop, stacked full-width on mobile (primary on top). */
export function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-footer" className={cn(overlayFooter, className)} {...props} />;
}

/** Closes the dialog. Compose with a button: `render={<Button variant="secondary" />}`. */
export function DialogClose(props: BaseDialog.Close.Props) {
  return <BaseDialog.Close data-slot="dialog-close" {...props} />;
}
