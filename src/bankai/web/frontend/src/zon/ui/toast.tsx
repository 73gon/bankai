import {
  Toast as BaseToast,
  type ToastManager,
  type ToastManagerAddOptions,
  type ToastManagerPromiseOptions,
  type ToastManagerUpdateOptions,
  type ToastObject,
} from "@base-ui/react/toast";
import { CircleAlertIcon, CircleCheckIcon, LoaderCircleIcon, XIcon } from "lucide-react";
import * as React from "react";
import { cn } from "@/zon/lib/cn";

export type ToastType = "default" | "success" | "error" | "loading";

/** A button inside the toast: "Undo", "View invoice". */
export interface ToastActionOptions {
  label: React.ReactNode;
  onClick: () => void;
}

export interface ToastOptions extends Omit<ToastManagerAddOptions<object>, "type" | "title"> {
  type?: ToastType;
  /** Shorthand for `actionProps: { children: label, onClick }`. */
  action?: ToastActionOptions;
}

type ToastManagerLike = Pick<ToastManager<object>, "add" | "close" | "update" | "promise">;

/** The `toast` API: call it for a default toast, or use a typed shortcut. */
export interface ToastApi {
  (title: React.ReactNode, options?: ToastOptions): string;
  /** A green check, a green leading bar and a green-tinted edge. The surface stays the menu material. */
  success: (title: React.ReactNode, options?: ToastOptions) => string;
  /** The same treatment in `--destructive`, announced urgently. */
  error: (title: React.ReactNode, options?: ToastOptions) => string;
  /** Spinner, no auto-dismiss. Update it with `toast.update(id, { type: "success", … })`. */
  loading: (title: React.ReactNode, options?: ToastOptions) => string;
  /** loading → success / error, following a promise. */
  promise: <Value>(
    promise: Promise<Value>,
    options: ToastManagerPromiseOptions<Value, object>,
  ) => Promise<Value>;
  update: (
    id: string,
    options: Omit<ToastManagerUpdateOptions<object>, "type"> & { type?: ToastType },
  ) => void;
  /** Close one toast, or all of them when called without an id. */
  dismiss: (id?: string) => void;
}

function toAddOptions(title: React.ReactNode, { action, ...options }: ToastOptions = {}) {
  const add: ToastManagerAddOptions<object> = { title, ...options };
  if (action) add.actionProps = { ...options.actionProps, children: action.label, onClick: action.onClick };
  return add;
}

function createToastApi(manager: ToastManagerLike): ToastApi {
  const show = (title: React.ReactNode, options?: ToastOptions) => manager.add(toAddOptions(title, options));
  return Object.assign(show, {
    success: (title: React.ReactNode, options?: ToastOptions) => show(title, { ...options, type: "success" }),
    error: (title: React.ReactNode, options?: ToastOptions) =>
      show(title, { priority: "high", ...options, type: "error" }),
    loading: (title: React.ReactNode, options?: ToastOptions) =>
      show(title, { timeout: 0, ...options, type: "loading" }),
    promise: <Value,>(promise: Promise<Value>, options: ToastManagerPromiseOptions<Value, object>) =>
      manager.promise(promise, options),
    update: (id: string, options: ToastManagerUpdateOptions<object>) => manager.update(id, options),
    dismiss: (id?: string) => manager.close(id),
  });
}

/** The library-wide manager. `<Toaster />` renders it unless you pass your own `toastManager`. */
const globalToastManager = BaseToast.createToastManager<object>();

/**
 * toast — queue a toast from anywhere, including outside React (an API client, a store).
 * Requires one `<Toaster />` mounted in the app.
 *
 *   toast.success("Your changes are saved.")
 *   toast.error("We couldn't send your message.", { description: "Check your connection and try again." })
 *   toast("Integration removed", { action: { label: "Undo", onClick: restore } })
 */
export const toast: ToastApi = createToastApi(globalToastManager);

/** The manager of the nearest `<Toaster>`, so `useToast` adds through it rather than a local store. */
const ToasterManagerContext = React.createContext<ToastManager<object> | null>(null);

/**
 * useToast — the same API as `toast`, bound to the nearest `<Toaster>` (render your tree as its
 * children to use it), plus the live `toasts` array.
 */
export function useToast(): ToastApi & { toasts: ToastObject<object>[] } {
  const shared = React.useContext(ToasterManagerContext);
  const { add, close, update, promise, toasts } = BaseToast.useToastManager<object>();
  const api = React.useMemo(
    () => createToastApi(shared ?? { add, close, update, promise }),
    [shared, add, close, update, promise],
  );
  return Object.assign(api, { toasts });
}

/*
 * Several <Toaster>s can share one manager (two demos on a page, a layout and a nested route).
 * Only the first one mounted renders the stack, so a toast never appears twice.
 */
