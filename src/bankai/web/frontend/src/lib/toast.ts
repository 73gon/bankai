import type * as React from 'react';
import { toast as zonToast, type ToastApi, type ToastOptions } from '@zonsh/ui';

/**
 * zon's toast, plus the two kinds bankai also raises: a warning (something
 * half-done the person should know about) and an info note. zon keeps toasts
 * monochrome apart from success and error, so both show as its default toast.
 */
export const toast: ToastApi & {
  warning: (title: React.ReactNode, options?: ToastOptions) => string;
  info: (title: React.ReactNode, options?: ToastOptions) => string;
} = Object.assign((title: React.ReactNode, options?: ToastOptions) => zonToast(title, options), zonToast, {
  warning: (title: React.ReactNode, options?: ToastOptions) => zonToast(title, options),
  info: (title: React.ReactNode, options?: ToastOptions) => zonToast(title, options),
});

/**
 * Work a dialog started, followed outside it: the dialog closes at once, a toast with a spinner
 * stands for the work and turns into its outcome -- the answer it gives or the error. Waiting in a
 * dialog on a busy button ("Saving…", "Queuing…") kept the page from being used meanwhile.
 * Returns the work's own promise, for what follows it (a reload); a failure is said in the toast and
 * also rejects it, so callers that go on only after success can `.then`.
 */
export function inBackground<T>(
  work: Promise<T>,
  messages: { loading: string; success: string | ((result: T) => string); error?: string | ((error: Error) => string) },
): Promise<T> {
  return zonToast.promise(work, {
    loading: messages.loading,
    success: messages.success,
    error: (error: Error) => {
      const said = typeof messages.error === 'function' ? messages.error(error) : messages.error;
      return said ? `${said}: ${error?.message ?? error}` : String(error?.message ?? error);
    },
  });
}
