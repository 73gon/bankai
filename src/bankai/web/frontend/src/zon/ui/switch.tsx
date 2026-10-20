import { Switch as BaseSwitch } from "@base-ui/react/switch";
import { sx } from "@/zon/lib/cn";

export interface SwitchProps extends BaseSwitch.Root.Props {}

/**
 * Switch — a setting that takes effect immediately: a recessed track with a raised round thumb.
 * On, the track takes the primary fill. Hover stretches the thumb, a press stretches it further,
 * and it slides across (size and travel live in parts/controls.css).
 *
 * Use a switch when flipping it changes something right away (notifications, a preview mode).
 * The rule people break: using a switch inside a form that still needs "Save" — that is a
 * `Checkbox`. Always pair it with a visible label (`<FieldLabel><Switch />Label</FieldLabel>`).
 */
export function Switch({ className, ...props }: SwitchProps) {
  return (
    <BaseSwitch.Root
      data-slot="switch"
      className={sx(
        [
          "relative inline-flex h-[22px] w-[38px] shrink-0 material-field cursor-pointer items-center rounded-full p-px",
          "data-checked:border-transparent data-checked:bg-primary data-checked:bg-none data-checked:shadow-checked",
          "data-readonly:cursor-default",
        ],
        className,
      )}
      {...props}
    >
      <BaseSwitch.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block rounded-full bg-(--control-thumb) bg-linear-to-b from-control-inner-edge to-transparent shadow-thumb data-checked:bg-(--control-thumb-on)"
      />
    </BaseSwitch.Root>
  );
}
