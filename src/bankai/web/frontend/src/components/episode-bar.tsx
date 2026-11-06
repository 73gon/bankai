import { cn } from '@/zon/lib/cn';

/** What one letter of a show's strip is: its name, and its colour on the bar. */
export type StripTier = { label: string; className: string };
export type Strip = Array<{ label: string; states: string }>;

// Letters every strip shares; a show's own `tiers` name the rest.
const MISSING: StripTier = { label: 'missing', className: 'bg-control-surface' };
const NOT_AIRED: StripTier = { label: 'not aired yet', className: 'bg-control-surface opacity-40' };
const STAGED: StripTier = { label: 'in staging', className: 'bg-info/45' };
// Season breaks only while there are few enough of them to leave the bar readable.
const MAX_BREAKS = 8;

function tierOf(letter: string, tiers: Record<string, StripTier>): StripTier {
  return tiers[letter] ?? (letter === 't' ? NOT_AIRED : letter === 's' ? STAGED : letter === 'm' ? MISSING : { label: 'unknown', className: 'bg-muted-foreground/40' });
}

/** The strip without what has not aired yet (TBA): the bar and its count are about aired episodes,
 *  and a season with none aired is not on it at all. */
export function airedStrip(strip: Strip): Strip {
  return strip
    .map((season) => ({ ...season, states: season.states.replace(/t/g, '') }))
    .filter((season) => season.states.length > 0);
}

/** How many aired episodes a strip holds. */
export function airedCount(strip: Strip): number {
  return airedStrip(strip).reduce((sum, season) => sum + season.states.length, 0);
}

/** A season's letters as runs of one letter each: "hhhaa" -> [h ×3 from 1, a ×2 from 4]. */
function runs(states: string): Array<{ letter: string; from: number; length: number }> {
  const out: Array<{ letter: string; from: number; length: number }> = [];
  for (let index = 0; index < states.length; index += 1) {
    const last = out[out.length - 1];
    if (last && last.letter === states[index]) last.length += 1;
    else out.push({ letter: states[index], from: index + 1, length: 1 });
  }
  return out;
}

/** "12 HEVC · 3 AVC · 9 missing" for one season's letters. */
export function describeStrip(states: string, tiers: Record<string, StripTier>): string {
  const counts = new Map<string, number>();
  for (const letter of states) counts.set(letter, (counts.get(letter) ?? 0) + 1);
  return [...counts].map(([letter, count]) => `${count} ${tierOf(letter, tiers).label}`).join(' · ');
}

/**
 * A show's aired episodes end to end, season after season, as one continuous
 * bar: where the library has them it is lit, in the colour of what they are
 * (`tiers`: an encode, a language), and where it has none the bar's ground
 * shows. Each stretch is as wide as its episodes and names them on hover;
 * seasons are parted by a hairline of the ground. What has not aired yet is
 * left out.
 */
export function EpisodeBar({ strip: whole, tiers, className, label }: { strip: Strip; tiers: Record<string, StripTier>; className?: string; label?: string }) {
  const strip = airedStrip(whole);
  const breaks = strip.length > 1 && strip.length <= MAX_BREAKS;
  return (
    <div
      role='img'
      aria-label={label}
      className={cn(
        'flex h-[13px] w-[110px] shrink-0 items-center overflow-hidden rounded-[3px] border border-field-edge bg-field-surface px-[2px] shadow-field',
        breaks && 'gap-[2px]',
        className,
      )}
    >
      {strip.map((season, index) => (
        // Square ends: a season break is a plain cut, not a row of rounded pills.
        <div key={index} className='flex h-[7px] min-w-px' style={{ flexGrow: Math.max(1, season.states.length), flexBasis: 0 }}>
          {runs(season.states).map((run) => {
            const tier = tierOf(run.letter, tiers);
            const episodes = run.length === 1 ? `E${run.from}` : `E${run.from}–${run.from + run.length - 1}`;
            return (
              <span
                key={run.from}
                className={cn('h-full min-w-0', tier.className)}
                style={{ flexGrow: run.length, flexBasis: 0 }}
                title={`${season.label}: ${episodes} ${tier.label}`}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
