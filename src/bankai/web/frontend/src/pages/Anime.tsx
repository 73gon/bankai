import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Captions,
  Download,
  ExternalLink,
  Film,
  Link2,
  Magnet,
  Volume2,
  Search,
  Tv,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/clipboard';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  api,
  type AniListAnime,
  type AnimeAniListMatch,
  type AnimeEntry,
  type AnimeSearchPage,
  type AnimeSource,
} from '@/lib/api';
import { POSTER_IMG, posterSources } from '@/lib/posters';
import { anilistName, anilistUrl, formatLabel } from '@/lib/anilist';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { AnimePoster } from '@/components/AnimePoster';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/zon/ui/dialog';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/zon/ui/drawer';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Input } from '@/zon/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Skeleton } from '@/zon/ui/skeleton';
import { Spinner } from '@/zon/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tag } from '@/zon/ui/tag';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/zon/ui/tooltip';

/** What both sources filter on. Every term is matched ignoring case. */
type Common = {
  quality: string;
  publisher: string;
  titleFilters: string;
  descriptionFilters: string;
  /** As typed; empty for none. */
  minSeeders: string;
};
type NyaaFilters = Common & { category: string };
type NekobtFilters = Common & { subtitles: string[]; audio: string[]; batch: string };
type SourceView<F> = { query: string; filters: F };
type Saved = { source: AnimeSource; nyaa: SourceView<NyaaFilters>; nekobt: SourceView<NekobtFilters> };

const COMMON: Common = { quality: 'all', publisher: '', titleFilters: '', descriptionFilters: '', minSeeders: '' };
const DEFAULT_SAVED: Saved = {
  source: 'nyaa',
  nyaa: { query: '', filters: { ...COMMON, category: '1_0' } },
  nekobt: { query: '', filters: { ...COMMON, subtitles: [], audio: [], batch: 'any' } },
};
// The source, each source's search and filters: the tab opens where it was left.
const SAVED_KEY = 'bankai:anime-discover';
const OLD_FILTERS_KEY = 'bankai:anime-filters';

const CATEGORY_ITEMS = [
  { value: '1_0', label: 'All anime' },
  { value: '1_2', label: 'English-translated' },
  { value: '1_3', label: 'Non-English' },
  { value: '1_4', label: 'Raw' },
  { value: '1_1', label: 'Music video' },
];
const QUALITY_ITEMS = [
  { value: 'all', label: 'Quality' },
  { value: '2160p', label: '2160p' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '480p', label: '480p' },
];
const BATCH_ITEMS = [
  { value: 'any', label: 'Batches: any' },
  { value: 'batch', label: 'Batches only' },
  { value: 'single', label: 'Single episodes' },
];
const KIND_ITEMS = [
  { value: 'show', label: 'Show episodes' },
  { value: 'movie', label: 'Movie' },
];
const SOURCE_LABEL: Record<AnimeSource, string> = { nyaa: 'Nyaa', nekobt: 'nekobt' };

function strings(value: unknown, fallback: string[]): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : fallback;
}

function text(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback;
}

function loadSaved(): Saved {
  try {
    const raw = JSON.parse(localStorage.getItem(SAVED_KEY) || 'null') as Partial<Saved> | null;
    // Before the two sources: Nyaa's filters alone, under their own key.
    const old = raw ? null : (JSON.parse(localStorage.getItem(OLD_FILTERS_KEY) || '{}') as Record<string, unknown>);
    const nyaa = (raw?.nyaa?.filters ?? old ?? {}) as Record<string, unknown>;
    const nekobt = (raw?.nekobt?.filters ?? {}) as Record<string, unknown>;
    const common = (from: Record<string, unknown>): Common => ({
      quality: text(from.quality, COMMON.quality),
      publisher: text(from.publisher, ''),
      titleFilters: text(from.titleFilters, ''),
      descriptionFilters: text(from.descriptionFilters, ''),
      minSeeders: text(from.minSeeders, ''),
    });
    return {
      source: raw?.source === 'nekobt' ? 'nekobt' : 'nyaa',
      nyaa: { query: text(raw?.nyaa?.query, ''), filters: { ...common(nyaa), category: text(nyaa.category, '1_0') } },
      nekobt: {
        query: text(raw?.nekobt?.query, ''),
        filters: {
          ...common(nekobt),
          subtitles: strings(nekobt.subtitles, []),
          audio: strings(nekobt.audio, []),
          batch: BATCH_ITEMS.some((item) => item.value === nekobt.batch) ? (nekobt.batch as string) : 'any',
        },
      },
    };
  } catch {
    return structuredClone(DEFAULT_SAVED);
  }
}

function store(saved: Saved) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(saved));
  } catch {
    /* localStorage can be unavailable in locked-down browsers. */
  }
}

// Kept while the app runs: the searched view and its page, per source, and what came back.
let discoverView: Saved = loadSaved();
const pages: Record<AnimeSource, number> = { nyaa: 0, nekobt: 0 };
const resultCache = new Map<string, AnimeSearchPage>();

