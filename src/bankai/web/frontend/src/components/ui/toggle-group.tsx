import * as React from 'react';
import { cn } from '@/lib/utils';

/** The segmented control: a recessed track holding one raised, current segment.
 *
 *  For two to five short, mutually exclusive options where comparing them is
 *  the point. A Select hides the alternatives behind a click, which is the
 *  wrong trade when seeing them side by side is the value; past five options,
 *  or with long labels, go back to a Select.
 *
 *  Everything visual lives in index.css beside the other control rules, so
 *  the track wears the field treatment and the current segment wears the
 *  secondary-button one without either being restated here.
 */
export function ToggleGroup({
  className,
  label,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { label: string }) {
  return <div data-slot='toggle-group' role='radiogroup' aria-label={label} className={cn(className)} {...props} />;
}

export function ToggleGroupItem({
  className,
  selected,
  label,
  showLabel = true,
  icon: Icon,
  ...props
}: Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
  selected: boolean;
  label: string;
  /** False leaves the icon alone to speak, with the label as its accessible name. */
  showLabel?: boolean;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <button
      type='button'
      data-slot='toggle-group-item'
      data-state={selected ? 'on' : 'off'}
      role='radio'
      aria-checked={selected}
      aria-label={label}
      className={cn(className)}
      {...props}
    >
      {Icon && <Icon className='size-3.5 shrink-0' />}
      {showLabel && <span>{label}</span>}
    </button>
  );
}
