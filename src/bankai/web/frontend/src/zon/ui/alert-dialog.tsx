import { AlertDialog as BaseAlertDialog } from "@base-ui/react/alert-dialog";
import type * as React from "react";
import { cn, sx, triggerSlot } from "@/zon/lib/cn";
import {
  overlayBackdrop,
  overlayCard,
  overlayDescription,
  overlayFooter,
  overlayHeader,
  overlayTitle,
} from "@/zon/ui/_overlay";

/**
 * AlertDialog — a confirmation that interrupts on purpose: "Remove this integration?".
 * Same object as `Dialog`, but it does not close on an outside click and is announced as an
 * alert, so the person has to choose.
 *
 * The rule people break: vague buttons. Name the action ("Remove integration"), not "OK", and
 * keep the cancel action as the safe default. Use `Button variant="destructive"` for the confirm
 * action only when it really destroys something.
 */
export const AlertDialog = BaseAlertDialog.Root;

/** Opens the alert dialog. Compose with a button: `render={<Button variant="destructive" />}`. */
export function AlertDialogTrigger(props: BaseAlertDialog.Trigger.Props) {
  return <BaseAlertDialog.Trigger {...triggerSlot("alert-dialog-trigger", props.render)} {...props} />;
}

const alertDialogSizes = {
  sm: "max-w-[26rem]",
  default: "max-w-[32rem]",
  lg: "max-w-[44rem]",
  xl: "max-w-[56rem]",
} as const;

export type AlertDialogSize = keyof typeof alertDialogSizes;

export interface AlertDialogContentProps extends BaseAlertDialog.Popup.Props {
  /**
   * Max width: `sm` 416px (default) · `default` 512px · `lg` 704px · `xl` 896px. Reach for the
   * wider sizes only when the question needs room (a list of what is affected, a comparison).
   * Always full width minus 16px gutters on mobile.
   */
  size?: AlertDialogSize;
  /** Props for the portal (e.g. `container`). */
  portal?: Omit<BaseAlertDialog.Portal.Props, "children">;
}

/** Portal + Backdrop + Viewport + Popup, in the dialog card material. No close icon: decide. */
export function AlertDialogContent({ size = "sm", portal, className, ...props }: AlertDialogContentProps) {
  return (
    <BaseAlertDialog.Portal {...portal}>
      <BaseAlertDialog.Backdrop data-slot="alert-dialog-backdrop" className={overlayBackdrop} />
      <BaseAlertDialog.Viewport
        data-slot="alert-dialog-viewport"
        className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
      >
        <BaseAlertDialog.Popup
          data-slot="alert-dialog-content"
          data-size={size}
          className={sx(
            [
              "relative flex max-h-full w-full min-w-0 flex-col gap-6 overflow-y-auto rounded-card p-6 font-body outline-none sm:p-8",
              overlayCard,
              "popup-motion",
              alertDialogSizes[size],
            ],
            className,
          )}
          {...props}
        />
      </BaseAlertDialog.Viewport>
    </BaseAlertDialog.Portal>
  );
}

export function AlertDialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-header" className={cn(overlayHeader, className)} {...props} />;
}

/** Phrase it as the question being decided: "Remove this integration?" */
export function AlertDialogTitle({ className, ...props }: BaseAlertDialog.Title.Props) {
  return (
    <BaseAlertDialog.Title
      data-slot="alert-dialog-title"
      className={sx(overlayTitle, className)}
      {...props}
    />
  );
}

/** Say what happens next, concretely: what is lost, what is kept. */
export function AlertDialogDescription({ className, ...props }: BaseAlertDialog.Description.Props) {
  return (
    <BaseAlertDialog.Description
      data-slot="alert-dialog-description"
      className={sx(overlayDescription, className)}
      {...props}
    />
  );
}

export function AlertDialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-footer" className={cn(overlayFooter, className)} {...props} />;
}

/**
 * Closes the alert dialog — the cancel action, or the confirm action after it has run.
 * Compose with a button: `render={<Button variant="secondary" />}`.
 */
export function AlertDialogClose(props: BaseAlertDialog.Close.Props) {
  return <BaseAlertDialog.Close data-slot="alert-dialog-close" {...props} />;
}
