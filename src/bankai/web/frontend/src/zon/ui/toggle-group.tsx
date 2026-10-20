import { Toggle as BaseToggle } from "@base-ui/react/toggle";
import { ToggleGroup as BaseToggleGroup } from "@base-ui/react/toggle-group";
import * as React from "react";
import { cn, sx } from "@/zon/lib/cn";
import { glideMotion, glideStyle, useGlide, useMergedRef } from "@/zon/lib/glide";

export interface ToggleGroupProps<Value extends string = string> extends BaseToggleGroup.Props<Value> {
  /**
   * In single mode (the default) one segment always stays pressed: clicking the current
   * segment again does nothing. Set `allowEmpty` to let it be cleared.
   */
  allowEmpty?: boolean;
  /**
   * Segments with an `icon` and a label show only one of them until pressed: `label` keeps the
   * icons and reveals the pressed segment's label; `icon` keeps the labels and reveals its icon.
   * The segment widens as the hidden part slides in, and the raised surface widens with it.
   */
  reveal?: "label" | "icon";
  ref?: React.Ref<HTMLDivElement>;
}

const ITEM = '[data-slot="toggle-group-item"]';
const findPressed = (root: HTMLElement) => root.querySelector(`${ITEM}[data-pressed]`);

/**
 * ToggleGroup — the segmented control. A recessed 38px track holding 30px segments; the current
 * segment is raised like a secondary button. In single mode one raised indicator glides to the
 * segment you pick; until it has measured (and in `multiple` mode) each pressed segment raises
 * itself.
 *
 * Use it for 2–5 short, mutually exclusive options where seeing them side by side is the point
 * (billing period, view mode, a units switch). Past five options, or with long labels, use
 * `Select`. The rule people break: using it as a row of independent actions — that is a
 * toolbar of `Button`s or `Toggle`s. Give the group an `aria-label` or a visible label.
 */
export function ToggleGroup<Value extends string = string>({
  allowEmpty = false,
  reveal,
  multiple,
  onValueChange,
  className,
  children,
  ref,
  ...props
}: ToggleGroupProps<Value>) {
  const [rootRef, setRef] = useMergedRef(ref);
  const glide = useGlide(rootRef, findPressed, {
    enabled: !multiple,
    attributes: ["data-pressed"],
    items: ITEM,
  });

  return (
    <BaseToggleGroup<Value>
      ref={setRef}
      data-slot="toggle-group"
      data-indicator={glide.visible ? "" : undefined}
      data-reveal={reveal}
      multiple={multiple}
      onValueChange={(value, eventDetails) => {
        if (!multiple && !allowEmpty && value.length === 0) {
          eventDetails.cancel();
          return;
        }
        onValueChange?.(value, eventDetails);
      }}
      className={sx(
        [
          "group/toggle-group relative inline-flex min-h-(--control-h) w-fit material-field items-stretch gap-0.5 rounded-control p-[3px]",
          "data-[orientation=vertical]:flex-col",
          "data-disabled:**:data-[slot=toggle-group-item]:opacity-100",
        ],
        className,
      )}
      {...props}
    >
      {!multiple && (
        <span
          aria-hidden
          data-slot="toggle-group-indicator"
          data-visible={glide.visible ? "" : undefined}
          data-instant={glide.instant ? "" : undefined}
          className={cn(
            glideMotion,
            "rounded-item border border-transparent bg-control-surface bg-linear-to-b from-control-inner-edge to-transparent shadow-raised",
          )}
          style={glideStyle(glide)}
        />
      )}
      {children}
    </BaseToggleGroup>
  );
}

export interface ToggleGroupItemProps<Value extends string = string> extends BaseToggle.Props<Value> {
  /**
   * A 16px lucide icon before the label. With the group's `reveal`, one of the two stays hidden
   * until the segment is pressed; the hidden label still names the segment for assistive tech.
   */
  icon?: React.ReactNode;
}

/**
 * ToggleGroupItem — one segment. Needs a `value`; icon-only segments need an `aria-label`.
 * Keep labels to a word or two so the segments stay comparable.
 */
export function ToggleGroupItem<Value extends string = string>({
  icon,
  className,
  children,
  ...props
}: ToggleGroupItemProps<Value>) {
  return (
    <BaseToggle<Value>
      data-slot="toggle-group-item"
      className={sx(
        [
          "relative inline-flex min-h-(--control-h-sm) flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-item border border-transparent px-3 font-body text-[13px] leading-none font-medium whitespace-nowrap text-control-muted select-none",
          "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
          // Hover only brightens the label; the raised material means "selected".
          "hover:text-foreground",
          "data-pressed:bg-control-surface data-pressed:bg-linear-to-b data-pressed:from-control-inner-edge data-pressed:to-transparent data-pressed:text-foreground data-pressed:shadow-raised",
          // Once the gliding indicator is placed it carries the raised surface instead.
          "group-data-indicator/toggle-group:data-pressed:bg-transparent group-data-indicator/toggle-group:data-pressed:bg-none group-data-indicator/toggle-group:data-pressed:shadow-none",
          "data-disabled:pointer-events-none data-disabled:opacity-50",
          // Revealing segments size to their content; the icon–label gap sits inside the part that slides.
          "group-data-[reveal]/toggle-group:flex-none group-data-[reveal]/toggle-group:gap-0",
        ],
        className,
      )}
      {...props}
    >
      {icon == null ? (
        children
      ) : (
        // Each part sits in its own grid, so the group's `reveal` can collapse it to 0fr
        // (styles in parts/controls.css).
        <>
          <span data-slot="toggle-group-icon">
            <span>{icon}</span>
          </span>
          {children != null && (
            <span data-slot="toggle-group-label">
              <span>{children}</span>
            </span>
          )}
        </>
      )}
    </BaseToggle>
  );
}
