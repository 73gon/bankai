import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Activity, BookOpen, Clock, Download, Film, Layers, ListVideo, RefreshCw, RotateCcw, Sparkles, Users } from 'lucide-react';

import { AnimePoster } from '@/components/AnimePoster';
import { DiskUsage } from '@/components/DiskUsage';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@zonsh/ui';
import { Card, CardContent, CardHeader, CardTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Meter } from '@zonsh/ui';
import { NativeSelect, NativeSelectOption } from '@zonsh/ui';
import { Popover, PopoverContent, PopoverDescription, PopoverTitle, PopoverTrigger } from '@zonsh/ui';
import { Skeleton } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableGroup, TableGroupHeader, TableRow } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';
import { toast } from '@/lib/toast';
import {
  api,
  pagePaths,
  recall,
  type Dashboard as DashboardData,
  type DashboardDueItem,
  type DashboardLane,
  type DashboardRecent,
  type SystemUsage,
} from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/utils';
import { cn } from '@zonsh/ui';

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

// The strip's dividers: one row of five on a wide screen; two by two and the last across on a narrow one.
const STAT_EDGES = [
  '',
  'border-l',
  'border-t lg:border-t-0 lg:border-l',
  'border-l border-t lg:border-t-0',
  'col-span-2 border-t lg:col-span-1 lg:border-t-0 lg:border-l',
];

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
        {/* The dot and the count say running or idle; only a lane that is down says so in words. */}
        {down && <span className='ml-auto shrink-0 text-xs text-destructive'>{laneState(lane)}</span>}
        <span className={cn('w-12 shrink-0 text-right font-mono text-[11px] tabular-nums', !down && 'ml-auto', lane.busy ? 'text-success' : 'text-muted-foreground')} title={lane.capacity === null ? 'No fixed limit' : laneState(lane)}>
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

/** What happened to a Recently added row's files: new, encoded again, or tracks taken out. */
const CHANGE: Record<NonNullable<DashboardRecent['change']>, { label: string; variant: 'success' | 'info' | 'repack' | 'accent'; title: string }> = {
  added: { label: 'New', variant: 'success', title: 'New in the library' },
  encoded: { label: 'Encoded', variant: 'info', title: 'Encoded again to HEVC: smaller, the same picture' },
  remuxed: { label: 'Remuxed', variant: 'repack', title: 'Written anew with the audio and subtitle tracks switched off taken out (lossless)' },
  replaced: { label: 'Replaced', variant: 'accent', title: 'Replaced by hand with the torrent of a magnet link' },
};

