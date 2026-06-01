import { useMemo, useState } from 'react';
import { ListVideo, Loader2, X, RotateCcw, CheckCircle2, AlertCircle, Clock, Wifi, WifiOff } from 'lucide-react';
import { toast } from 'sonner';
import { api, type Job } from '@/lib/api';
import { useJobStream } from '@/hooks/useJobStream';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty';
import { cn, timeAgo } from '@/lib/utils';

function statusBadge(job: Job) {
  if (job.pending) return <Badge variant='muted'>Pending</Badge>;
  switch (job.status) {
    case 'running':
      return <Badge variant='accent'>Running</Badge>;
    case 'done':
    case 'success':
      return <Badge variant='success'>Done</Badge>;
    case 'error':
    case 'failed':
      return <Badge variant='destructive'>Failed</Badge>;
    case 'cancelled':
      return <Badge variant='warning'>Cancelled</Badge>;
    default:
      return <Badge variant='muted'>{job.status}</Badge>;
  }
}

function StatusIcon({ job }: { job: Job }) {
  if (job.pending) return <Clock className='h-4 w-4 text-muted-foreground' />;
  if (job.status === 'running') return <Loader2 className='h-4 w-4 animate-spin text-fuchsia-300' />;
  if (['done', 'success'].includes(job.status)) return <CheckCircle2 className='h-4 w-4 text-emerald-400' />;
  if (['error', 'failed'].includes(job.status)) return <AlertCircle className='h-4 w-4 text-red-400' />;
  return <ListVideo className='h-4 w-4 text-muted-foreground' />;
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
        <div>
          <h1 className='text-2xl font-semibold'>Queue</h1>
          <p className='text-sm text-muted-foreground'>Live download &amp; dubbing jobs.</p>
        </div>
        <Badge variant={connected ? 'success' : 'muted'} className='gap-1.5'>
          {connected ? <Wifi className='h-3 w-3' /> : <WifiOff className='h-3 w-3' />}
          {live ? 'Live' : connected ? 'Polling' : 'Offline'}
        </Badge>
      </header>

      {sorted.length === 0 ? (
        <EmptyState icon={ListVideo} title='Queue is empty' description='Queue a title from Discover or Search.' />
      ) : (
        <div className='grid gap-3'>
          {sorted.map((job) => {
            const pct = job.overall_percent ?? 0;
            const active = follow === job.id;
            return (
              <Card key={job.id} className={cn(active && 'ring-1 ring-primary/40')}>
                <CardContent className='p-4'>
                  <div className='flex items-center justify-between gap-3'>
                    <button className='flex min-w-0 flex-1 items-center gap-3 text-left' onClick={() => setFollow(active ? null : job.id)}>
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
                        <Button size='icon' variant='ghost' onClick={() => cancel(job.id)} title='Cancel'>
                          <X className='h-4 w-4' />
                        </Button>
                      )}
                      {['error', 'failed', 'cancelled'].includes(job.status) && (
                        <Button size='icon' variant='ghost' onClick={() => retry(job.id)} title='Retry'>
                          <RotateCcw className='h-4 w-4' />
                        </Button>
                      )}
                    </div>
                  </div>

                  {(job.status === 'running' || (job.overall_percent ?? 0) > 0) && !job.pending && (
                    <div className='mt-3'>
                      <div className='h-1.5 overflow-hidden rounded-full bg-secondary'>
                        <div
                          className='h-full rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500 transition-all'
                          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
                        />
                      </div>
                      {job.total_steps != null && (
                        <div className='mt-1 text-[11px] text-muted-foreground'>
                          Step {job.step ?? 0}/{job.total_steps} · {Math.round(pct)}%
                        </div>
                      )}
                    </div>
                  )}

                  {active && (
                    <pre className='ansi-log mt-3 max-h-72 overflow-auto rounded-md bg-black/50 p-3 text-[11px] leading-relaxed'>
                      {logTail || 'Waiting for log output…'}
                    </pre>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
