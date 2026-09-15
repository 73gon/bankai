import { useEffect, useState } from 'react';
import { CircleStop, Play, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api, type Job } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';

function formatTime(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}

function statusVariant(status: string) {
  if (status === 'done') return 'success' as const;
  if (status === 'failed' || status === 'cancelled') return 'destructive' as const;
  if (status === 'stopped' || status === 'queued') return 'warning' as const;
  return 'info' as const;
}

export default function AnimeQueue() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      setJobs((await api.animeQueue()).jobs);
    } catch (error: any) {
      if (!silent) toast.error(error.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 2500);
    return () => window.clearInterval(timer);
  }, []);

  async function act(job: Job, action: 'stop' | 'continue' | 'retry' | 'delete') {
    setBusy(job.id);
    try {
      if (action === 'stop') await api.stopJob(job.id);
      if (action === 'continue') await api.continueJob(job.id);
      if (action === 'retry') await api.retryJob(job.id);
      if (action === 'delete') await api.deleteJob(job.id);
      await load(true);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Queue</h1>
          <p className='text-sm text-muted-foreground'>Only Erai-raws and manually selected Anime downloads appear here.</p>
        </div>
        <Button variant='secondary' onClick={() => void load()} disabled={loading}>
          <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Anime downloads</CardTitle>
          <CardDescription>{jobs.length} job{jobs.length === 1 ? '' : 's'} in Anime history</CardDescription>
        </CardHeader>
        <CardContent className='overflow-x-auto'>
          {loading && jobs.length === 0 ? (
            <div className='flex min-h-40 items-center justify-center'><Spinner /></div>
          ) : jobs.length === 0 ? (
            <EmptyState title='Anime queue is empty' description='Automatic and manual Erai downloads will appear here.' />
          ) : (
            <table className='w-full min-w-[820px] border-collapse text-sm'>
              <thead>
                <tr className='border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground'>
                  <th className='px-3 py-3 font-medium'>Title</th>
                  <th className='px-3 py-3 font-medium'>Status</th>
                  <th className='px-3 py-3 font-medium'>Progress</th>
                  <th className='px-3 py-3 font-medium'>Updated</th>
                  <th className='px-3 py-3 text-right font-medium'>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className='border-b border-border/60 align-top last:border-0'>
                    <td className='px-3 py-4'>
                      <p className='font-medium text-foreground'>{job.title}</p>
                      <p className='mt-1 max-w-xl text-xs text-muted-foreground'>{job.reason || job.step_label || job.kind}</p>
                    </td>
                    <td className='px-3 py-4'><Badge variant={statusVariant(job.status)}>{job.status}</Badge></td>
                    <td className='px-3 py-4'>
                      <div className='flex min-w-36 flex-col gap-2'>
                        <span className='font-mono text-xs'>{Math.round(job.overall_percent ?? 0)}%</span>
                        <div className='h-1.5 overflow-hidden rounded-full bg-secondary'>
                          <div className='h-full rounded-full bg-primary transition-[width]' style={{ width: String(Math.max(0, Math.min(100, job.overall_percent ?? 0))) + '%' }} />
                        </div>
                      </div>
                    </td>
                    <td className='px-3 py-4 text-xs text-muted-foreground'>{formatTime(job.updated_at)}</td>
                    <td className='px-3 py-4'>
                      <div className='flex justify-end gap-1'>
                        {job.status === 'running' && (
                          <Button size='icon' variant='ghost' aria-label={'Stop ' + job.title} onClick={() => void act(job, 'stop')} disabled={busy === job.id}><CircleStop /></Button>
                        )}
                        {job.status === 'stopped' && (
                          <Button size='icon' variant='ghost' aria-label={'Continue ' + job.title} onClick={() => void act(job, 'continue')} disabled={busy === job.id}><Play /></Button>
                        )}
                        {job.status === 'failed' && (
                          <Button size='icon' variant='ghost' aria-label={'Retry ' + job.title} onClick={() => void act(job, 'retry')} disabled={busy === job.id}><RotateCcw /></Button>
                        )}
                        <Button size='icon' variant='ghost' aria-label={'Remove ' + job.title} onClick={() => void act(job, 'delete')} disabled={busy === job.id}><Trash2 /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}