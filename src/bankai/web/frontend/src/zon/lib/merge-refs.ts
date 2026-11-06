import type * as React from "react";

/**
 * One ref callback that sets each of `refs` — a part's own ref and the one a consumer passed.
 * In React 19 `ref` is an ordinary prop: spread after the part's own, it would replace it.
 *
 * It returns a cleanup, so React 19 detaches through it rather than calling it with null: each
 * callback ref's own cleanup runs (or it is called with null, if it returned none), and each
 * object ref is cleared.
 */
export function mergeRefs<T>(...refs: (React.Ref<T> | undefined)[]): React.RefCallback<T> {
  return (node) => {
    const cleanups = refs.map((ref) => {
      if (typeof ref === "function") {
        const cleanup = ref(node);
        return typeof cleanup === "function" ? cleanup : () => ref(null);
      }
      if (ref) {
        (ref as React.RefObject<T | null>).current = node;
        return () => {
          (ref as React.RefObject<T | null>).current = null;
        };
      }
      return undefined;
    });
    return () => cleanups.forEach((cleanup) => cleanup?.());
  };
}
