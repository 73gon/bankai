"""Movies and shows straight from a torrent, as they are: no German dub, no sync, no review.

The pipeline fuses a German dub with an English picture. Sometimes the release
itself is what is wanted -- Modern Family in English only -- so this searches
Prowlarr, and the download is filed as it is into the library folder picked for
it (Movies or Shows, German or English, each root the Server page lists). The
anime folders are left out: anime is filed by AniList through the Anime page,
and a file the anime index does not know would sit outside its layout.

A movie becomes ``<root>/<Title (Year)>/<Title (Year)>.mkv``, a show's episodes
``<root>/<Show>/Season NN/<Show> - SxxEyy.mkv`` -- in the show's folder when the
root has one already (``Mr. Bean``, matched without its year), flat when that
folder keeps its episodes flat. A file already there is never replaced.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.processor.naming import sanitise
from bankai.web import library_walk

log = get_logger(__name__)

# Rows of recent searches by id, with their links: Prowlarr's download links
# carry its API key, so the page only ever sees the id and sends it back.
_FOUND: dict[str, dict[str, Any]] = {}
_FOUND_LIMIT = 2000

# Prowlarr's (Newznab) categories: Movies and TV with their subcategories.
CATEGORIES = {
    "movie": [2000, 2010, 2020, 2030, 2040, 2045, 2050],
    "show": [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070],
}
QUALITIES = ("2160p", "1080p", "720p", "480p")
LIBRARIES = ("movies", "shows")

_RES = re.compile(r"\b(2160p|1440p|1080p|720p|576p|480p)\b", re.I)
_UHD = re.compile(r"\b(4k|uhd)\b", re.I)
_VIDEO_EXT = re.compile(r"\.(mkv|mp4|avi|m4v|mov|ts)$", re.I)
_YEAR = re.compile(r"(?<![0-9])(19[2-9][0-9]|20[0-9][0-9])(?![0-9])")
_SEASON = re.compile(r"\bS(?P<season>\d{1,2})(?:[ ._-]?E\d{1,4})?\b|\bSeason[ ._-]?(?P<word>\d{1,2})\b", re.I)
# Where a release name stops being its title: quality, source, codec, edition words.
_TAIL = re.compile(
    r"\b(2160p|1440p|1080p|720p|576p|480p|4k|uhd|complete|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|"
    r"hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|avc|hdr|dv|repack|proper|extended|unrated|"
    r"german|multi|dual|ac3|dts|aac|imax)\b",
    re.I,
)
# One file's season and episode(s): S01E05, S01E05E06, S01E05-E06, 1x05.
_FILE_EPISODE = re.compile(
    r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})[ ._-]?E(?P<episode>\d{1,4})(?:-?E(?P<last>\d{1,4}))?(?![0-9])"
    r"|(?<![0-9])(?P<season2>\d{1,2})x(?P<episode2>\d{1,3})(?![0-9])",
    re.I,
)
_LANG_WORDS = {
    "german": "German", "ger": "German", "deutsch": "German",
    "multi": "Multi", "dual": "Dual audio",
    "french": "French", "vff": "French", "truefrench": "French", "ita": "Italian", "italian": "Italian",
    "spanish": "Spanish", "esp": "Spanish", "castellano": "Spanish", "latino": "Spanish",
    "hindi": "Hindi", "rus": "Russian", "russian": "Russian", "kor": "Korean", "jap": "Japanese",
}


# -- reading release names ----------------------------------------------------


def resolution(title: str) -> str | None:
    found = _RES.search(title)
    if found:
        return found.group(1).lower()
    return "2160p" if _UHD.search(title) else None


def languages(title: str) -> list[str]:
    """Languages a release name says it carries besides English."""
    words = re.findall(r"[a-z]+", title.casefold())
    return list(dict.fromkeys(_LANG_WORDS[word] for word in words if word in _LANG_WORDS))


def guess(release: str) -> dict[str, Any]:
    """Title, year and season told from a release name, to prefill the download dialog.

    ``Modern.Family.S01.1080p.BluRay.x264-DEMAND`` -> Modern Family, season 1, a show;
    ``The.Matrix.1999.2160p.UHD.BluRay`` -> The Matrix (1999), a movie.
    """
    name = _VIDEO_EXT.sub("", release.strip())
    name = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", name)
    spaced = re.sub(r"[._]+", " ", name)
    cut = len(spaced)
    season_match = _SEASON.search(spaced)
    season = None
    if season_match:
        season = int(season_match.group("season") or season_match.group("word"))
        cut = min(cut, season_match.start())
    year = None
    for found in _YEAR.finditer(spaced):
        # A year that opens the name is the title (2012, 1917).
        if found.start() == 0 or found.start() > cut:
            continue
        year = int(found.group(1))
        cut = min(cut, found.start())
        break
    tail = _TAIL.search(spaced)
    if tail and tail.start() > 0:
        cut = min(cut, tail.start())
    title = spaced[:cut]
    title = re.sub(r"[\[(][^\])]*$", "", title)
    title = re.sub(r"[\s\-(\[]+$", "", title).strip()
    return {"title": title or spaced.strip(), "year": year, "season": season, "kind": "show" if season else "movie"}


def file_episode(name: str) -> tuple[int, int, int | None] | None:
    """(season, episode, last episode of a double) a file's name says, else None."""
    found = _FILE_EPISODE.search(Path(name).stem)
    if not found:
        return None
    if found.group("season") is not None:
        first = int(found.group("episode"))
        last = int(found.group("last")) if found.group("last") else None
        return int(found.group("season")), first, last if last and last > first else None
    return int(found.group("season2")), int(found.group("episode2")), None


