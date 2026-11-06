import { api } from '@/lib/api';

// AniList's cover sizes on its CDN, by the width each is: small 100x143, medium
// 230x328, large 460x656 (a large PNG is half a megabyte, decoded whole however
// small it is drawn). TMDB's are named by width.
const ANILIST = /^(https:\/\/s\d+\.anilist\.co\/file\/anilistcdn\/media\/(?:anime|manga)\/cover\/)(?:small|medium|large|extraLarge)(\/.+)$/;
const ANILIST_SIZES: Array<[string, number]> = [['small', 100], ['medium', 230], ['large', 460]];
const TMDB = /^(https:\/\/image\.tmdb\.org\/t\/p\/)(?:w\d+|original)(\/.+)$/;
const TMDB_SIZES = [92, 185, 342, 500];

/**
 * A poster's address and, where its host offers sizes, a `srcSet` of them: with
 * `sizes="auto"` the browser takes the smallest that fills the poster as it is
 * drawn -- a 100px cover for a table's thumbnail, not the 460px one.
 */
export function posterSources(url: string): { src: string; srcSet?: string } {
  const anilist = ANILIST.exec(url);
  if (anilist) {
    const [, head, tail] = anilist;
    return {
      src: `${head}medium${tail}`,
      srcSet: ANILIST_SIZES.map(([name, width]) => `${head}${name}${tail} ${width}w`).join(', '),
    };
  }
  const tmdb = TMDB.exec(url);
  if (tmdb) {
    const [, head, tail] = tmdb;
    return {
      src: `${head}w342${tail}`,
      srcSet: TMDB_SIZES.map((width) => `${head}w${width}${tail} ${width}w`).join(', '),
    };
  }
  return { src: api.posterUrl(url) };
}

/** What every poster <img> gets: sized by where it is drawn, loaded and decoded off the main path. */
export const POSTER_IMG = { loading: 'lazy', decoding: 'async', sizes: 'auto, 240px' } as const;