function cacheKey(source: AnimeSource, view: SourceView<NyaaFilters | NekobtFilters>, page: number) {
  return JSON.stringify([source, view, page]);
}

function fetchPage(source: AnimeSource, view: SourceView<NyaaFilters | NekobtFilters>, page: number) {
  const f = view.filters;
  const common = {
    page,
    quality: f.quality,
    publisher: f.publisher.trim(),
    titleFilters: f.titleFilters.trim(),
    descriptionFilters: f.descriptionFilters.trim(),
    minSeeders: Math.max(0, Number.parseInt(f.minSeeders, 10) || 0) || undefined,
  };
  if (source === 'nekobt') {
    const n = f as NekobtFilters;
    const batch = n.batch === 'batch' ? true : n.batch === 'single' ? false : undefined;
    return api.animeSearchNekobt(view.query, { ...common, subtitles: n.subtitles, audio: n.audio, batch });
  }
  return api.animeSearch(view.query, { ...common, category: (f as NyaaFilters).category });
}

// A language code as nekobt writes it (de, pt-br, es-419), in words.
const languageNames = (() => {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' });
  } catch {
    return null;
  }
})();

function languageName(code: string): string {
  try {
    return languageNames?.of(code) ?? code;
  } catch {
    return code;
  }
}

/** Episode numbers as runs: [1, 2, 3, 5] -> "E01–E03, E05". */
function episodeRuns(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const runs: string[] = [];
  const label = (n: number) => 'E' + String(n).padStart(2, '0');
  for (let i = 0; i < sorted.length; i++) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    runs.push(j > i ? `${label(sorted[i])}–${label(sorted[j])}` : label(sorted[i]));
    i = j;
  }
  return runs.join(', ');
}

// Matroska's language codes as the two-letter ones shown (and named by Intl).
const TWO_LETTER: Record<string, string> = {
  ger: 'de', eng: 'en', jpn: 'ja', fre: 'fr', spa: 'es', ita: 'it', por: 'pt', rus: 'ru', ara: 'ar', chi: 'zh',
  kor: 'ko', pol: 'pl', tur: 'tr', dut: 'nl', swe: 'sv', nor: 'no', dan: 'da', fin: 'fi', cze: 'cs', hun: 'hu',
  gre: 'el', heb: 'he', hin: 'hi', tha: 'th', vie: 'vi', ind: 'id', may: 'ms', ukr: 'uk', rum: 'ro',
};
// Shown first and always by name; the rest of a long subtitle list is counted.
const FIRST_LANGUAGES = ['ger', 'eng', 'jpn'];

function shortLanguage(code: string): string {
  return (TWO_LETTER[code] ?? code).toUpperCase();
}

function longLanguage(code: string): string {
  return languageName(TWO_LETTER[code] ?? code);
}

/** "JA", "EN DE 12/28 +13": a language short, with how many episodes have it when not all do. */
function languageList(counts: Record<string, number>, read: number, limit: number) {
  const codes = Object.keys(counts).sort((a, b) => {
    const rank = (code: string) => (FIRST_LANGUAGES.includes(code) ? FIRST_LANGUAGES.indexOf(code) : 99);
    return rank(a) - rank(b) || counts[b] - counts[a];
  });
  const shown = codes.slice(0, limit).map((code) => (counts[code] < read ? `${shortLanguage(code)} ${counts[code]}/${read}` : shortLanguage(code)));
  const rest = codes.length - limit;
  return { text: shown.join(' ') + (rest > 0 ? ` +${rest}` : ''), title: codes.map((code) => `${longLanguage(code)} ${counts[code]}/${read}`).join(', ') };
}

/** What the library has of a row's release (its one episode, or of a batch its entry's episodes),
 *  those episodes' audio and subtitle languages, and the German the release would add to them. */
function LibraryTags({ match }: { match: AnimeAniListMatch | null }) {
  const owned = match?.owned ?? [];
  if (!match || !owned.length) return null;
  if (match.episode != null && !owned.includes(match.episode)) return null;
  const single = match.episode != null;
  const all = !single && match.episodes != null && owned.length >= match.episodes;
  const title = single
    ? 'This episode is in the library already: a download leaves it out.'
    : `In the library: ${episodeRuns(owned)}. A download leaves those files out.`;
  const langs = match.languages;
  const audio = langs?.read ? languageList(langs.audio, langs.read, 3) : null;
  const subtitles = langs?.read ? languageList(langs.subtitles, langs.read, 2) : null;
  const gainTitle = (gain: NonNullable<AnimeAniListMatch['gains']>[number]) =>
    `This release has ${longLanguage(gain.language)} ${gain.kind === 'audio' ? 'audio' : 'subtitles'}; ${gain.episodes} of your ${gain.of} episode${gain.of === 1 ? '' : 's'} have none. A download leaves episodes you have out: swap them with Replace in the library.`;
  return (
    <div className='mt-1 flex flex-wrap items-center gap-1'>
      <StatusTag variant={single || all ? 'success' : 'secondary'} title={title}>
        {single ? 'In library' : all ? 'All in library' : `${owned.length}${match.episodes ? `/${match.episodes}` : ''} in library`}
      </StatusTag>
      {audio?.text && <StatusTag variant='outline' title={`Audio in your episodes: ${audio.title}`}><Volume2 />{audio.text}</StatusTag>}
      {subtitles?.text && <StatusTag variant='outline' title={`Subtitles in your episodes: ${subtitles.title}`}><Captions />{subtitles.text}</StatusTag>}
      {(match.gains ?? []).map((gain) => (
        <StatusTag key={gain.kind + gain.language} variant='accent' title={gainTitle(gain)}>
          + {longLanguage(gain.language)} {gain.kind === 'audio' ? 'audio' : 'subs'}
        </StatusTag>
      ))}
    </div>
  );
}