# -- searching ----------------------------------------------------------------


def split_terms(raw: str | None) -> list[str]:
    return [term.strip().casefold() for term in (raw or "").split(",") if term.strip()]


def matches(row: dict[str, Any], *, quality: str | None, include: list[str], exclude: list[str], min_seeders: int) -> bool:
    title = row["title"].casefold()
    if quality and quality != "all" and row["resolution"] != quality:
        return False
    if row["seeders"] < min_seeders:
        return False
    if include and not any(term in title for term in include):
        return False
    return not any(term in title for term in exclude)


def _row(candidate: Any) -> dict[str, Any]:
    ident = candidate.info_hash or hashlib.sha1(
        (candidate.download_url or candidate.magnet_uri or candidate.title).encode()
    ).hexdigest()
    return {
        "id": ident,
        "title": candidate.title,
        "indexer": candidate.indexer,
        "size_bytes": candidate.size_bytes,
        "seeders": candidate.seeders,
        "leechers": candidate.leechers,
        "published": candidate.publish_date,
        "resolution": resolution(candidate.title),
        "codec": candidate.codec,
        "source": candidate.source,
        "languages": languages(candidate.title),
        "info_url": candidate.info_url,
        "download_url": candidate.download_url or None,
        "magnet_uri": candidate.magnet_uri,
        "info_hash": candidate.info_hash,
        "guess": guess(candidate.title),
    }


async def search(
    query: str,
    *,
    kind: str,
    quality: str | None = None,
    title_filters: str | None = None,
    exclude_filters: str | None = None,
    min_seeders: int = 0,
) -> dict[str, Any]:
    """Prowlarr's releases for a query in Movies or TV, filtered, the most seeded first."""
    from bankai.torrent.prowlarr import ProwlarrClient

    if kind not in CATEGORIES:
        raise ValueError("kind must be movie or show")
    client = ProwlarrClient()
    try:
        try:
            candidates = await client.search(query, categories=CATEGORIES[kind], limit=100)
        except Exception as exc:
            raise RuntimeError(f"Prowlarr at {get_settings().prowlarr.url} could not be searched: {exc}") from exc
        problem = await client.indexer_unavailable_reason() if not candidates else None
    finally:
        await client.aclose()
    include, exclude = split_terms(title_filters), split_terms(exclude_filters)
    # Indexers match loosely: "Modern Family S02" brings season 8 too.
    wanted_season = guess(query)["season"] if kind == "show" else None
    rows: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        row = _row(candidate)
        rows.setdefault(row["id"], row)
    found = len(rows)
    items = [
        row for row in rows.values()
        if matches(row, quality=quality, include=include, exclude=exclude, min_seeders=min_seeders)
        and (wanted_season is None or row["guess"]["season"] in {None, wanted_season})
    ]
    items.sort(key=lambda row: (row["seeders"], row["size_bytes"]), reverse=True)
    for row in items:
        _FOUND.pop(row["id"], None)
        _FOUND[row["id"]] = row
    while len(_FOUND) > _FOUND_LIMIT:
        _FOUND.pop(next(iter(_FOUND)))
    shown = [{key: value for key, value in row.items() if key not in {"download_url", "magnet_uri"}} for row in items]
    return {"query": query, "kind": kind, "found": found, "items": shown, "problem": problem}