const toasterRegistry = new WeakMap<object, symbol[]>();
const registryListeners = new Set<() => void>();
const notifyRegistry = () => registryListeners.forEach((listener) => listener());
const subscribeRegistry = (listener: () => void) => {
  registryListeners.add(listener);
  return () => {
    registryListeners.delete(listener);
  };
};

function useIsPrimaryToaster(manager: object) {
  const [id] = React.useState(() => Symbol("toaster"));
  React.useEffect(() => {
    const list = toasterRegistry.get(manager) ?? [];
    toasterRegistry.set(manager, [...list, id]);
    notifyRegistry();
    return () => {
      toasterRegistry.set(
        manager,
        (toasterRegistry.get(manager) ?? []).filter((entry) => entry !== id),
      );
      notifyRegistry();
    };
  }, [manager, id]);
  return React.useSyncExternalStore(
    subscribeRegistry,
    () => toasterRegistry.get(manager)?.[0] === id,
    () => false,
  );
}

/* ── Rendering ─────────────────────────────────────────────────────────────── */

const icons: Record<ToastType, React.ReactNode> = {
  default: null,
  /* The one place status colour shows: a green check for success, red for errors. */
  success: <CircleCheckIcon aria-hidden className="size-4 text-success" />,
  error: <CircleAlertIcon aria-hidden className="size-4 text-destructive" />,
  loading: <LoaderCircleIcon aria-hidden className="size-4 text-muted-foreground motion-safe:animate-spin" />,
};

function ToastItem({ toast: item, closeLabel }: { toast: ToastObject<object>; closeLabel: string }) {
  const type = (item.type ?? "default") as ToastType;
  const icon = icons[type] ?? null;
  return (
    <BaseToast.Root
      toast={item}
      data-slot="toast"
      data-type={type}
      className="rounded-button material-menu font-body outline-none"
    >
      <BaseToast.Content
        data-slot="toast-content"
        className="flex items-start gap-3 overflow-hidden p-4 pr-11"
      >
        {icon && <span className="flex h-5 shrink-0 items-center">{icon}</span>}
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <BaseToast.Title
            data-slot="toast-title"
            className="font-body text-sm leading-5 font-medium tracking-normal text-foreground"
          />
          <BaseToast.Description
            data-slot="toast-description"
            className="text-[13px] leading-normal text-muted-foreground first:text-sm first:leading-5 first:text-foreground"
          />
          <BaseToast.Action
            data-slot="toast-action"
            className="mt-2.5 inline-flex min-h-(--control-h-sm) cursor-pointer items-center self-start rounded-button material-raised px-3 text-[13px] font-medium focus-ring"
          />
        </div>
        <BaseToast.Close
          data-slot="toast-close"
          aria-label={closeLabel}
          className="absolute top-3 right-3 inline-flex size-6 cursor-pointer items-center justify-center rounded-full material-row text-muted-foreground focus-ring"
        >
          <XIcon aria-hidden className="size-3.5" />
        </BaseToast.Close>
      </BaseToast.Content>
    </BaseToast.Root>
  );
}

function ToastList({ closeLabel }: { closeLabel: string }) {
  const { toasts } = BaseToast.useToastManager<object>();
  return toasts.map((item) => <ToastItem key={item.id} toast={item} closeLabel={closeLabel} />);
}

export interface ToasterProps extends Omit<BaseToast.Provider.Props, "toastManager"> {
  /** Defaults to the library-wide manager behind `toast()`. */
  toastManager?: ToastManager<object>;
  /** Classes for the viewport (the fixed stack in the bottom-right corner). */
  className?: string;
  /** Accessible name of each toast's close button. */
  closeLabel?: string;
}

/**
 * Toaster — Toast.Provider + Viewport: toasts stack in the bottom-right corner (bottom, full
 * width on mobile), expand on hover or focus, and swipe away down or right. F6 jumps into the stack.
 *
 * Mount it once near the root (extra Toasters on the same manager render nothing). Render the app
 * as its children if you want `useToast()`.
 * The rule people break: toasting what needs a decision. A toast disappears; anything the person
 * must act on belongs in the page or an `AlertDialog`.
 */
export function Toaster({
  toastManager = globalToastManager,
  limit = 3,
  className,
  closeLabel = "Dismiss",
  children,
  ...props
}: ToasterProps) {
  const primary = useIsPrimaryToaster(toastManager);
  return (
    <BaseToast.Provider toastManager={toastManager} limit={limit} {...props}>
      <ToasterManagerContext.Provider value={toastManager}>{children}</ToasterManagerContext.Provider>
      {primary && (
        <BaseToast.Portal>
          <BaseToast.Viewport
            data-slot="toast-viewport"
            className={cn(
              "fixed right-4 bottom-4 z-[60] mx-auto w-[calc(100vw-2rem)] outline-none sm:right-6 sm:bottom-6 sm:w-90",
              className,
            )}
          >
            <ToastList closeLabel={closeLabel} />
          </BaseToast.Viewport>
        </BaseToast.Portal>
      )}
    </BaseToast.Provider>
  );
}
