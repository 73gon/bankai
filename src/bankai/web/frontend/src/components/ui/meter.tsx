import { cn } from '@/lib/utils';

/** A segmented meter, after the Kargul Studio CRM's probability column.
 *
 *  Discrete segments rather than one filled bar: the steps read as a measure
 *  at a glance where a continuous bar reads as decoration, and a segment can
 *  carry its own colour, which is what lets one meter show a composition.
 */
const SEGMENTS = 12;

export type MeterPart = { value: number; className: string };

export function Meter({
  parts,
  total,
  className,
  title,
}: {
  parts: MeterPart[];
  total: number;
  className?: string;
  title?: string;
}) {
  const filled: string[] = [];
  if (total > 0) {
    // Largest-remainder allocation, so a part that exists at all keeps a
    // segment instead of rounding away to nothing.
    const exact = parts.map((part) => ({ ...part, exact: (part.value / total) * SEGMENTS }));
    const base = exact.map((part) => ({ ...part, whole: Math.floor(part.exact) }));
    let used = base.reduce((sum, part) => sum + part.whole, 0);
    const order = [...base]
      .map((part, index) => ({ index, rest: part.exact - part.whole, any: part.value > 0 }))
      .filter((row) => row.any)
      .sort((a, b) => b.rest - a.rest);
    for (const row of order) {
      if (used >= SEGMENTS) break;
      if (base[row.index].whole === 0 || row.rest > 0) {
        base[row.index].whole += 1;
        used += 1;
      }
    }
    for (const part of base) {
      for (let i = 0; i < part.whole; i += 1) filled.push(part.className);
    }
  }
  return (
    <div
      className={cn('flex h-[13px] items-center gap-[2px] overflow-hidden rounded-[2px] bg-meter-track px-[2px]', className)}
      title={title}
      role='img'
      aria-label={title}
    >
      {Array.from({ length: SEGMENTS }, (_, index) => (
        <span
          key={index}
          className={cn(
            'h-[9px] min-w-px flex-1 rounded-[1px]',
            filled[index] ?? 'bg-meter-empty',
          )}
        />
      ))}
    </div>
  );
}

/** Threshold colours for a single ratio, ramping as the meter fills. */
export function rampParts(value: number, total: number): MeterPart[] {
  if (total <= 0 || value <= 0) return [];
  const ratio = value / total;
  const className = ratio >= 0.85 ? 'bg-trend' : ratio >= 0.5 ? 'bg-warning' : 'bg-destructive';
  return [{ value, className }];
}