def found(ident: str) -> dict[str, Any] | None:
    """A row of a recent search, links and all."""
    return _FOUND.get(ident)


# -- where a download goes ----------------------------------------------------


def _language(root: Path) -> str:
    name = root.name.casefold()
    return "English" if name.endswith(("_en", "-en", " en", "english")) else "German"


def targets() -> list[dict[str, Any]]:
    """The folders a download can go to: every Movies and Shows root, named by library and language."""
    from bankai.web.track_cleanup import roots

    rows = []
    for library in LIBRARIES:
        for raw in roots(library):
            root = Path(raw)
            drive = root.parts[2].upper() + ":" if len(root.parts) > 2 and root.parts[1] == "mnt" else ""
            rows.append({
                "root": str(root),
                "library": library,
                "language": _language(root),
                "drive": drive,
                "label": f"{'Movies' if library == 'movies' else 'Shows'} · {_language(root)}",
                "exists": root.is_dir(),
            })
    labels = [row["label"] for row in rows]
    for row in rows:
        if labels.count(row["label"]) > 1 and row["drive"]:
            row["label"] += f" ({row['drive']})"
    return rows


def target(root: str, kind: str) -> dict[str, Any]:
    """The target named by its root, which must take this kind; raises ValueError otherwise."""
    library = "movies" if kind == "movie" else "shows"
    for row in targets():
        if row["root"] == str(Path(root)):
            if row["library"] != library:
                raise ValueError(f"{row['label']} is not a folder for {'movies' if kind == 'movie' else 'shows'}")
            if not row["exists"]:
                raise ValueError(f"{row['root']} cannot be reached")
            return row
    raise ValueError("that folder is not one of the library's roots")


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def folder_name(root: Path, title: str, year: int | None) -> str:
    """The title's folder in this root: one there already, by its name with or without the year, else a new one."""
    wanted = sanitise(f"{title} ({year})" if year else title)
    keys = {_key(wanted), _key(sanitise(title))}
    try:
        names = sorted(entry.name for entry in root.iterdir() if entry.is_dir())
    except OSError:
        names = []
    exact = [name for name in names if _key(name) == _key(wanted)]
    if exact:
        return exact[0]
    loose = [name for name in names if _key(re.sub(r"\s*\((19|20)\d\d\)\s*$", "", name)) in keys]
    return loose[0] if loose else wanted


def _flat(show: Path) -> bool:
    """Does this show's folder keep its episodes beside each other, with no season folders?"""
    try:
        entries = list(show.iterdir())
    except OSError:
        return False
    if any(entry.is_dir() and re.match(r"(season|staffel)\s*\d+|specials", entry.name, re.I) for entry in entries):
        return False
    return any(entry.is_file() and _VIDEO_EXT.search(entry.name) and file_episode(entry.name) for entry in entries)


def placements(
    sources: list[Path],
    *,
    kind: str,
    root: Path,
    title: str,
    year: int | None,
    season: int | None = None,
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str]]]:
    """Where each downloaded video goes, and the ones that go nowhere with why.

    A movie is its largest file (``sources`` holds just that one). A show's file
    goes by the season and episode its name says; a file naming only an episode
    ("E05") takes the release's season.
    """
    output = get_settings().output
    title = title.strip()
    folder = folder_name(root, title, year)
    if kind == "movie":
        fields = {"title": sanitise(title), "year": year or "unknown"}
        try:
            stem = sanitise(output.filename_template.format(**fields))
        except (KeyError, IndexError, ValueError):
            stem = sanitise(f"{title} ({year})" if year else title)
        stem = _VIDEO_EXT.sub("", stem)
        return [(source, root / folder / f"{stem}{source.suffix.casefold()}") for source in sources[:1]], []
    show = root / folder
    flat = _flat(show)
    show_title = re.sub(r"\s*\((19|20)\d\d\)\s*$", "", folder)
    placed: list[tuple[Path, Path]] = []
    left: list[tuple[Path, str]] = []
    taken: set[Path] = set()
    for source in sources:
        if re.search(r"\bsample\b", source.stem, re.I):
            left.append((source, "a sample"))
            continue
        found = file_episode(source.name)
        if found is None and season is not None:
            alone = re.search(r"(?<![A-Za-z0-9])(?:E|EP|Episode[ ._-]?)(\d{1,3})(?![0-9])", source.stem, re.I)
            found = (season, int(alone.group(1)), None) if alone else None
        if found is None:
            left.append((source, "its name says no episode"))
            continue
        number, first, last = found
        code = f"S{number:02d}E{first:02d}" + (f"-E{last:02d}" if last else "")
        name = sanitise(f"{show_title} - {code}") + source.suffix.casefold()
        if flat:
            destination = show / name
        else:
            try:
                season_folder = sanitise(output.season_folder_template.format(season=number))
            except (KeyError, IndexError, ValueError):
                season_folder = f"Season {number:02d}"
            destination = show / season_folder / name
        if destination in taken:
            left.append((source, f"a second file for {code}"))
            continue
        taken.add(destination)
        placed.append((source, destination))
    return placed, left


