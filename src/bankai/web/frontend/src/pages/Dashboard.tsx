import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, Clock, Download, Film, Layers, ListVideo, Sparkles, Users } from 'lucide-react';

import { AnimePoster } from '@/components/AnimePoster';
import { DiskUsage } from '@/components/DiskUsage';
import { StatusTag } from '@/components/status-tag';
import { Card, CardContent, CardHeader, CardTitle } from '@/zon/ui/card';
import { Meter } from '@/zon/ui/meter';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';
import {
  api,
  pagePaths,
  recall,
  type Dashboard as DashboardData,
  type DashboardDueItem,
  type DashboardLane,
  type DashboardRecent,
} from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/utils';
import { cn } from '@/zon/lib/cn';

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

// The strip's dividers: one row on a wide screen, two by two on a narrow one.
const STAT_EDGES = ['', 'border-l', 'border-t lg:border-t-0 lg:border-l', 'border-l border-t lg:border-t-0'];

/** One segment of the summary strip: label and number on one line; what it means on hover. */
function Stat({ icon: Icon, label, value, hint, extra, tone, index }: {
  icon: typeof Users; label: string; value: string; hint: string; extra?: string; tone?: string; index: number;
}) {
  return (
    <div className={cn('flex min-w-0 items-center gap-2.5 border-border/60 px-3.5 py-2', STAT_EDGES[index])} title={hint}>
      <Icon className={cn('size-3.5 shrink-0 text-muted-foreground', tone)} aria-hidden='true' />
      <span className='eyebrow truncate text-[10px]'>{label}</span>
      <span className='ml-auto flex shrink-0 items-center gap-2'>
        {extra && <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{extra}</span>}
        <span className={cn('font-mono text-base tabular-nums text-foreground', tone)}>{value}</span>
      </span>
    </div>
  );
}

/** One worker, one line: what it does, how far, how long. */
function WorkerRow({ worker }: { worker: DashboardLane['workers'][number] }) {
  const task = worker.task;
  const body = (
    <div
      className={cn(
        'flex flex-col gap-1 rounded-item border px-2.5 py-1.5 transition-colors',
        worker.busy ? 'border-border bg-secondary/25 hover:bg-secondary/45' : 'border-border/40 bg-transparent',
      )}
    >
      <div className='flex items-center gap-2 text-[13px]'>
        <span className={cn('size-1.5 shrink-0 rounded-full', worker.name === 'Failed' ? 'bg-destructive' : worker.extra && worker.busy ? 'bg-warning' : worker.busy ? 'bg-success' : 'bg-muted-foreground/40')} aria-hidden='true' />
        <span className={cn('min-w-0 shrink truncate', worker.busy ? 'text-foreground' : 'text-muted-foreground')} title={task?.title}>
          {task?.title ?? 'Idle'}
        </span>
        {task?.detail && !task.percent && (
          <span className='min-w-0 flex-1 truncate text-xs text-muted-foreground' title={task.detail}>· {task.detail}</span>
        )}
        <span className='ml-auto shrink-0 font-mono text-[11px] text-muted-foreground' title={worker.name}>
          {task?.started_at ? runningFor(task.started_at) : worker.name}
        </span>
      </div>
      {task && task.percent !== null && (
        <div className='flex items-center gap-2 pl-3.5'>
          {task.detail && <span className='min-w-0 flex-1 truncate text-xs text-muted-foreground' title={task.detail}>{task.detail}</span>}
          <Meter value={task.percent} max={100} aria-label={task.title ?? worker.name} className='w-24 shrink-0' title={`${Math.round(task.percent)}%`} />
          <span className='w-8 text-right font-mono text-[11px] text-muted-foreground'>{Math.round(task.percent)}%</span>
        </div>
      )}
    </div>
  );
  return task?.href ? (
    <Link to={task.href} className='block rounded-item focus-ring'>{body}</Link>
  ) : (
    body
  );
}

// A busy lane shows this many of its rows on the right; the rest are a "+N more".
const LANE_ROWS = 6;

