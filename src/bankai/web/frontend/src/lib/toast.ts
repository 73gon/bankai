import type * as React from 'react';
import { toast as zonToast, type ToastApi, type ToastOptions } from '@/zon/ui/toast';

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