# -- by language: German into the German folder, English-only into the English one --


def default_roots(kind: str) -> dict[str, str]:
    """The first reachable root of each language for this kind: {"German": ..., "English": ...}."""
    library = "movies" if kind == "movie" else "shows"
    chosen: dict[str, str] = {}
    for row in targets():
        if row["library"] == library and row["exists"]:
            chosen.setdefault(row["language"], row["root"])
    return chosen


def existing_folder(root: Path, title: str, year: int | None, names: tuple[str, ...] = ()) -> Path | None:
    """The title's folder in this root if there is one, by its name or another of its names."""
    for name in (title, *names):
        if not name:
            continue
        folder = root / folder_name(root, name, year)
        if folder.is_dir():
            return folder
    return None


def episode_files(show: Path) -> dict[tuple[int, int], list[Path]]:
    """Every video under a show's folder by (season, episode); a double episode under both numbers."""
    found: dict[tuple[int, int], list[Path]] = {}
    try:
        files = [path for path in show.rglob("*") if path.is_file() and _VIDEO_EXT.search(path.name)]
    except OSError:
        return found
    for path in files:
        if re.search(r"\bsample\b", path.stem, re.I) or path.name.startswith("."):
            continue
        numbers = file_episode(path.name)
        if numbers is None:
            continue
        season, first, last = numbers
        for episode in range(first, (last or first) + 1):
            found.setdefault((season, episode), []).append(path)
    return found


def audio_languages(path: Path, *, probe: bool = True) -> list[str] | None:
    """The audio tracks ("lang|name") of a file, from the track inventory or ffprobe; None when unknown."""
    from bankai.web import track_cleanup

    entry = track_cleanup.load_inventory().get(str(path))
    tracks = entry.get("tracks") if entry and not entry.get("unreadable") else None
    if tracks is None and probe:
        tracks = track_cleanup.probe(path)
    if tracks is None:
        return None
    return [str(track.get("lang") or "") + "|" + str(track.get("name") or "") for track in tracks if track.get("kind") == "audio"]


# Picture classes, lowest first, as the library pages name them (mas_library.quality).
_QUALITY_RANK = {"SD": 0, "720p": 1, "1080p": 2, "2160p": 3}


def picture_rank(path: Path, *, probe: bool = True) -> int | None:
    """How good a video's picture is (SD 0 .. 2160p 3), from the track inventory or ffprobe; None when unknown."""
    import json
    import subprocess

    from bankai.web import mas_library, track_cleanup

    entry = track_cleanup.load_inventory().get(str(path)) or {}
    video = entry.get("video") if not entry.get("unreadable") else None
    if not video and probe:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=True,
            ).stdout
            streams = json.loads(out).get("streams") or []
            video = streams[0] if streams else None
        except (OSError, subprocess.SubprocessError, ValueError):
            video = None
    found = mas_library.quality(video) if video else None
    return _QUALITY_RANK.get(found) if found else None


# Subtitle files a release may carry beside its videos (the library walk keeps their names).
SUBTITLE_SUFFIXES = library_walk.SUBTITLE_SUFFIXES
_ENGLISH_SUB_WORDS = frozenset({"en", "eng", "english"})


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.casefold()))


def _listed(directory: Path) -> list[str] | None:
    try:
        return [entry.name for entry in os.scandir(directory)]
    except OSError:
        return None