/** The AniList entry a download is filed under, however it was chosen. */
type Chosen = {
  anilist_id: number;
  /** How it is shown: English, else romaji. */
  name: string;
  /** The romanised title, as releases are named. */
  romaji: string | null;
  year: number | null;
  /** AniList's format: TV, MOVIE, OVA, ... */
  format: string | null;
  cover: string | null;
};

/** The entry told from a release's title. */
function fromMatch(match: AnimeAniListMatch): Chosen {
  return {
    anilist_id: match.anilist_id,
    name: match.name || match.title,
    romaji: match.title,
    year: match.year ?? null,
    format: match.format ?? null,
    cover: match.cover,
  };
}

/** The entry picked by hand. */
function fromPick(anime: AniListAnime): Chosen {
  return {
    anilist_id: anime.anilist_id,
    name: anilistName(anime),
    romaji: anime.romaji,
    year: anime.year,
    format: anime.format,
    cover: anime.cover,
  };
}

function isMovie(format: string | null | undefined) {
  return (format ?? '').toUpperCase() === 'MOVIE';
}

function Poster({ url, title, movie }: { url: string | null | undefined; title: string; movie: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className='flex aspect-[2/3] w-20 shrink-0 items-center justify-center overflow-hidden rounded-item border border-field-edge bg-field-surface sm:w-24'>
      {url && !failed ? (
        <img
          {...posterSources(url)}
          {...POSTER_IMG}
          alt={`${title} poster`}
          className='h-full w-full object-cover'
          onError={() => setFailed(true)}
        />
      ) : movie ? (
        <Film className='text-muted-foreground' />
      ) : (
        <Tv className='text-muted-foreground' />
      )}
    </div>
  );
}

type SortKey = 'quality' | 'seeders' | 'leechers' | 'size' | 'uploaded';
// The first click already gives the answer wanted most: the best, most, largest, newest.
const FIRST_DIR: Record<SortKey, SortDir> = { quality: 'desc', seeders: 'desc', leechers: 'desc', size: 'desc', uploaded: 'desc' };
const QUALITY_RANK: Record<string, number> = { '2160p': 4, '1080p': 3, '720p': 2, '480p': 1 };

function uploadedAt(entry: AnimeEntry): number {
  return entry.published_at ? Date.parse(entry.published_at) || 0 : 0;
}

function sorted(items: AnimeEntry[], sort: SortState<SortKey>): AnimeEntry[] {
  const value = (entry: AnimeEntry): number => {
    switch (sort.key) {
      case 'quality': return (QUALITY_RANK[entry.quality ?? ''] ?? 0) * 1e7 + entry.seeders;
      case 'leechers': return entry.leechers;
      case 'size': return entry.size_bytes;
      case 'uploaded': return uploadedAt(entry);
      default: return entry.seeders;
    }
  };
  const factor = sort.dir === 'asc' ? 1 : -1;
  return [...items].sort((a, b) => (value(a) - value(b)) * factor);
}