/** What a lane says on its line of the lane list. */
function laneState(lane: DashboardLane): string {
  const capacity = lane.capacity ?? lane.workers.length;
  if (lane.state === 'down') return lane.key === 'downloads' ? 'Unreachable' : 'Not reporting';
  if (lane.busy) return 'Running';
  if (lane.workers.length === 0) return 'Nothing running';
  return capacity > 1 ? `All ${capacity} idle` : 'Idle';
}

/** One line of the lane list: how busy a lane is. Click: all its rows on the right. */
function LaneLine({ lane, open, onToggle }: { lane: DashboardLane; open: boolean; onToggle: () => void }) {
  const down = lane.state === 'down';
  const capacity = lane.capacity ?? lane.workers.length;
  const rows = lane.workers.filter((worker) => worker.busy || worker.task).length;
  return (
    <div className='flex flex-col'>
      <button
        type='button'
        onClick={onToggle}
        disabled={rows === 0}
        aria-pressed={open}
        title={lane.description}
        className={cn(
          'flex w-full items-center gap-2 rounded-item px-2 py-1.5 text-left focus-ring enabled:cursor-pointer enabled:hover:bg-secondary/40',
          open && 'bg-secondary/50',
        )}
      >
        <span className={cn('size-1.5 shrink-0 rounded-full', down ? 'bg-destructive' : lane.busy ? 'bg-success' : 'bg-muted-foreground/40')} aria-hidden='true' />
        <span className={cn('truncate text-sm', lane.busy || down ? 'text-foreground' : 'text-muted-foreground')}>{lane.label}</span>
        <span className={cn('ml-auto shrink-0 text-xs', down ? 'text-destructive' : 'text-muted-foreground')}>{laneState(lane)}</span>
        <span className={cn('w-12 shrink-0 text-right font-mono text-[11px] tabular-nums', lane.busy ? 'text-success' : 'text-muted-foreground')} title={lane.capacity === null ? 'No fixed limit' : undefined}>
          {lane.busy}/{lane.capacity === null ? (capacity || '∞') : capacity}
        </span>
      </button>
      {/* What went wrong behind the lane: one line, the rest on hover. */}
      {lane.note && <p className='truncate pl-5 pr-2 text-xs text-warning' title={lane.note}>{lane.note}</p>}
    </div>
  );
}

/** A lane's rows on the right: what runs (or, opened, every row it has), a few, then "+N more". */
function LaneRows({ lane, open }: { lane: DashboardLane; open: boolean }) {
  const rows = open ? lane.workers.filter((worker) => worker.busy || worker.task) : lane.workers.filter((worker) => worker.busy);
  const shown = open ? rows : rows.slice(0, LANE_ROWS);
  const more = rows.length - shown.length;
  return (
    <div className='flex flex-col gap-1.5'>
      <p className='eyebrow text-[10px]'>{lane.label}</p>
      <div className='grid gap-1.5 md:grid-cols-2'>
        {shown.map((worker) => <WorkerRow key={worker.id} worker={worker} />)}
      </div>
      {more > 0 && (
        lane.key === 'downloads'
          ? <Link to='/qbittorrent' className='self-start text-xs text-muted-foreground hover:text-foreground hover:underline'>+{more} more in qBittorrent</Link>
          : <p className='text-xs text-muted-foreground'>+{more} more; click the lane to see them</p>
      )}
    </div>
  );
}

function DueWhen({ item }: { item: DashboardDueItem }) {
  if (item.running) return <StatusTag variant='success'>Running</StatusTag>;
  if (item.due_at) return <span className='font-mono text-xs text-foreground'>{timeUntil(item.due_at)}</span>;
  if (item.since) return <span className='text-xs text-muted-foreground'>waiting {timeAgo(item.since).replace(' ago', '')}</span>;
  return <span className='text-xs text-muted-foreground'>—</span>;
}

/**
 * The middle row's card: Up next and both Recently added lists side by side, one height and one
 * header each (room for the anime toggle in every header), the list scrolling inside.
 */
