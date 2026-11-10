import { ExternalLink } from 'lucide-react';
import type { HevcUpgradePlan } from '@/lib/api';
import { Checkbox } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';

function formatSize(bytes: number) {
  if (!bytes) return '—';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(1) + ' GB' : (bytes / 1024 ** 2).toFixed(0) + ' MB';
}

function span(first: number, last: number) {
  return first === last ? 'episode ' + first : 'episodes ' + first + '–' + last;
}

/** The approved form an upgrade request carries: each ticked release with the paths shown for it. */
export function approvedOf(plans: HevcUpgradePlan[], picked: Set<string>): Record<string, string[]> {
  return Object.fromEntries(plans.filter((plan) => picked.has(plan.info_hash)).map((plan) => [plan.info_hash, plan.replaces.map((row) => row.path)]));
}

/**
 * Each HEVC release an upgrade would download: what bankai takes it for and every file it would
 * replace, so a release taken for the wrong entry is seen before anything is downloaded (Re:Zero's
 * second season replaced its first, Nov 2026). Only the ticked releases are queued.
 */
export function HevcUpgradePlans({
  plans,
  picked,
  onPickedChange,
}: {
  plans: HevcUpgradePlan[];
  picked: Set<string>;
  onPickedChange: (next: Set<string>) => void;
}) {
  return (
    <div className='flex flex-col gap-4'>
      {plans.map((plan) => (
        <section key={plan.info_hash} className='flex flex-col gap-2 rounded-control border border-border/60 p-3'>
          <label className='flex items-start gap-2.5'>
            <Checkbox
              className='mt-0.5'
              aria-label={'Upgrade with ' + plan.title}
              checked={picked.has(plan.info_hash)}
              onCheckedChange={(on) => {
                const next = new Set(picked);
                if (on) next.add(plan.info_hash);
                else next.delete(plan.info_hash);
                onPickedChange(next);
              }}
            />
            <span className='flex min-w-0 flex-1 flex-col gap-0.5'>
              <span className='text-sm break-words text-foreground'>
                {plan.title}
                {plan.detail_url && (
                  <a href={plan.detail_url} target='_blank' rel='noreferrer' className='ml-1.5 inline-flex align-middle text-muted-foreground hover:text-foreground' aria-label='Open on Nyaa'>
                    <ExternalLink className='size-3.5' />
                  </a>
                )}
              </span>
              <span className='text-xs text-muted-foreground'>
                Taken for <span className='font-medium text-foreground'>{plan.entry}</span>, {span(plan.first, plan.last)} · {formatSize(plan.size_bytes)}
                {plan.keeps.length > 0 && ' · German dub kept: ' + plan.keeps.join(', ')}
              </span>
            </span>
          </label>
          <Table dense>
            <TableHeader>
              <TableRow>
                <TableHead>Replaces</TableHead>
                <TableHead>Folder</TableHead>
                <TableHead numeric>Episode</TableHead>
                <TableHead numeric>Size</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.replaces.map((row) => (
                <TableRow key={row.path}>
                  <TableCell className='max-w-0 text-xs'><span className='block truncate text-foreground' title={row.path}>{row.name}</span></TableCell>
                  <TableCell className='text-xs whitespace-nowrap text-muted-foreground'>{row.folder}</TableCell>
                  <TableCell numeric className='font-mono text-xs'>{row.episode}</TableCell>
                  <TableCell numeric className='text-xs whitespace-nowrap'>{formatSize(row.size)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      ))}
    </div>
  );
}
