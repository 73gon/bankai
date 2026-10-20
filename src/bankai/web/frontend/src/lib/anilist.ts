// Small helpers for showing AniList entries.

/** An entry's page on AniList. */
export function anilistUrl(id: number | string): string {
  return 'https://anilist.co/anime/' + id;
}

const FORMATS: Record<string, string> = {
  TV: 'TV',
  TV_SHORT: 'TV short',
  MOVIE: 'Movie',
  OVA: 'OVA',
  ONA: 'ONA',
  SPECIAL: 'Special',
  MUSIC: 'Music',
};

/** AniList's format (TV, TV_SHORT, MOVIE, ...) as a label; null when unknown. */
export function formatLabel(format: string | null | undefined): string | null {
  if (!format) return null;
  return FORMATS[format.toUpperCase()] ?? format;
}

/** How an entry is shown: English, else romaji, else its id. */
export function anilistName(anime: {
  anilist_id: number;
  name?: string | null;
  english?: string | null;
  romaji?: string | null;
}): string {
  return anime.name || anime.english || anime.romaji || 'AniList ' + anime.anilist_id;
}