/** One half of the Recently added card: a header (room for the anime toggle) and its list, scrolling inside. */
function RecentList({ title, icon: Icon, rows, empty, action }: { title: string; icon: typeof Film; rows: DashboardRecent[]; empty: string; action?: React.ReactNode }) {
  return (
    <div className='flex min-h-0 min-w-0 flex-col gap-2'>
      <div className='flex min-h-8 items-center gap-2'>
        <Icon className='size-4 shrink-0 text-muted-foreground' />
        <h3 className='truncate font-heading text-base'>{title}</h3>
        {action && <div className='ml-auto'>{action}</div>}
      </div>
      <div className='flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto'>
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
                  : row.kind === 'episode' || row.kind === 'book'
                  ? row.episode_label
                  : [row.episode_label, row.count > 1 ? `${row.count} ${row.encoded ? '' : 'new '}episodes` : row.episode_label ? null : 'New episode']
                      .filter(Boolean)
                      .join(' · ')}
              </p>
            </div>
            {(() => {
              const change = CHANGE[row.change ?? (row.encoded ? 'encoded' : 'added')];
              return <StatusTag variant={change.variant} title={change.title}>{change.label}</StatusTag>;
            })()}
            <span className='w-14 shrink-0 text-right text-xs text-muted-foreground'>{timeAgo(row.added_at)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Up next's bands: waiting work amber, timed passes blue. */
const GROUP_TONE: Record<string, 'warning' | 'info'> = { backlog: 'warning', scheduled: 'info' };

const UNITS = [
  { label: 'seconds', seconds: 1 },
  { label: 'minutes', seconds: 60 },
  { label: 'hours', seconds: 3600 },
] as const;

function splitInterval(seconds: number): { amount: number; unit: number } {
  if (seconds >= 3600 && seconds % 3600 === 0) return { amount: seconds / 3600, unit: 3600 };
  if (seconds >= 60 && seconds % 60 === 0) return { amount: seconds / 60, unit: 60 };
  return { amount: seconds, unit: 1 };
}

/** A scheduled pass's row in Up next: click it to change how often the pass runs. */
function PassInterval({ item, children, onChanged }: { item: DashboardDueItem; children: React.ReactNode; onChanged: () => void }) {
  const every = item.every_seconds ?? 60;
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(String(splitInterval(every).amount));
  const [unit, setUnit] = useState(splitInterval(every).unit);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    const split = splitInterval(every);
    setAmount(String(split.amount));
    setUnit(split.unit);
  }, [open, every]);

  async function save(seconds: number | null) {
    if (!item.pass_name) return;
    setSaving(true);
    try {
      await api.setPassInterval(item.pass_name, seconds);
      toast.success(`${item.title}: ${seconds === null ? 'back to its default' : 'interval changed'}`);
      setOpen(false);
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  const seconds = Math.round(Number(amount) * unit);
  const changed = item.default_seconds != null && every !== item.default_seconds;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<button type='button' className='block w-full min-w-0 cursor-pointer truncate rounded-item text-left focus-ring hover:underline' title={`${item.title} · click to change how often it runs`} />}
      >
        {children}
      </PopoverTrigger>
      <PopoverContent align='start' className='flex w-72 flex-col gap-3'>
        <div className='flex flex-col gap-1'>
          <PopoverTitle>{item.title}</PopoverTitle>
          <PopoverDescription>
            How often it runs.{item.default_seconds ? ` Default: every ${splitInterval(item.default_seconds).amount} ${UNITS.find((u) => u.seconds === splitInterval(item.default_seconds!).unit)!.label}.` : ''}
          </PopoverDescription>
        </div>
        <form className='flex items-center gap-2' onSubmit={(event) => { event.preventDefault(); if (seconds > 0) void save(seconds); }}>
          <span className='text-sm text-muted-foreground'>Every</span>
          <Input type='number' min={1} step='any' value={amount} onChange={(event) => setAmount(event.target.value)} className='w-20' aria-label='Interval' />
          <NativeSelect value={String(unit)} onChange={(event) => setUnit(Number(event.target.value))} aria-label='Unit'>
            {UNITS.map((u) => <NativeSelectOption key={u.seconds} value={String(u.seconds)}>{u.label}</NativeSelectOption>)}
          </NativeSelect>
        </form>
        <div className='flex items-center gap-2'>
          {changed && (
            <Button size='sm' variant='ghost' disabled={saving} onClick={() => void save(null)}>
              <RotateCcw data-icon='inline-start' /> Default
            </Button>
          )}
          <Button size='sm' className='ml-auto' disabled={saving || !(seconds > 0) || seconds === every} onClick={() => void save(seconds)}>
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** A label, a meter and its number, one line of the System card. */
/** ``warn`` says when the meter turns amber; by default from 90% full. ``note`` stands under the label. */
function UsageLine({ label, value, max = 100, text, hint, warn, note }: {
  label: string;
  value: number | null | undefined;
  max?: number;
  text: string;
  hint?: string;
  warn?: boolean;
  note?: ReactNode;
}) {
  const share = value == null || !max ? null : Math.min(1, value / max);
  const amber = warn ?? (share !== null && share >= 0.9);
  return (
    <div className='flex items-center gap-3' title={hint}>
      <span className='flex w-20 shrink-0 flex-col text-xs text-muted-foreground'>
        {label}
        {note}
      </span>
      <Meter
        value={share === null ? 0 : share * 100}
        max={100}
        aria-label={label}
        className={cn('min-w-0 flex-1', amber && '[&_[data-slot=meter-indicator]]:bg-warning')}
      />
      <span className='w-36 shrink-0 text-right font-mono text-[11px] tabular-nums text-foreground'>{text}</span>
    </div>
  );
}

// Pages moving between swap and memory faster than this mean memory is short; a full swap alone does
// not (programs idle for days leave their pages there, and nothing reads them).
const SWAP_BUSY = 1024 * 1024;

/** Swap: how full, and whether it is in use -- amber only while pages move, not because it is full. */
function SwapLine({ memory }: { memory: NonNullable<SystemUsage['memory']> }) {
  const moving = (memory.swap_in_per_second ?? 0) + (memory.swap_out_per_second ?? 0);
  const known = memory.swap_in_per_second != null;
  const busy = known && moving >= SWAP_BUSY;
  const rates = known ? `${formatBytes(memory.swap_in_per_second ?? 0)}/s in, ${formatBytes(memory.swap_out_per_second ?? 0)}/s out` : '';
  return (
    <UsageLine
      label='Swap'
      value={memory.swap_used}
      max={memory.swap_total}
      text={`${formatBytes(memory.swap_used)} / ${formatBytes(memory.swap_total)}`}
      warn={busy}
      note={known && <span className={cn('text-[10px]', busy ? 'text-warning' : 'text-muted-foreground/70')}>{busy ? 'in use' : 'idle'}</span>}
      hint={
        !known ? 'Whether it is in use shows with the next reading'
        : busy ? `In use: ${rates}. Memory is short; programs wait on the disk.`
        : `Idle (${rates}): the pages in it belong to programs that have not touched them in a long while. Full is no trouble while nothing moves.`
      }
    />
  );
}

function percent(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value)}%`;
}

function uptime(seconds: number | null | undefined) {
  if (!seconds) return null;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days} d ${hours} h` : `${hours} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** CPU, memory, GPU and network of the machine bankai runs on, read every few seconds. */
function SystemCard() {
  const [usage, setUsage] = useState<SystemUsage | null>(null);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    async function poll() {
      try {
        const result = await api.systemUsage();
        if (alive) setUsage(result);
      } catch {
        /* the next read may reach the server */
      }
      if (alive) timer = window.setTimeout(() => void poll(), 3_000);
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  const cpu = usage?.cpu;
  const memory = usage?.memory;
  const network = usage?.network;
  // Beside Up next: headed as it is, the card as tall as its table, its two halves (processor and
  // memory, graphics card) one over the other there; side by side when it has the page's width.
  return (
    <section className='flex min-w-0 flex-col gap-2'>
      <div className='flex items-baseline gap-2'>
        <h2 className='font-heading text-lg font-light'>System</h2>
        {usage && (
          <span className='ml-auto text-xs text-muted-foreground'>
            seireitei (WSL){uptime(usage.uptime_seconds) ? ` · up ${uptime(usage.uptime_seconds)}` : ''}
          </span>
        )}
      </div>
      <Card className='flex-1 gap-3 [--card-padding:14px] xl:h-[26rem] xl:overflow-y-auto'>
        <CardContent className='grid content-start gap-x-8 gap-y-5 sm:grid-cols-2 xl:grid-cols-1'>
          {!usage ? (
            <Skeleton shape='card' className='h-24 sm:col-span-2 xl:col-span-1' />
          ) : (
            <>
              <div className='flex flex-col gap-2'>
                <p className='eyebrow text-[10px]'>Processor and memory</p>
                <UsageLine
                  label='CPU'
                  value={cpu?.percent}
                  text={`${percent(cpu?.percent)}${cpu?.cores ? ` of ${cpu.cores} cores` : ''}`}
                  hint={cpu ? `Load average ${cpu.load.join(' / ')} (1, 5, 15 min)` : undefined}
                />
                <UsageLine
                  label='Memory'
                  value={memory?.used}
                  max={memory?.total}
                  text={memory ? `${formatBytes(memory.used)} / ${formatBytes(memory.total)}` : '—'}
                  hint={memory ? `${formatBytes(memory.cached)} more is file cache, given back when needed` : undefined}
                />
                {memory && memory.swap_total > 0 && <SwapLine memory={memory} />}
                <div className='flex items-center gap-3 text-xs'>
                  <span className='w-20 shrink-0 text-muted-foreground'>Network</span>
                  <span className='font-mono text-[11px] tabular-nums text-foreground'>
                    ↓ {network?.received_per_second == null ? '—' : `${formatBytes(network.received_per_second)}/s`}
                    <span className='mx-2 text-muted-foreground'>·</span>
                    ↑ {network?.sent_per_second == null ? '—' : `${formatBytes(network.sent_per_second)}/s`}
                  </span>
                  {cpu && <span className='ml-auto font-mono text-[11px] tabular-nums text-muted-foreground' title='Load average over 1, 5 and 15 minutes'>load {cpu.load.join(' ')}</span>}
                </div>
              </div>
              <div className='flex flex-col gap-2'>
                {(usage.gpus ?? []).length === 0 && <p className='text-sm text-muted-foreground'>No NVIDIA graphics card found (nvidia-smi).</p>}
                {(usage.gpus ?? []).map((gpu) => (
                  <div key={gpu.name} className='flex flex-col gap-2'>
                    <p className='eyebrow flex items-center gap-2 text-[10px]'>
                      <span className='truncate'>{gpu.name.replace(/^NVIDIA (GeForce )?/, '')}</span>
                      <span className='ml-auto font-mono normal-case tracking-normal text-muted-foreground'>
                        {[gpu.temperature != null ? `${Math.round(gpu.temperature)} °C` : null, gpu.power_watts != null ? `${Math.round(gpu.power_watts)} W` : null].filter(Boolean).join(' · ')}
                      </span>
                    </p>
                    <UsageLine label='GPU' value={gpu.percent} text={percent(gpu.percent)} />
                    <UsageLine label='Encoder' value={gpu.encoder_percent} text={percent(gpu.encoder_percent)} hint='NVENC: the Encode page encodes on it' />
                    <UsageLine
                      label='Video memory'
                      value={gpu.memory_used}
                      max={gpu.memory_total ?? undefined}
                      text={gpu.memory_used != null && gpu.memory_total ? `${formatBytes(gpu.memory_used)} / ${formatBytes(gpu.memory_total)}` : '—'}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

type RecentView = 'episodes' | 'series';

/** A Recently added list one row per episode or grouped into series; remembered per list in this browser. */
function useRecentView(key: string): [RecentView, (view: RecentView) => void] {
  const [view, setView] = useState<RecentView>(() => {
    try {
      return localStorage.getItem(key) === 'episodes' ? 'episodes' : 'series';
    } catch {
      return 'series';
    }
  });
  function choose(next: RecentView) {
    setView(next);
    try {
      localStorage.setItem(key, next);
    } catch {
      /* a remembered view is a convenience */
    }
  }
  return [view, choose];
}

function RecentViewToggle({ label, view, onChange }: { label: string; view: RecentView; onChange: (view: RecentView) => void }) {
  return (
    <ToggleGroup<RecentView> aria-label={label} value={[view]} onValueChange={(values) => values[0] && onChange(values[0])}>
      {/* Words where the card is wide enough; icons alone beside a narrow card's title. */}
      <ToggleGroupItem<RecentView> value='episodes' icon={<ListVideo />} title='Episodes'><span className='sr-only 2xl:not-sr-only'>Episodes</span></ToggleGroupItem>
      <ToggleGroupItem<RecentView> value='series' icon={<Layers />} title='Series'><span className='sr-only 2xl:not-sr-only'>Series</span></ToggleGroupItem>
    </ToggleGroup>
  );
}

export default function Dashboard() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [data, setData] = useState<DashboardData | undefined>(() => recall<DashboardData>(pagePaths.dashboard));
  const [, setTick] = useState(0);
  // The Workers panel's lanes opened to every row.
  const [openLanes, setOpenLanes] = useState<Set<string>>(new Set());
  // Arrivals one row per episode, or grouped into their series; remembered per library.
  const [animeView, chooseAnimeView] = useRecentView('bankai:dashboard:recent-anime');
  const [masView, chooseMasView] = useRecentView('bankai:dashboard:recent-mas');

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

  return (
    <div className='flex flex-col gap-4'>
      <h1 className='font-heading text-2xl font-light'>Dashboard</h1>

      {!data ? (
        <Skeleton shape='card' className='h-10' />
      ) : (
        <>
          {/* The four numbers as one thin strip, not four cards: the lanes below are the content. */}
          <Card className='grid grid-cols-2 gap-0 overflow-hidden [--card-padding:0] lg:grid-cols-5'>
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
            <Stat
              index={4}
              icon={RefreshCw}
              label='Updated'
              value={timeAgo(data.generated_at)}
              hint='When this page last heard from the server; it asks every few seconds'
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

          {/* Recently added across the page: three libraries side by side need the width (beside Up next
              their titles and toggles were cut). */}
          <section className='flex min-w-0 flex-col gap-2'>
            <h2 className='font-heading text-lg font-light'>Recently added</h2>
            {/* One card, the three libraries side by side with a rule between (stacked on a narrow screen). */}
            <Card className='grid min-w-0 gap-4 [--card-padding:14px] md:h-[26rem] md:grid-cols-[minmax(0,1fr)_1px_minmax(0,1fr)_1px_minmax(0,1fr)] [&>*]:max-md:max-h-80'>
              <RecentList
                title='Movies & Shows'
                icon={Film}
                rows={masView === 'episodes' ? data.recent.mas_episodes ?? data.recent.mas : data.recent.mas}
                empty='Nothing new in the Movies & Shows library.'
                action={<RecentViewToggle label='Recently added movies and shows as' view={masView} onChange={chooseMasView} />}
              />
              <div className='hidden bg-border/60 md:block' aria-hidden='true' />
              <RecentList
                title='Anime'
                icon={Sparkles}
                rows={animeView === 'episodes' ? data.recent.anime_episodes ?? [] : data.recent.anime}
                empty='Nothing new in the anime library.'
                action={<RecentViewToggle label='Recently added anime as' view={animeView} onChange={chooseAnimeView} />}
              />
              <div className='hidden bg-border/60 md:block' aria-hidden='true' />
              <RecentList title='Manga' icon={BookOpen} rows={data.recent.manga ?? []} empty='No manga or light novel filed yet.' />
            </Card>
          </section>

          {/* Up next and System side by side, under the workers and Recently added. */}
          <div className='grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]'>
            <section className='flex min-w-0 flex-col gap-2'>
              <h2 className='font-heading text-lg font-light'>Up next</h2>
              {/* A band per lane (Backlog, Scheduled), pinned while its rows scroll; the task takes what
                  the time leaves and is cut there, so the table never scrolls sideways. A scheduled
                  pass's row opens its interval. */}
              <Table dense className='table-fixed' containerProps={{ className: 'h-[26rem] overflow-y-auto' }}>
                {(data.due ?? []).every((group) => group.items.length === 0) && (
                  <TableBody>
                    <TableRow><TableCell colSpan={2} className='py-6 text-center text-muted-foreground'>Nothing due.</TableCell></TableRow>
                  </TableBody>
                )}
                {(data.due ?? []).filter((group) => group.items.length > 0).map((group) => (
                  <TableGroup key={group.lane} collapsible={false}>
                    <TableGroupHeader sticky colSpan={2} count={group.items.length + group.more}>
                      {/* A coloured tag, so the band never reads as one more row. */}
                      <StatusTag variant={GROUP_TONE[group.lane] ?? 'secondary'}>{group.label}</StatusTag>
                    </TableGroupHeader>
                    {[...group.items, ...(group.more > 0 ? [{ title: `${group.more} more`, href: group.items[0]?.href ?? null } as DashboardDueItem] : [])].map((item, index) => {
                      const more = index >= group.items.length;
                      const label = (
                        <>
                          {item.position ? <span className='mr-2 font-mono text-xs text-muted-foreground'>#{item.position}</span> : null}
                          {item.title}
                          {item.detail && <span className='ml-2 text-xs text-muted-foreground'>{item.detail}</span>}
                        </>
                      );
                      return (
                        <TableRow key={group.lane + ':' + index + ':' + item.title}>
                          <TableCell className='py-1.5'>
                            {item.pass_name ? (
                              <PassInterval item={item} onChanged={() => void api.dashboard().then(setData).catch(() => undefined)}>{label}</PassInterval>
                            ) : item.href ? (
                              <Link to={item.href} className={cn('block truncate hover:underline', more ? 'text-muted-foreground' : 'text-foreground')} title={item.title}>{label}</Link>
                            ) : (
                              <span className='block truncate text-foreground' title={item.title}>{label}</span>
                            )}
                          </TableCell>
                          <TableCell className='w-28 whitespace-nowrap py-1.5 text-right'>{more ? null : <DueWhen item={item} />}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableGroup>
                ))}
              </Table>
            </section>
            <SystemCard />
          </div>

          <DiskUsage />
        </>
      )}
    </div>
  );
}
