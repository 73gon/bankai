import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';

export type SortDir = 'asc' | 'desc';
export type SortState<K extends string> = { key: K; dir: SortDir };

/** A column heading that sorts, for any table that has one.
 *
 *  The arrow only appears on the column actually sorting, and faintly on
 *  hover elsewhere, so a row of headings does not read as a row of controls
 *  competing for attention.
 */
export function SortHeader<K extends string>({
  label,
  column,
  sort,
  onSort,
  align = 'left',
  className,
}: {
  label: string;
  column: K;
  sort: SortState<K> | null;
  onSort: (key: K) => void;
  align?: 'left' | 'right';
  className?: string;
}) {
  const active = sort?.key === column;
  const Icon = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown;
  return (
    <th
      className={cn('px-3 py-2.5 font-medium', className)}
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type='button'
        onClick={() => onSort(column)}
        className={cn(
          'group inline-flex w-full items-center gap-1 whitespace-nowrap uppercase tracking-wide transition-colors hover:text-foreground',
          active && 'text-foreground',
          align === 'right' && 'flex-row-reverse',
        )}
      >
        {label}
        <Icon
          aria-hidden='true'
          className={cn('size-3 shrink-0 transition-opacity', active ? 'opacity-100' : 'opacity-0 group-hover:opacity-50')}
        />
      </button>
    </th>
  );
}

/** Flip the direction when the same column is clicked, else start afresh.
 *
 *  Text reads naturally A-Z and a number is nearly always most interesting at
 *  its largest, so the first click on a column should already be the answer.
 */
export function nextSort<K extends string>(
  current: SortState<K> | null,
  key: K,
  first: Record<K, SortDir>,
): SortState<K> {
  return current?.key === key
    ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: first[key] };
}