function formatUploaded(entry: AnimeEntry): string {
  const at = uploadedAt(entry);
  if (!at) return '—';
  const date = new Date(at);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** An icon button with its tooltip: the actions column holds four of them. */
function RowAction({ label, onClick, href, children }: { label: string; onClick?: () => void; href?: string; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          href
            ? <Button variant='ghost' size='icon-sm' aria-label={label} nativeButton={false} render={<a href={href} target='_blank' rel='noreferrer' />} />
            : <Button variant='ghost' size='icon-sm' aria-label={label} onClick={onClick} />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** The release groups from the anime settings, or any name typed by hand. */
function PublisherField({ value, onChange, publishers }: { value: string; onChange: (value: string) => void; publishers: string[] }) {
  // "Other…" chosen: a name typed by hand, not one of the saved groups.
  const [other, setOther] = useState(false);
  const typed = value.trim();
  const saved = publishers.find((name) => name.toLowerCase() === typed.toLowerCase());
  const choice = other ? '__other' : !typed ? '__any' : saved ?? '__other';
  const items = [
    { value: '__any', label: 'Any publisher' },
    ...publishers.map((name) => ({ value: name, label: name })),
    { value: '__other', label: 'Other…' },
  ];
  return (
    <div className='flex gap-2'>
      <Select
        items={items}
        value={choice}
        onValueChange={(next) => {
          if (next === null) return;
          setOther(next === '__other');
          onChange(next === '__any' || next === '__other' ? '' : String(next));
        }}
      >
        <SelectTrigger aria-label='Publisher' className='w-44 min-w-0'><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
          </SelectGroup>
        </SelectContent>
      </Select>
      {choice === '__other' && (
        <Input
          autoFocus={other}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder='Release group'
          aria-label='Publisher by name'
          className='w-40'
        />
      )}
    </div>
  );
}

/** Several languages, any of which will do. */
function LanguagesField({ label, value, onChange, languages }: { label: string; value: string[]; onChange: (value: string[]) => void; languages: string[] }) {
  // A language kept from an earlier search stays choosable after Settings dropped it.
  const codes = [...new Set([...languages, ...value])];
  const items = codes.map((code) => ({ value: code, label: languageName(code) }));
  return (
    <Select multiple items={items} value={value} onValueChange={(next) => onChange((next as string[] | null) ?? [])}>
      <SelectTrigger aria-label={label} className='w-52 min-w-0'>
        <SelectValue>
          {(chosen: string[]) => `${label}: ${chosen?.length ? chosen.map(languageName).join(', ') : 'any'}`}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label} <span className='font-mono text-xs text-muted-foreground'>{item.value}</span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

function SimpleSelect({ label, items, value, onChange, className }: { label: string; items: { value: string; label: string }[]; value: string; onChange: (value: string) => void; className?: string }) {
  return (
    <Select items={items} value={value} onValueChange={(next) => next !== null && onChange(String(next))}>
      <SelectTrigger aria-label={label} className={cn('min-w-0', className)}><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {items.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

const MARKDOWN = {
  h1: (props: any) => <h1 className='font-heading text-xl font-light' {...props} />,
  h2: (props: any) => <h2 className='font-heading text-lg font-light' {...props} />,
  h3: (props: any) => <h3 className='font-heading text-base font-light' {...props} />,
  p: (props: any) => <p className='whitespace-pre-wrap' {...props} />,
  a: (props: any) => <a className='text-info underline underline-offset-4' target='_blank' rel='noreferrer' {...props} />,
  ul: (props: any) => <ul className='list-disc pl-5' {...props} />,
  ol: (props: any) => <ol className='list-decimal pl-5' {...props} />,
  img: (props: any) => <img className='inline-block max-h-64 max-w-full' loading='lazy' {...props} />,
  table: (props: any) => <table className='w-full border-collapse text-left text-xs' {...props} />,
  th: (props: any) => <th className='border border-border bg-control-surface px-3 py-2 font-medium' {...props} />,
  td: (props: any) => <td className='border border-border px-3 py-2 align-top' {...props} />,
};

/** A release's description, from the bottom: loaded when opened. */
function DescriptionDrawer({ entry, onClose }: { entry: AnimeEntry | null; onClose: () => void }) {
  const [body, setBody] = useState<string | null>(null);
  useEffect(() => {
    if (!entry) return;
    setBody(null);
    let cancelled = false;
    const load = entry.source === 'nekobt'
      ? api.nekobtDescription(String(entry.id)).then((result) => result.description)
      : api.animeDetail(entry.detail_url).then((result) => result.description);
    load
      .then((value) => !cancelled && setBody(value || 'This release has no description.'))
      .catch((error: any) => {
        if (cancelled) return;
        toast.error(error.message);
        onClose();
      });
    return () => {
      cancelled = true;
    };
  }, [entry]);
  return (
    <Drawer side='bottom' open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <DrawerContent size='full' className='max-h-[85vh]'>
        <DrawerHeader>
          <DrawerTitle className='break-words font-mono text-sm font-normal'>{entry?.title}</DrawerTitle>
          <DrawerDescription>Description on {entry ? SOURCE_LABEL[entry.source ?? 'nyaa'] : ''}</DrawerDescription>
        </DrawerHeader>
        <div className='mx-auto flex w-full max-w-5xl min-h-0 flex-col gap-3 overflow-auto px-6 pb-4 text-sm leading-relaxed text-foreground'>
          {body === null ? (
            <span className='flex items-center gap-2 text-muted-foreground'><Spinner label='Loading the description' /> Loading the description…</span>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN}>{body}</ReactMarkdown>
          )}
        </div>
      </DrawerContent>
    </Drawer>
  );
}

export default function Anime() {
  const [source, setSource] = useState<AnimeSource>(discoverView.source);
  // What the fields hold, per source, and what was last searched (the results' view).
  const [drafts, setDrafts] = useState<Saved>(() => structuredClone(discoverView));
  const [searched, setSearched] = useState<Saved>(() => structuredClone(discoverView));
  const [page, setPage] = useState(pages[discoverView.source]);
  // The release groups and the nekobt languages set in the anime settings.
  const [publishers, setPublishers] = useState<string[]>([]);
  const [languages, setLanguages] = useState<string[]>([]);
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'seeders', dir: 'desc' });
  const view = searched[source];
  const initial = resultCache.get(cacheKey(source, view, page));
  const [result, setResult] = useState<AnimeSearchPage | null>(initial ?? null);
  const [loading, setLoading] = useState(!initial);
  const [described, setDescribed] = useState<AnimeEntry | null>(null);
  const [selectedEntry, setSelectedEntry] = useState<AnimeEntry | null>(null);
  const [chosen, setChosen] = useState<Chosen | null>(null);
  // The chosen entry's episodes in the library, for the dialog to say what a download leaves out.
  const [library, setLibrary] = useState<{ anilistId: number; owned: number[]; episodes: number | null } | null>(null);
  const [kind, setKind] = useState<'show' | 'movie'>('show');
  // The AniList picker, opened with what to search for first.
  const [pickFor, setPickFor] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [manualEpisode, setManualEpisode] = useState('');

  useEffect(() => {
    api.animePublishers().then((settings) => {
      setPublishers(settings.publishers);
      setLanguages(settings.nekobt_languages ?? []);
    }).catch(() => undefined);
  }, []);

  // The fields as typed are kept too: back on the tab, nothing has to be typed again.
  useEffect(() => store({ ...drafts, source }), [drafts, source]);

  useEffect(() => {
    discoverView = { ...searched, source };
    pages[source] = page;
    const key = cacheKey(source, view, page);
    const cached = resultCache.get(key);
    setResult(cached ?? null);
    setLoading(!cached);
    let cancelled = false;
    fetchPage(source, view, page)
      .then((response) => {
        if (cancelled) return;
        resultCache.set(key, response);
        setResult(response);
        if (response.has_next) {
          const next = cacheKey(source, view, page + 1);
          if (!resultCache.has(next)) fetchPage(source, view, page + 1).then((more) => resultCache.set(next, more)).catch(() => undefined);
        }
      })
      .catch((error) => !cancelled && toast.error(error.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [source, view, page]);

  const rows = useMemo(() => (result ? sorted(result.items, sort) : []), [result, sort]);
  const aliases = useMemo(
    () => result?.aliases.filter((alias) => alias.toLocaleLowerCase() !== view.query.toLocaleLowerCase()) ?? [],
    [view.query, result],
  );
  const draft = drafts[source];

  function switchSource(next: AnimeSource) {
    setSource(next);
    setPage(pages[next]);
  }

  function setQuery(query: string) {
    setDrafts((old) => ({ ...old, [source]: { ...old[source], query } }));
  }

  function setFilter<K extends keyof NyaaFilters | keyof NekobtFilters>(key: K, value: unknown) {
    setDrafts((old) => ({ ...old, [source]: { ...old[source], filters: { ...old[source].filters, [key]: value } } }));
  }

  function applySearch() {
    const next = { ...drafts[source], query: drafts[source].query.trim() };
    setDrafts((old) => ({ ...old, [source]: next }));
    setSearched((old) => ({ ...old, [source]: structuredClone(next) }));
    setPage(0);
  }

  function choose(anime: Chosen) {
    setChosen(anime);
    setKind(isMovie(anime.format) ? 'movie' : 'show');
  }

  function openDownload(entry: AnimeEntry) {
    setSelectedEntry(entry);
    setChosen(null);
    setManualEpisode(entry.anilist?.episode == null ? '' : String(entry.anilist.episode));
    if (entry.anilist) {
      choose(fromMatch(entry.anilist));
    } else {
      // Nothing told from the title: the AniList entry is picked first.
      setPickFor(entry.title.replace(/^(\s*\[[^\]]+\])+\s*/, '').replace(/\s+-\s+\d+.*$/, ''));
    }
  }

  async function pickAnime(anime: AniListAnime) {
    choose(fromPick(anime));
    setPickFor(null);
  }

  async function copyMagnet(entry: AnimeEntry) {
    try {
      await copyText(entry.magnet_uri);
      toast.success('Magnet link copied');
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    const anilistId = chosen?.anilist_id;
    if (!selectedEntry || anilistId == null) return;
    let live = true;
    api.animeOwned(anilistId).then(
      (answer) => live && setLibrary({ anilistId, ...answer }),
      () => live && setLibrary(null),
    );
    return () => {
      live = false;
    };
  }, [selectedEntry, chosen?.anilist_id]);

  /** What the download leaves out because the library has it, said before it is queued. */
  function ownedNotice(): string | null {
    if (!chosen || kind !== 'show' || library?.anilistId !== chosen.anilist_id || !library.owned.length) return null;
    const episode = manualEpisode.trim() ? Number.parseInt(manualEpisode, 10) : null;
    if (episode != null) {
      return library.owned.includes(episode)
        ? `Episode ${episode} is in the library already, so nothing would be downloaded. To swap it for this release, use Replace in the library.`
        : null;
    }
    if (library.episodes != null && library.owned.length >= library.episodes) {
      return 'Every episode of this entry is in the library already, so nothing would be downloaded. To swap them for this release, use Replace in the library.';
    }
    const count = library.episodes ? ` (${library.owned.length} of ${library.episodes})` : '';
    return `In the library already: ${episodeRuns(library.owned)}${count}. Those files are left out of the download; only the others come down.`;
  }

  async function downloadSelected() {
    if (!selectedEntry || !chosen) return;
    const episode = kind === 'show' && manualEpisode.trim() ? Number.parseInt(manualEpisode, 10) : null;
    if (episode != null && !(episode >= 1)) {
      toast.error('The episode must be a positive number.');
      return;
    }
    setDownloading(true);
    try {
      // A show goes on into the library, as every other anime download does; staged, it waited
      // for a Transfer click per episode (Fullmetal Alchemist: Brotherhood, 64 of them).
      await api.animeDownload(selectedEntry, chosen.anilist_id, kind, episode, kind === 'show');
      toast.success(`${chosen.name} was added to the queue from ${SOURCE_LABEL[selectedEntry.source ?? 'nyaa']}.`);
      setSelectedEntry(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDownloading(false);
    }
  }

  const nekobt = source === 'nekobt';
  const toggleSort = (key: SortKey) => setSort((current) => nextSort(current, key, FIRST_DIR));

  return (
    <div className='flex h-full min-h-0 flex-col gap-3'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div className='flex flex-wrap items-baseline gap-2'>
          <h1 className='font-heading text-2xl font-light tracking-tight'>Search</h1>
          <span className='text-sm text-muted-foreground'>— Browse releases and download them filed by their AniList entry.</span>
        </div>
        <ToggleGroup aria-label='Source' value={[source]} onValueChange={(values) => { if (values[0]) switchSource(values[0] as AnimeSource); }}>
          <ToggleGroupItem value='nyaa'>nyaa.si</ToggleGroupItem>
          <ToggleGroupItem value='nekobt'>nekobt.to</ToggleGroupItem>
        </ToggleGroup>
      </div>

      {/* Two rows of filters, then the requests they made: the table gets the height. */}
      <div className='flex shrink-0 flex-col gap-2'>
        <div className='flex flex-wrap gap-2'>
          <Input
            value={draft.query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && applySearch()}
            placeholder='Anime title in English or Japanese — leave empty to browse'
            aria-label='Anime title'
            className='min-w-60 flex-1'
          />
          <SimpleSelect label='Quality' items={QUALITY_ITEMS} value={draft.filters.quality} onChange={(value) => setFilter('quality', value)} className='w-28' />
          {nekobt ? (
            <>
              <LanguagesField label='Subtitles' languages={languages} value={(draft.filters as NekobtFilters).subtitles} onChange={(value) => setFilter('subtitles', value)} />
              <LanguagesField label='Audio' languages={languages} value={(draft.filters as NekobtFilters).audio} onChange={(value) => setFilter('audio', value)} />
            </>
          ) : (
            <SimpleSelect label='Nyaa category' items={CATEGORY_ITEMS} value={(draft.filters as NyaaFilters).category} onChange={(value) => setFilter('category', value)} className='w-40' />
          )}
        </div>
        <div className='flex flex-wrap gap-2'>
          <PublisherField publishers={publishers} value={draft.filters.publisher} onChange={(value) => setFilter('publisher', value)} />
          {nekobt && (
            <SimpleSelect label='Batches' items={BATCH_ITEMS} value={(draft.filters as NekobtFilters).batch} onChange={(value) => setFilter('batch', value)} className='w-36' />
          )}
          <Input
            type='number'
            min={0}
            inputMode='numeric'
            value={draft.filters.minSeeders}
            onChange={(event) => setFilter('minSeeders', event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && applySearch()}
            placeholder='Min seeders'
            aria-label='Minimum seeders'
            className='w-28'
          />
          <Input
            value={draft.filters.titleFilters}
            onChange={(event) => setFilter('titleFilters', event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && applySearch()}
            placeholder='Title contains — GER, German, Dual Audio'
            aria-label='Title contains, any of these, comma separated'
            className='min-w-40 flex-1'
          />
          <Input
            value={draft.filters.descriptionFilters}
            onChange={(event) => setFilter('descriptionFilters', event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && applySearch()}
            placeholder='Description contains — Deutsch, German subtitles'
            aria-label='Description contains, any of these, comma separated'
            className='min-w-40 flex-1'
          />
          <Button onClick={applySearch} disabled={loading}>
            {loading ? <Spinner label={`Searching ${SOURCE_LABEL[source]}`} /> : <Search data-icon='inline-start' />} Search
          </Button>
        </div>
        {(result?.sent?.length || aliases.length > 0) && (
          <div className='flex flex-col gap-0.5 text-xs text-muted-foreground'>
            {aliases.length > 0 && (
              <div className='flex flex-wrap items-center gap-1.5'>
                <span>Also searched:</span>
                {aliases.map((alias) => <Tag key={alias} variant='muted'>{alias}</Tag>)}
              </div>
            )}
            {result?.sent?.map((url) => (
              <a key={url} href={url} target='_blank' rel='noreferrer' className='truncate font-mono hover:text-foreground' title={url}>
                {decodeURIComponent(url.replace(/^https:\/\//, ''))}
              </a>
            ))}
          </div>
        )}
      </div>

      <div className='flex min-h-0 flex-1 flex-col'>
        {loading && result && (
          <div className='flex shrink-0 items-center gap-2 border-b border-border/60 px-1 py-2 text-sm text-foreground'>
            <Spinner label='Loading' /> Loading…
          </div>
        )}
        {loading && !result ? (
          <div className='flex flex-col gap-2' aria-label='Loading releases'>
            {Array.from({ length: 8 }).map((_, index) => <Skeleton key={index} className='h-14 w-full' />)}
          </div>
        ) : rows.length ? (
          <Table variant='plain' className='[&_td]:px-2 [&_th]:px-2' containerProps={{ className: 'min-h-0 flex-1 overflow-auto' }}>
            <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
              <TableRow>
                <TableHead className='w-full'>Release</TableHead>
                <TableHead>Publisher</TableHead>
                <SortHeader label='Quality' column='quality' sort={sort} onSort={toggleSort} />
                {nekobt && <TableHead>Codec</TableHead>}
                <SortHeader label='Seeders' column='seeders' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Leechers' column='leechers' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Size' column='size' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Uploaded' column='uploaded' sort={sort} onSort={toggleSort} />
                <TableHead className='text-right'>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((entry) => {
                const match = entry.anilist;
                const name = match ? match.name || match.title : null;
                return (
                  <TableRow
                    key={entry.info_hash || String(entry.id)}
                    className='data-row cursor-pointer'
                    tabIndex={0}
                    aria-label={`${entry.title}: open its description`}
                    onClick={() => setDescribed(entry)}
                    onKeyDown={(event) => event.key === 'Enter' && event.target === event.currentTarget && setDescribed(entry)}
                  >
                    <TableCell className='max-w-0'>
                      <div className='flex items-center gap-2.5'>
                        <AnimePoster url={match?.cover} title={name ?? entry.title} className='w-9 shrink-0 rounded' />
                        <div className='min-w-0'>
                          {name && (
                            <p className='truncate text-foreground' title={name}>
                              <span className='font-medium'>{name}</span>
                              {match?.year ? <span className='ml-1.5 font-mono text-xs tabular-nums text-muted-foreground'>{match.year}</span> : null}
                            </p>
                          )}
                          <p className={cn('truncate font-mono text-xs', name ? 'text-muted-foreground' : 'text-foreground')} title={entry.title}>{entry.title}</p>
                          <LibraryTags match={match} />
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className='max-w-36 truncate text-xs text-foreground' title={entry.publisher ?? undefined}>{entry.publisher ?? '—'}</TableCell>
                    <TableCell className='whitespace-nowrap text-xs'>
                      <span className='text-foreground'>{entry.quality ?? '—'}</span>
                      {entry.video_type && <span className='block text-[0.68rem] text-muted-foreground'>{entry.video_type}</span>}
                    </TableCell>
                    {nekobt && <TableCell className='whitespace-nowrap text-xs text-foreground'>{entry.codec ?? '—'}</TableCell>}
                    <TableCell numeric className='text-xs text-success'>{entry.seeders}</TableCell>
                    <TableCell numeric className='text-xs text-muted-foreground'>{entry.leechers}</TableCell>
                    <TableCell numeric className='whitespace-nowrap text-xs text-foreground'>{entry.size}</TableCell>
                    <TableCell className='whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground' title={entry.published_at ?? undefined}>{formatUploaded(entry)}</TableCell>
                    <TableCell>
                      {/* The row opens the description; its buttons do only what they say. */}
                      <div className='flex justify-end gap-0.5' onClick={(event) => event.stopPropagation()}>
                        <RowAction label={`Open on ${SOURCE_LABEL[entry.source ?? 'nyaa']}`} href={entry.detail_url}><ExternalLink /></RowAction>
                        <RowAction label='Download' onClick={() => openDownload(entry)}><Download /></RowAction>
                        <RowAction label='Copy magnet link' onClick={() => void copyMagnet(entry)}><Magnet /></RowAction>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : (
          <Empty>
            <EmptyMedia><Search /></EmptyMedia>
            <EmptyTitle>No matching releases on {SOURCE_LABEL[source]}</EmptyTitle>
            <EmptyDescription>Try fewer filters or another title alias.</EmptyDescription>
          </Empty>
        )}
        <div className='flex shrink-0 items-center justify-between border-t border-border/60 pt-2'>
          <Button variant='secondary' size='sm' onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0 || loading}>
            <ArrowLeft data-icon='inline-start' /> Previous
          </Button>
          <span className='text-sm text-foreground'>
            Page {page + 1} · {rows.length} {rows.length === 1 ? 'release' : 'releases'}
          </span>
          <Button variant='secondary' size='sm' onClick={() => setPage((value) => value + 1)} disabled={!result?.has_next || loading}>
            Next <ArrowRight data-icon='inline-end' />
          </Button>
        </div>
      </div>

      <DescriptionDrawer entry={described} onClose={() => setDescribed(null)} />

      <Dialog open={selectedEntry !== null && pickFor === null} onOpenChange={(open) => !open && setSelectedEntry(null)}>
        <DialogContent size='lg' className='max-h-[90vh]'>
          <DialogHeader>
            <DialogTitle>Download as this AniList entry</DialogTitle>
            <DialogDescription>
              The selected torrent is downloaded directly. A show&apos;s episodes are filed under its AniList entry, numbered as AniList numbers them; a movie is copied as one file. Audio sync and remuxing are skipped.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className='flex flex-col gap-4'>
          {selectedEntry && <p className='break-words rounded-item border border-field-edge bg-field-surface p-3 font-mono text-sm text-foreground'>{selectedEntry.title}</p>}

          {chosen ? (
            <div className='flex gap-3 rounded-button border border-field-edge bg-field-surface p-3'>
              <Poster url={chosen.cover} title={chosen.name} movie={kind === 'movie'} />
              <div className='flex min-w-0 flex-1 flex-col gap-1'>
                <span className='font-medium text-foreground'>{chosen.name}</span>
                {chosen.romaji && chosen.romaji !== chosen.name && <span className='text-sm text-muted-foreground'>{chosen.romaji}</span>}
                <span className='font-mono text-xs text-muted-foreground'>
                  {[formatLabel(chosen.format), chosen.year, 'id ' + chosen.anilist_id].filter(Boolean).join(' · ')}
                </span>
                <div className='mt-auto flex flex-wrap items-center gap-2 pt-2'>
                  <Button variant='secondary' size='sm' nativeButton={false} render={<a href={anilistUrl(chosen.anilist_id)} target='_blank' rel='noreferrer' />}>
                    <ExternalLink data-icon='inline-start' /> AniList
                  </Button>
                  <Button variant='secondary' size='sm' onClick={() => setPickFor(chosen.romaji || chosen.name)}>
                    <Link2 data-icon='inline-start' /> Change AniList entry
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <Empty className='py-10 md:py-10'>
              <EmptyMedia><Search /></EmptyMedia>
              <EmptyTitle>No AniList entry chosen</EmptyTitle>
              <EmptyDescription>Pick the AniList entry this release belongs to.</EmptyDescription>
            </Empty>
          )}

          {chosen && (
            <div className='grid gap-3 sm:grid-cols-2'>
              <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                Download as
                <Select items={KIND_ITEMS} value={kind} onValueChange={(value) => value !== null && setKind(value as 'show' | 'movie')}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value='show'>Show episodes</SelectItem>
                      <SelectItem value='movie'>Movie</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </label>
              {kind === 'show' && (
                <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                  AniList episode
                  <Input
                    type='number'
                    min='1'
                    value={manualEpisode}
                    onChange={(event) => setManualEpisode(event.target.value)}
                    placeholder='From each file name'
                  />
                </label>
              )}
              {kind === 'show' && (
                <p className='text-xs text-muted-foreground sm:col-span-2'>
                  Leave the episode empty for a batch: each file is numbered by its own name. A set episode applies only to a torrent with one video file.
                </p>
              )}
              {ownedNotice() && (
                <p className='rounded-item border border-success/30 bg-success/10 p-3 text-xs text-foreground sm:col-span-2'>{ownedNotice()}</p>
              )}
            </div>
          )}
          </DialogBody>

          <DialogFooter>
            <Button variant='secondary' onClick={() => setSelectedEntry(null)}>Cancel</Button>
            {!chosen && (
              <Button variant='secondary' onClick={() => selectedEntry && setPickFor(selectedEntry.title)}>
                <Search data-icon='inline-start' /> Find on AniList
              </Button>
            )}
            <Button onClick={() => void downloadSelected()} disabled={!chosen || downloading}>
              {downloading ? <Spinner label='Downloading' /> : <Download data-icon='inline-start' />}
              Download {kind === 'movie' ? 'movie' : 'show'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AniListPickDialog
        query={pickFor}
        onClose={() => setPickFor(null)}
        onPick={pickAnime}
        title='Choose the AniList entry'
        description='The download is filed under the entry you pick, and later releases of this name follow it.'
        action='Choose'
      />
    </div>
  );
}
