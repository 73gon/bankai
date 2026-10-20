import { useMemo, useState } from 'react';
import { ListVideo, Loader2, X, RotateCcw, CheckCircle2, AlertCircle, Clock, Wifi, WifiOff } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type Job } from '@/lib/api';
import { useJobStream } from '@/hooks/useJobStream';
import { Button } from '@/zon/ui/button';
import { StatusTag } from '@/components/status-tag';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { timeAgo } from '@/lib/utils';
import { cn } from '@/zon/lib/cn';

function statusBadge(job: Job) {
  if (job.pending) return <StatusTag variant='muted'>Pending</StatusTag>;
  switch (job.status) {
    case 'running':
      return job.kind === 'transfer' ? <StatusTag variant='transfer'>Transferring</StatusTag> : <StatusTag variant='info'>Running</StatusTag>;
    case 'done':
    case 'success':
      return <StatusTag variant='success'>Done</StatusTag>;
    case 'error':
    case 'failed':
      return <StatusTag variant='destructive'>Failed</StatusTag>;
    case 'cancelled':
      return <StatusTag variant='warning'>Cancelled</StatusTag>;
    default:
      return <StatusTag variant='muted'>{job.status}</StatusTag>;
  }
}

function StatusIcon({ job }: { job: Job }) {
  if (job.pending) return <Clock className='h-4 w-4 text-muted-foreground' />;
  if (job.status === 'running')
    return <Loader2 className={`h-4 w-4 animate-spin ${job.kind === 'transfer' ? 'text-transfer' : 'text-info'}`} />;
  if (['done', 'success'].includes(job.status)) return <CheckCircle2 className='h-4 w-4 text-success' />;
  if (['error', 'failed'].includes(job.status)) return <AlertCircle className='h-4 w-4 text-destructive' />;
  return <ListVideo className='h-4 w-4 text-muted-foreground' />;
}

function jobTint(job: Job): string {
  if (job.pending) return 'bg-muted/25 hover:bg-muted/35';
  if (job.status === 'running') {
    return job.kind === 'transfer' ? 'bg-transfer/[0.14] hover:bg-transfer/[0.2]' : 'bg-info/[0.13] hover:bg-info/[0.19]';
  }
  if (['done', 'success'].includes(job.status)) return 'bg-success/[0.14] hover:bg-success/[0.2]';
  if (['error', 'failed'].includes(job.status)) return 'bg-destructive/[0.15] hover:bg-destructive/[0.21]';
  if (job.status === 'cancelled') return 'bg-warning/[0.13] hover:bg-warning/[0.19]';
  return 'bg-muted/20 hover:bg-muted/30';
}

export default function Queue() {
  const [follow, setFollow] = useState<string | null>(null);
  const { jobs, logTail, connected, live } = useJobStream(follow);

  const sorted = useMemo(() => [...jobs].sort((a, b) => (b.started_at || 0) - (a.started_at || 0)), [jobs]);

  async function cancel(id: string) {
    try {
      await api.cancelJob(id);
      toast.success('Cancelled');
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  async function retry(id: string) {
    try {
      await api.retryJob(id);
      toast.success('Retrying');
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className='space-y-6'>
      <header className='flex items-center justify-between'>
        <div className='flex items-baseline gap-2'>
          <h1 className='font-heading text-3xl font-light'>Queue</h1>
          <span className='text-sm text-muted-foreground'>— Live download and dubbing jobs.</span>
        </div>
        <StatusTag variant={connected ? 'success' : 'muted'} className='gap-1.5'>
          {connected ? <Wifi className='h-3 w-3' /> : <WifiOff className='h-3 w-3' />}
          {live ? 'Live' : connected ? 'Polling' : 'Offline'}
        </StatusTag>
      </header>

      {sorted.length === 0 ? (
        <Empty>
          <EmptyMedia><ListVideo /></EmptyMedia>
          <EmptyTitle>Queue is empty</EmptyTitle>
          <EmptyDescription>Queue a title from Discover or Search.</EmptyDescription>
        </Empty>
      ) : (
        <div className='grid gap-3'>
          {sorted.map((job) => {
            const pct = job.overall_percent ?? 0;
            const active = follow === job.id;
            return (
              <div
                key={job.id}
                className={cn(
                  'rounded-card border border-field-edge p-4 transition-colors duration-(--duration) ease-(--ease)',
                  jobTint(job),
                  active && 'ring-1 ring-primary/40',
                )}
              >
                <div>
                  <div className='flex items-center justify-between gap-3'>
                    <button className='flex min-w-0 flex-1 items-center gap-3 rounded-item text-left focus-ring' onClick={() => setFollow(active ? null : job.id)}>
                      <StatusIcon job={job} />
                      <div className='min-w-0'>
                        <div className='truncate font-medium'>{job.title}</div>
                        <div className='flex items-center gap-2 text-xs text-muted-foreground'>
                          <span className='uppercase tracking-wide'>{job.kind}</span>
                          {job.step_label && <span className='truncate'>· {job.step_label}</span>}
                          {job.started_at > 0 && <span>· {timeAgo(job.started_at)}</span>}
                        </div>
                      </div>
                    </button>
                    <div className='flex items-center gap-2'>
                      {statusBadge(job)}
                      {job.status === 'running' && !job.pending && (
                        <Button size='icon-sm' variant='ghost' onClick={() => cancel(job.id)} title='Cancel' aria-label='Cancel'>
                          <X className='h-4 w-4' />
                        </Button>
                      )}
                      {['error', 'failed', 'cancelled'].includes(job.status) && (
                        <Button size='icon-sm' variant='ghost' onClick={() => retry(job.id)} title='Retry' aria-label='Retry'>
                          <RotateCcw className='h-4 w-4' />
                        </Button>
                      )}
                    </div>
                  </div>

                  {(job.status === 'running' || (job.overall_percent ?? 0) > 0) && !job.pending && (
                    <div className='mt-3'>
                      <div className='h-1.5 overflow-hidden rounded-full border border-field-edge bg-field-surface'>
                        <div
                          className={cn(
                            'h-full rounded-full bg-linear-to-r transition-all',
                            job.kind === 'transfer' ? 'from-transfer to-primary' : 'from-info to-primary',
                          )}
                          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
                        />
                      </div>
                      {job.total_steps != null && (
                        <div className='mt-1 font-mono text-[11px] tabular-nums text-muted-foreground'>
                          Step {job.step ?? 0}/{job.total_steps} · {Math.round(pct)}%
                        </div>
                      )}
                    </div>
                  )}

                  {active && (
                    <pre className='ansi-log mt-3 max-h-72 overflow-auto rounded-control border border-field-edge bg-black/50 p-3 font-mono text-[11px] leading-relaxed'>
                      {logTail || 'Waiting for log output…'}
                    </pre>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