function Panel({ title, icon: Icon, action, children, className }: { title: string; icon: typeof Film; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <Card className={cn('flex h-[26rem] min-w-0 flex-col gap-2 [--card-padding:14px]', className)}>
      <CardHeader className='flex min-h-8 flex-row items-center gap-2'>
        <Icon className='size-4 shrink-0 text-muted-foreground' />
        <CardTitle className='truncate text-base sm:text-base'>{title}</CardTitle>
        {action && <div className='ml-auto'>{action}</div>}
      </CardHeader>
      {children}
    </Card>
  );
}

function RecentList({ title, icon, rows, empty, action }: { title: string; icon: typeof Film; rows: DashboardRecent[]; empty: string; action?: React.ReactNode }) {
  return (
    <Panel title={title} icon={icon} action={action}>
      <CardContent className='flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto'>
        {rows.length === 0 && <p className='text-sm text-muted-foreground'>{empty}</p>}
        {rows.map((row) => (
          <Link
            key={row.kind + ':' + row.key}
            to={row.href}
            className='flex items-center gap-2.5 rounded-item material-row px-1.5 py-1 focus-ring'
          >
            <AnimePoster url={row.poster_url} title={row.title} className='w-7 shrink-0 rounded' />
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
    </Panel>
  );
}

const ANIME_VIEW_KEY = 'bankai:dashboard:recent-anime';

export default function Dashboard() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<DashboardData | undefined>(() => recall<DashboardData>(pagePaths.dashboard));
  const [, setTick] = useState(0);
  // The Workers panel's lanes opened to every row.
  const [openLanes, setOpenLanes] = useState<Set<string>>(new Set());
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
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div>
          <h1 className='font-heading text-2xl font-light'>Dashboard</h1>
          <p className='text-xs text-muted-foreground'>What every worker is doing, what comes next, and what arrived lately.</p>
        </div>
        {data && <p className='text-xs text-muted-foreground'>Updated {timeAgo(data.generated_at)}</p>}
      </div>

      {!data ? (
        <Skeleton shape='card' className='h-10' />
      ) : (
        <>
          {/* The four numbers as one thin strip, not four cards: the lanes below are the content. */}
          <Card className='grid grid-cols-2 gap-0 overflow-hidden [--card-padding:0] lg:grid-cols-4'>
            <Stat index={0} icon={Users} label='Workers' value={`${summary!.workers_busy} / ${summary!.workers_total}`} hint='Workers busy, across every lane below' />
            <Stat index={1} icon={Clock} label='Due' value={String(summary!.due_total)} hint='Queued jobs and releases waiting' />
            <Stat
              index={2}
              icon={Download}
              label='Downloading'
              value={String(summary!.downloading)}
              extra={summary!.download_speed > 0 ? `${formatBytes(summary!.download_speed)}/s` : undefined}
              hint={summary!.download_speed > 0 ? `${formatBytes(summary!.download_speed)}/s in total` : 'Nothing moving right now'}
            />
            <Stat
              index={3}
              icon={Activity}
              label='Automation'
              value={summary!.automation === 'running' ? 'Running' : summary!.automation === 'down' ? 'Down' : 'Idle'}
              tone={summary!.automation === 'down' ? 'text-destructive' : summary!.automation === 'running' ? 'text-success' : undefined}
              hint={summary!.automation === 'down' ? 'The anime automation worker is not reporting' : 'The anime automation worker'}
            />
          </Card>

          {/* Every lane in one panel: on the left a line each, how busy; on the right only what
              runs, so an idle lane costs one line, not a card. */}
          <Card className='gap-3 [--card-padding:14px]'>
            <CardHeader className='flex flex-row items-center gap-2'>
              <Users className='size-4 shrink-0 text-muted-foreground' />
              <CardTitle className='text-base sm:text-base'>Workers</CardTitle>
            </CardHeader>
            <CardContent className='grid gap-x-6 gap-y-4 lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]'>
              <div className='flex flex-col gap-0.5 lg:border-r lg:border-border/60 lg:pr-4'>
                {data.lanes.map((lane) => (
                  <LaneLine
                    key={lane.key}
                    lane={lane}
                    open={openLanes.has(lane.key)}
                    onToggle={() => setOpenLanes((current) => {
                      const next = new Set(current);
                      if (next.has(lane.key)) next.delete(lane.key);
                      else next.add(lane.key);
                      return next;
                    })}
                  />
                ))}
              </div>
              <div className='flex min-w-0 flex-col gap-4'>
                {data.lanes
                  .filter((lane) => lane.busy > 0 || openLanes.has(lane.key))
                  .map((lane) => <LaneRows key={lane.key} lane={lane} open={openLanes.has(lane.key)} />)}
                {!data.lanes.some((lane) => lane.busy > 0 || openLanes.has(lane.key)) && (
                  <p className='flex h-full items-center justify-center py-6 text-sm text-muted-foreground'>Nothing running. Click a lane to see what it did last.</p>
                )}
              </div>
            </CardContent>
          </Card>

          <section className='flex flex-col gap-2'>
            <h2 className='font-heading text-lg font-light'>Up next &amp; recently added</h2>
            {/* Up next is a table: half the row on a wide screen, the full row on a narrower one. */}
            <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-4'>
              <Panel title='Up next' icon={Clock} className='lg:col-span-2'>
                <CardContent className='min-h-0 flex-1 overflow-y-auto'>
                  {/* Fixed columns: the task takes what the lane and the time leave, and is cut there,
                      so the table never scrolls sideways. */}
                  <Table variant='plain' dense className='table-fixed'>
                    <TableHeader className='sticky top-0 z-10 bg-card'>
                      <TableRow>
                        <TableHead className='w-24'>Lane</TableHead>
                        <TableHead>Task</TableHead>
                        <TableHead numeric className='w-28'>When</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {dueRows.length === 0 && (
                        <TableRow><TableCell colSpan={3} className='py-6 text-center text-muted-foreground'>Nothing due.</TableCell></TableRow>
                      )}
                      {dueRows.map(({ group, item, index }) => (
                        <TableRow key={group.lane + ':' + index + ':' + item.title}>
                          <TableCell className='truncate py-1.5 text-xs text-muted-foreground'>{index <= 0 ? group.label : ''}</TableCell>
                          <TableCell className='py-1.5'>
                            {item.href ? (
                              <Link to={item.href} className={cn('block truncate hover:underline', index === -1 ? 'text-muted-foreground' : 'text-foreground')} title={item.title}>
                                {item.position ? <span className='mr-2 font-mono text-xs text-muted-foreground'>#{item.position}</span> : null}
                                {item.title}
                                {item.detail && <span className='ml-2 text-xs text-muted-foreground'>{item.detail}</span>}
                              </Link>
                            ) : (
                              <span className='block truncate text-foreground' title={item.title}>
                                {item.title}
                                {item.detail && <span className='ml-2 text-xs text-muted-foreground'>{item.detail}</span>}
                              </span>
                            )}
                          </TableCell>
                          <TableCell className='whitespace-nowrap py-1.5 text-right'>{index === -1 ? null : <DueWhen item={item} />}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Panel>
              <RecentList title='Movies & Shows' icon={Film} rows={data.recent.mas} empty='Nothing new in the Movies & Shows library.' />
              <RecentList
                title='Anime'
                icon={Sparkles}
                rows={animeView === 'episodes' ? data.recent.anime_episodes ?? [] : data.recent.anime}
                empty='Nothing new in the anime library.'
                action={
                  <ToggleGroup<'episodes' | 'series'>
                    aria-label='Recently added anime as'
                    value={[animeView]}
                    onValueChange={(values) => values[0] && chooseAnimeView(values[0])}
                  >
                    {/* Words where the card is wide enough; icons alone beside a narrow card's title. */}
                    <ToggleGroupItem<'episodes' | 'series'> value='episodes' icon={<ListVideo />} title='Episodes'><span className='sr-only 2xl:not-sr-only'>Episodes</span></ToggleGroupItem>
                    <ToggleGroupItem<'episodes' | 'series'> value='series' icon={<Layers />} title='Series'><span className='sr-only 2xl:not-sr-only'>Series</span></ToggleGroupItem>
                  </ToggleGroup>
                }
              />
            </div>
          </section>

          <DiskUsage />
        </>
      )}
    </div>
  );
}