def english_subtitle_file(path: Path, listed: Callable[[Path], list[str] | None] = _listed) -> Path | None:
    """An English subtitle file a release ships for this video: beside it under its name
    (``Show.S01E01.en.srt``, or plain ``.srt``), or in a ``Subs`` folder (``Subs/<video>/2_English.srt``,
    ``Subs/English.srt``). A plain one, naming no language, counts: an English release's subtitles are English.

    ``listed`` gives a directory's names (None: no such directory); the library passes its held
    tree, so its episode bar reads the same files as a followed show's drawer without a disk read."""
    def subtitles(directory: Path) -> list[str]:
        return [name for name in listed(directory) or [] if os.path.splitext(name)[1].casefold() in SUBTITLE_SUFFIXES]

    for name in subtitles(path.parent):
        stem, suffix = os.path.splitext(name)
        if not stem.startswith(path.stem):
            continue
        qualifier = stem[len(path.stem):]
        if not qualifier.strip(".") or (qualifier.startswith(".") and _words(qualifier) & _ENGLISH_SUB_WORDS):
            return path.parent / name
    for folder in (path.parent / "Subs" / path.stem, path.parent / "Subs", path.parent / "subs"):
        found = sorted(
            (name for name in subtitles(folder) if _words(os.path.splitext(name)[0]) & _ENGLISH_SUB_WORDS),
            # Full subtitles before forced ones and SDH.
            key=lambda name: ("forced" in name.casefold(), "sdh" in name.casefold(), name),
        )
        if found:
            return folder / found[0]
    return None


def has_english_subtitles(path: Path, *, probe: bool = True) -> bool | None:
    """Does a video come with English subtitles -- a track in it, or a file the release ships?
    None when its tracks cannot be read and no file says so."""
    from bankai.web import track_cleanup

    if english_subtitle_file(path) is not None:
        return True
    entry = track_cleanup.load_inventory().get(str(path))
    tracks = entry.get("tracks") if entry and not entry.get("unreadable") else None
    if tracks is None and probe:
        tracks = track_cleanup.probe(path)
    if tracks is None:
        return None
    return any(
        str(track.get("lang") or "").casefold() in _ENGLISH_SUB_WORDS or "english" in _words(str(track.get("name") or ""))
        for track in tracks
        if track.get("kind") == "subtitles"
    )


def has_german(path: Path, release_title: str = "", *, probe: bool = True) -> bool:
    """Does this video carry German audio? Its tracks say; the release name only when they cannot be read."""
    from bankai.web.media import _is_german

    found = audio_languages(path, probe=probe)
    if found is None:
        return "German" in languages(release_title)
    return any(_is_german(lang.split("|", 1)[0] or None, lang.split("|", 1)[1] or None) for lang in found)


def held(kind: str, title: str, year: int | None, names: tuple[str, ...] = ()) -> dict[str, dict]:
    """What the library has of a title, per language.

    Shows: ``{"German": {(s, e): [paths]}, "English": {...}}`` -- a file in the English folder
    that carries German counts as German. Movies: the same with the key ``(0, 0)``.
    """
    library = "movies" if kind == "movie" else "shows"
    out: dict[str, dict] = {"German": {}, "English": {}}
    for row in targets():
        if row["library"] != library or not row["exists"]:
            continue
        folder = existing_folder(Path(row["root"]), title, year, names)
        if folder is None:
            continue
        if kind == "movie":
            try:
                videos = [path for path in folder.rglob("*") if path.is_file() and _VIDEO_EXT.search(path.name)]
            except OSError:
                videos = []
            videos = [path for path in videos if not re.search(r"\bsample\b", path.stem, re.I)]
            files = {(0, 0): videos} if videos else {}
        else:
            files = episode_files(folder)
        for key, paths in files.items():
            for path in paths:
                language = row["language"]
                # Read from the track inventory only: a page must not wait on ffprobe over 9p.
                if language == "English" and has_german(path, probe=False):
                    language = "German"
                out[language].setdefault(key, []).append(path)
    return out


def remove_with_sidecars(path: Path, *, stop: Path) -> list[Path]:
    """Delete a video and the files named after it (.nfo, -thumb.jpg, .srt), then folders left empty up to ``stop``."""
    removed: list[Path] = []
    stem = path.stem
    try:
        siblings = list(path.parent.iterdir())
    except OSError:
        siblings = [path]
    for sibling in siblings:
        if sibling.is_file() and (sibling == path or sibling.name.startswith((stem + ".", stem + "-"))):
            sibling.unlink(missing_ok=True)
            removed.append(sibling)
    folder = path.parent
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            break
        folder = folder.parent
    return removed


__all__ = [
    "default_roots", "episode_files", "file_episode", "found", "guess", "has_german", "held",
    "placements", "remove_with_sidecars", "search", "target", "targets",
]
