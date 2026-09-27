import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, Clock, Download, Film, Layers, ListVideo, Sparkles, Users } from 'lucide-react';

import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Meter } from '@/components/ui/meter';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  api,
  pagePaths,
  recall,
  type Dashboard as DashboardData,
  type DashboardDueItem,
  type DashboardLane,
  type DashboardRecent,
} from '@/lib/api';
import { cn, formatBytes, timeAgo } from '@/lib/utils';

/** "in 42 s", "in 3 min", "now" -- for work that is scheduled rather than waiting. */
function timeUntil(ts: number | null | undefined): string {
  if (!ts) return '—';
  const diff = ts - Date.now() / 1000;
  if (diff <= 1) return 'now';
  if (diff < 60) return `in ${Math.round(diff)} s`;
  if (diff < 3600) return `in ${Math.round(diff / 60)} min`;
  return `in ${Math.round(diff / 3600)} h`;
}

/** "for 12 min" -- how long a task has been going. */
function runningFor(ts: number | null | undefined): string | null {
  if (!ts) return null;
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return 'under a minute';
  if (diff < 3600) return `${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ${Math.floor((diff % 3600) / 60)} min`;
  return `${Math.floor(diff / 86400)} d`;
}

function Stat({ icon: Icon, label, value, hint, tone }: { icon: typeof Users; label: string; value: string; hint?: string; tone?: string }) {
  return (
    <Card>
      <CardContent className='flex items-start gap-3 p-4'>
        <Icon className={cn('mt-0.5 size-4 shrink-0 text-muted-foreground', tone)} />
        <div className='min-w-0'>
          <p className='text-[0.7rem] uppercase tracking-wide text-muted-foreground'>{label}</p>
          <p className='mt-1 font-mono text-2xl text-foreground'>{value}</p>
          {hint && <p className='mt-0.5 truncate text-xs text-muted-foreground'>{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function LaneCard({ lane }: { lane: DashboardLane }) {
  const down = lane.state === 'down';
  const capacity = lane.capacity ?? lane.workers.length;
  return (
    <Card className={cn('flex flex-col', down && 'border-destructive/40')}>
      <CardHeader className='flex flex-row items-start justify-between gap-3 pb-3'>
        <div className='min-w-0'>
          <CardTitle className='text-base'>{lane.label}</CardTitle>
          <CardDescription className='mt-1'>{lane.description}</CardDescription>
          {lane.note && <p className='mt-1 text-xs text-warning'>{lane.note}</p>}
        </div>
        {down ? (
          <Badge variant='destructive' className='shrink-0 whitespace-nowrap'>Not reporting</Badge>
        ) : (
          <Badge variant={lane.busy ? 'success' : 'muted'} className='shrink-0 whitespace-nowrap' title={lane.capacity === null ? 'No fixed limit' : undefined}>
            {lane.busy} / {lane.capacity === null ? (capacity || '∞') : capacity} busy
          </Badge>
        )}
      </CardHeader>
      {/* Scrolls: qBittorrent alone can run twenty. */}
      <CardContent className='flex max-h-80 flex-col gap-2 overflow-y-auto pt-0'>
        {lane.workers.length === 0 && <p className='text-sm text-muted-foreground'>{down ? 'qBittorrent could not be reached.' : 'Nothing running.'}</p>}
        {lane.workers.map((worker) => {
          const task = worker.task;
          const body = (
            <div
              className={cn(
                'flex flex-col gap-1.5 rounded-md border px-3 py-2 transition-colors',
                worker.busy ? 'border-border bg-secondary/25 hover:bg-secondary/45' : 'border-border/50 bg-transparent',
              )}
            >
              <div className='flex items-center gap-2 text-sm'>
                <span className={cn('size-1.5 shrink-0 rounded-full', worker.extra ? 'bg-warning' : worker.busy ? 'bg-success' : 'bg-muted-foreground/40')} aria-hidden='true' />
                <span className='shrink-0 text-xs text-muted-foreground'>{worker.name}</span>
                <span className={cn('min-w-0 flex-1 truncate', worker.busy ? 'text-foreground' : 'text-muted-foreground')} title={task?.title}>
                  {task?.title ?? 'Idle'}
                </span>
                {task?.started_at ? <span className='shrink-0 font-mono text-xs text-muted-foreground'>{runningFor(task.started_at)}</span> : null}
              </div>
              {task && (task.detail || task.percent !== null) && (
                <div className='flex items-center gap-3 pl-3.5'>
                  {task.detail && <span className='min-w-0 flex-1 truncate text-xs text-muted-foreground' title={task.detail}>{task.detail}</span>}
                  {task.percent !== null && (
                    <div className='flex w-32 shrink-0 items-center gap-2'>
                      <Meter parts={[{ value: task.percent, className: 'bg-trend' }]} total={100} className='flex-1' title={`${Math.round(task.percent)}%`} />
                      <span className='w-9 text-right font-mono text-xs text-muted-foreground'>{Math.round(task.percent)}%</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
          return task?.href ? (
            <Link key={worker.id} to={task.href} className='block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded-md'>
              {body}
            </Link>
          ) : (
            <div key={worker.id}>{body}</div>
          );
        })}

      </CardContent>
    </Card>
  );
}

function DueWhen({ item }: { item: DashboardDueItem }) {
  if (item.running) return <Badge variant='success'>Running</Badge>;
  if (item.due_at) return <span className='font-mono text-xs text-foreground'>{timeUntil(item.due_at)}</span>;
  if (item.since) return <span className='text-xs text-muted-foreground'>waiting {timeAgo(item.since).replace(' ago', '')}</span>;
  return <span className='text-xs text-muted-foreground'>—</span>;
}

function RecentList({ title, icon: Icon, rows, empty, action }: { title: string; icon: typeof Film; rows: DashboardRecent[]; empty: string; action?: React.ReactNode }) {
  return (
    <Card className='flex flex-col'>
      <CardHeader className='flex flex-row items-center gap-2 pb-3'>
        <Icon className='size-4 text-muted-foreground' />
        <CardTitle className='text-base'>{title}</CardTitle>
        {action && <div className='ml-auto'>{action}</div>}
      </CardHeader>
      <CardContent className='flex max-h-[28rem] flex-col gap-1 overflow-y-auto pt-0'>
        {rows.length === 0 && <p className='text-sm text-muted-foreground'>{empty}</p>}
        {rows.map((row) => (
          <Link
            key={row.kind + ':' + row.key}
            to={row.href}
            className='flex items-center gap-3 rounded-md px-2 py-1.5 transition-colors hover:bg-foreground/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60'
          >
            <AnimePoster url={row.poster_url} title={row.title} className='w-9 shrink-0 rounded' />
            <div className='min-w-0 flex-1'>
              <p className='truncate text-sm text-foreground' title={row.title}>{row.title}</p>
              <p className='truncate text-xs text-muted-foreground'>
                {row.kind === 'movie'
                  ? 'Movie'
                  : row.kind === 'episode'
                  ? row.episode_label
                  : [row.episode_label, row.count > 1 ? `${row.count} new episodes` : row.episode_label ? null : 'New episode']
                      .filter(Boolean)
                      .join(' · ')}
              </p>
            </div>
            <span className='shrink-0 text-xs text-muted-foreground'>{timeAgo(row.added_at)}</span>
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}

const ANIME_VIEW_KEY = 'bankai:dashboard:recent-anime';

export default function Dashboard() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<DashboardData | undefined>(() => recall<DashboardData>(pagePaths.dashboard));
  const [, setTick] = useState(0);
  // Anime arrivals one row per episode, or grouped into their series; remembered.
  const [animeView, setAnimeView] = useState<'episodes' | 'series'>(() => {
    try {
      return localStorage.getItem(ANIME_VIEW_KEY) === 'episodes' ? 'episodes' : 'series';
    } catch {
      return 'series';
    }
  });
  function chooseAnimeView(view: 'episodes' | 'series') {
    setAnimeView(view);
    try {
      localStorage.setItem(ANIME_VIEW_KEY, view);
    } catch {
      /* a remembered view is a convenience */
    }
  }

  useEffect(() => {
    let alive = true;
    let timer = 0;
    // The next ask waits for the last answer, as the sidebar's does.
    async function poll() {
      try {
        const result = await api.dashboard();
        if (alive) setData(result);
      } catch {
        /* keep showing the last answer; the next poll may reach the server */
      }
      if (alive) timer = window.setTimeout(() => void poll(), 3_000);
    }
    void poll();
    // "in 42 s" and "for 3 min" move on between answers too.
    const clock = window.setInterval(() => setTick((n) => n + 1), 1_000);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      window.clearInterval(clock);
    };
  }, []);

  const summary = data?.summary;
  const dueRows = (data?.due ?? []).flatMap((group) => [
    ...group.items.map((item, index) => ({ group, item, index })),
    ...(group.more > 0 ? [{ group, item: { title: `${group.more} more`, href: group.items[0]?.href ?? null } as DashboardDueItem, index: -1 }] : []),
  ]);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div>
          <h1 className='font-serif text-3xl font-semibold'>Dashboard</h1>
          <p className='text-sm text-muted-foreground'>What every worker is doing, what comes next, and what arrived lately.</p>
        </div>
        {data && <p className='text-xs text-muted-foreground'>Updated {timeAgo(data.generated_at)}</p>}
      </div>

      {!data ? (
        <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
          {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className='h-24' />)}
        </div>
      ) : (
        <>
          <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
            <Stat icon={Users} label='Workers busy' value={`${summary!.workers_busy} / ${summary!.workers_total}`} hint='Across every lane below' />
            <Stat icon={Clock} label='Due' value={String(summary!.due_total)} hint='Queued jobs and releases waiting' />
            <Stat
              icon={Download}
              label='Downloading'
              value={String(summary!.downloading)}
              hint={summary!.download_speed > 0 ? `${formatBytes(summary!.download_speed)}/s in total` : 'Nothing moving right now'}
            />
            <Stat
              icon={Activity}
              label='Anime automation'
              value={summary!.automation === 'running' ? 'Running' : summary!.automation === 'down' ? 'Down' : 'Idle'}
              tone={summary!.automation === 'down' ? 'text-destructive' : summary!.automation === 'running' ? 'text-success' : undefined}
              hint={summary!.automation === 'down' ? 'The worker is not reporting' : undefined}
            />
          </div>

          <section className='flex flex-col gap-3'>
            <h2 className='font-serif text-xl font-semibold'>Workers</h2>
            <div className='grid gap-3 lg:grid-cols-2 2xl:grid-cols-3'>
              {data.lanes.map((lane) => <LaneCard key={lane.key} lane={lane} />)}
            </div>
          </section>

          <section className='flex flex-col gap-3'>
            <h2 className='font-serif text-xl font-semibold'>Up next</h2>
            <div className='table-bleed max-h-[28rem] overflow-auto'>
              <table className='w-full min-w-[720px] border-collapse text-sm'>
                <thead className='sticky top-0 z-10 bg-card'>
                  <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                    <th className='px-3 py-2.5 font-medium'>Lane</th>
                    <th className='px-3 py-2.5 font-medium'>Task</th>
                    <th className='px-3 py-2.5 font-medium'>Detail</th>
                    <th className='px-3 py-2.5 text-right font-medium'>When</th>
                  </tr>
                </thead>
                <tbody>
                  {dueRows.length === 0 && (
                    <tr><td colSpan={4} className='px-3 py-6 text-center text-muted-foreground'>Nothing due.</td></tr>
                  )}
                  {dueRows.map(({ group, item, index }) => (
                    <tr key={group.lane + ':' + index + ':' + item.title} className='border-b border-border/60 last:border-b-0'>
                      <td className='whitespace-nowrap px-3 py-2 text-xs text-muted-foreground'>{index <= 0 ? group.label : ''}</td>
                      <td className='max-w-[28rem] px-3 py-2'>
                        {item.href ? (
                          <Link to={item.href} className={cn('block truncate hover:underline', index === -1 ? 'text-muted-foreground' : 'text-foreground')} title={item.title}>
                            {item.position ? <span className='mr-2 font-mono text-xs text-muted-foreground'>#{item.position}</span> : null}
                            {item.title}
                          </Link>
                        ) : (
                          <span className='block truncate text-foreground' title={item.title}>{item.title}</span>
                        )}
                      </td>
                      <td className='max-w-[20rem] truncate px-3 py-2 text-xs text-muted-foreground' title={item.detail ?? undefined}>{item.detail ?? ''}</td>
                      <td className='whitespace-nowrap px-3 py-2 text-right'>{index === -1 ? null : <DueWhen item={item} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className='flex flex-col gap-3'>
            <h2 className='font-serif text-xl font-semibold'>Recently added</h2>
            <div className='grid gap-3 lg:grid-cols-2'>
              <RecentList title='Movies & Shows' icon={Film} rows={data.recent.mas} empty='Nothing new in the Movies & Shows library.' />
              <RecentList
                title='Anime'
                icon={Sparkles}
                rows={animeView === 'episodes' ? data.recent.anime_episodes ?? [] : data.recent.anime}
                empty='Nothing new in the anime library.'
                action={
                  <ToggleGroup label='Recently added anime as'>
                    <ToggleGroupItem icon={ListVideo} label='Episodes' selected={animeView === 'episodes'} onClick={() => chooseAnimeView('episodes')} />
                    <ToggleGroupItem icon={Layers} label='Series' selected={animeView === 'series'} onClick={() => chooseAnimeView('series')} />
                  </ToggleGroup>
                }
              />
            </div>
          </section>
        </>
      )}
    </div>
  );
}
