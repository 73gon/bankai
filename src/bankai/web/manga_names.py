"""What a manga or light novel release's name says, and what its files are.

nyaa's English literature uploads follow loose habits rather than a scheme:

    Sousou no Frieren | Frieren - Beyond Journey's End v01-14 + 138-147 (2021-2026) (Digital) (1r0n)
    [Oshi No Ko] v13 (2026) (Digital) (ShelfLife)
    【OSHI NO KO】 001-130 as v01-13 (Digital-Compilation) (Oak)
    Mushoku Tensei - Redundant Reincarnation v01-03 [Seven Seas] [Stick]

so ``parse`` reads each part where it usually is: the series' names (one or
more, split on " | " and " / ") before the first volume or chapter marker,
volumes ("v01-14", "Vol.10 - Vol.11", "Volume 02"), chapters ("CH001-140",
"Chapters 141-147", a bare "001-166" -- not one of years -- or the range after
"+" or ","), and the tags in brackets at the end (years, "Digital", the group;
the last of them cut off with the name, too). Whether it is a
light novel is guessed from what it says and, failing that, from its size per
volume (``novel``). ``file_number`` reads one file's volume or chapter, and
``target_name`` names it the way the library names its files: "Vol 01.cbz",
"Chapter 0138.cbz".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"\d+(?:\.\d+)?"
_VOLUME = re.compile(
    rf"(?<![A-Za-z0-9])(?:v|vol\.?|volumes?)\s*({_NUM})"
    rf"(?:\s*-\s*(?:v|vol\.?|volumes?)?\s*({_NUM}))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CHAPTER = re.compile(
    rf"(?<![A-Za-z0-9])(?:ch\.?\s*|chapters?\s*|c(?=\d))({_NUM})"
    rf"(?:\s*-\s*(?:ch\.?\s*|chapters?\s*|c(?=\d))?({_NUM}))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# A bare range of numbers; one of years ("{2021-2023}") is none.
_RANGE = re.compile(
    r"(?<![A-Za-z0-9.])(?!(?:19|20)\d\d\s*-\s*(?:19|20)\d\d(?![0-9.]))"
    r"(\d{1,4}(?:\.\d+)?)\s*-\s*(\d{1,4}(?:\.\d+)?)(?![A-Za-z0-9.])"
)
_TRAILING_TAG = re.compile(r"\s*[\(\[{]([^\(\)\[\]{}]*)[\)\]}]\s*$")
# "... v09-12 (2018-2019) [Viz] [LuCaZ": a name cut off inside its last tag.
_CUT_TAG = re.compile(r"\s*[\(\[{]([^\(\)\[\]{}]*)$")
_LEADING_TAG = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")
_YEARS = re.compile(r"^(\d{4})(?:\s*-\s*(\d{4}))?$")
_ALTERNATIVES = re.compile(r"\s+[|/｜]\s+")
_AUDIOBOOK = re.compile(r"\baudio\s*books?\b", re.IGNORECASE)
_NOVEL = re.compile(r"\b(?:light\s+novels?|novels?|LN|WN|epub|j-novel(?:\s+club)?|yen\s+on)\b", re.IGNORECASE)
_MANGA = re.compile(r"\b(?:digital(?:-compilation)?|mangadex|cbz|manga|webtoon|manhwa)\b", re.IGNORECASE)
_PART = re.compile(r"(?<![A-Za-z])part\s*\d", re.IGNORECASE)
# "Jujutsu Kaisen 191 (2022) (Digital) (1r0n)": a weekly chapter, its number bare after the
# title. Three digits at least, as these are padded, so a title's own number ("Kaiju No. 8")
# is not taken for one.
_BARE_CHAPTER = re.compile(r"[A-Za-z].*\s(\d{3,4}(?:\.\d+)?)$")
_PLAIN_TAGS = {"digital", "digital-compilation", "completed", "complete", "ongoing", "novel", "light novel", "ln", "epub", "cbz"}

# Under this much per volume a release is a light novel (an EPUB is a few MB),
# over the second it is manga (a volume of pictures is tens to hundreds).
_NOVEL_PER_VOLUME = 25 * 2**20
_MANGA_PER_VOLUME = 40 * 2**20

BOOK_EXTENSIONS = {".cbz", ".zip", ".cbr", ".rar", ".pdf", ".epub"}
# What the library keeps a file as: a ZIP of pictures is a CBZ, a RAR a CBR.
_RENAMED_EXTENSION = {".zip": ".cbz", ".rar": ".cbr"}


@dataclass(frozen=True)
class ReleaseName:
    names: tuple[str, ...]
    volumes: tuple[float, float] | None = None
    chapters: tuple[float, float] | None = None
    years: tuple[int, int] | None = None
    group: str | None = None
    # True: the name says light novel; False: it says manga; None: neither.
    says_novel: bool | None = None
    # A tag says it is a (light or web) novel: "(Novel)", "[WN ...]" -- not a title that has the
    # word in it ("The Novel's Extra" is a manhwa).
    tagged_novel: bool = False
    audiobook: bool = False
    complete: bool = False

    def volume_count(self) -> int:
        if not self.volumes:
            return 0
        return max(1, int(self.volumes[1]) - int(self.volumes[0]) + 1)


def _range(first: str, last: str | None) -> tuple[float, float]:
    low, high = float(first), float(last if last is not None else first)
    return (low, high) if low <= high else (high, low)


def _first_marker(text: str) -> tuple[int, str, re.Match[str]] | None:
    found = [
        (match.start(), kind, match)
        for kind, pattern in (("volumes", _VOLUME), ("chapters", _CHAPTER), ("range", _RANGE))
        if (match := pattern.search(text))
    ]
    return min(found, key=lambda row: row[0]) if found else None


def parse(title: str) -> ReleaseName:
    """A release name read into its parts; see the module's docstring."""
    text = " ".join(title.replace("【", " ").replace("】", " ").split())
    audiobook = bool(_AUDIOBOOK.search(text))
    says_novel: bool | None = True if _NOVEL.search(text) else False if _MANGA.search(text) else None
    tags: list[str] = []
    if (cut := _CUT_TAG.search(text)) and text[: cut.start()].strip():
        tags.append(cut[1].strip())
        text = text[: cut.start()].rstrip()
    while match := _TRAILING_TAG.search(text):
        if not text[: match.start()].strip():
            break
        tags.insert(0, match[1].strip())
        text = text[: match.start()].rstrip()
    group = None
    leading = _LEADING_TAG.match(text)
    if leading:
        rest = leading[2]
        marker = _first_marker(rest)
        if not rest or (marker is not None and marker[0] == 0):
            # "[Oshi No Ko] v13": the brackets are the title's own.
            text = f"{leading[1]} {rest}".strip()
        else:
            group, text = leading[1].strip(), rest
    years = None
    volumes = chapters = None
    complete = False
    for tag in tags:
        lowered = tag.casefold()
        if years_match := _YEARS.match(tag):
            years = (int(years_match[1]), int(years_match[2] or years_match[1]))
        elif lowered in {"completed", "complete"}:
            complete = True
        # "Frieren - (Chapters 141-147)": the numbers came in brackets.
        elif volume_tag := _VOLUME.search(tag):
            volumes = _range(volume_tag[1], volume_tag[2])
        elif chapter_tag := _CHAPTER.search(tag) or _RANGE.fullmatch(tag.strip()):
            chapters = _range(chapter_tag[1], chapter_tag[2])
        elif lowered not in _PLAIN_TAGS:
            group = tag
    marker = _first_marker(text)
    if marker is None:
        series = text
    else:
        series = text[: marker[0]]
        rest = text[marker[0] :]
        position, kind, match = marker
        if kind == "volumes":
            volumes = _range(match[1], match[2])
        else:
            chapters = _range(match[1], match[2])
        rest = rest[match.end() - position :]
        # What follows: "+ 138-147", ",098-127", "and Chapters 88-99", "as v01-13".
        if (more := _VOLUME.search(rest)) and volumes is None:
            volumes = _range(more[1], more[2])
        elif (more := _CHAPTER.search(rest) or _RANGE.search(rest)) and chapters is None:
            chapters = _range(more[1], more[2])
    series = series.strip(" -–—:|,~")
    names = tuple(name.strip(" -–—:,~") for name in _ALTERNATIVES.split(series) if name.strip(" -–—:,~"))
    return ReleaseName(
        names=names,
        volumes=volumes,
        chapters=chapters,
        years=years,
        group=group,
        says_novel=says_novel,
        tagged_novel=any(_NOVEL.search(tag) for tag in tags),
        audiobook=audiobook,
        complete=complete,
    )


def novel(release: ReleaseName, size_bytes: int | None) -> bool | None:
    """Whether a release is a light novel: what its name says, else its size per volume."""
    if release.says_novel is not None:
        return release.says_novel
    if release.chapters and not release.volumes:
        return False
    if size_bytes and release.volumes:
        per_volume = size_bytes / release.volume_count()
        if per_volume < _NOVEL_PER_VOLUME:
            return True
        if per_volume > _MANGA_PER_VOLUME:
            return False
    return None


# -- Files ---------------------------------------------------------------------


def _outside_brackets(stem: str) -> str:
    return re.sub(r"[\(\[{][^\(\)\[\]{}]*[\)\]}]", " ", stem)


def file_number(filename: str) -> tuple[str, float] | None:
    """One file's ("volume" | "chapter", number), from its name; None when it has no single one.

    "Frieren v01 (2021) (Digital) (1r0n).cbz" is volume 1, "Frieren - c138
    (2026).cbz" chapter 138, "Vol 01.cbz" and "Chapter 0001.cbz" (the
    library's own names) likewise, and "Jujutsu Kaisen 191 (2022)" chapter 191:
    a bare number of three digits or more ending the name. A number in brackets
    counts only when there is none outside them; a name with a "Part 2" in it is
    left alone, since its volumes start again with each part.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    if _PART.search(stem):
        return None
    for text in (_outside_brackets(stem), stem):
        chapters = list(_CHAPTER.finditer(text))
        volumes = list(_VOLUME.finditer(text))
        if len(chapters) == 1 and not chapters[0][2]:
            return "chapter", float(chapters[0][1])
        if not chapters and len(volumes) == 1 and not volumes[0][2]:
            return "volume", float(volumes[0][1])
        if chapters or volumes:
            return None
    bare = _BARE_CHAPTER.search(_outside_brackets(stem).strip())
    if bare and not 1900 <= float(bare[1]) <= 2100:
        return "chapter", float(bare[1])
    return None


def _number(value: float, width: int) -> str:
    whole = int(value)
    text = f"{whole:0{width}d}"
    return text if value == whole else f"{text}{str(value)[len(str(whole)):]}"


def target_name(kind: str, number: float, extension: str) -> str:
    """The library's name for a volume or chapter: "Vol 01.cbz", "Chapter 0138.cbz"."""
    extension = _RENAMED_EXTENSION.get(extension.lower(), extension.lower())
    if kind == "volume":
        return f"Vol {_number(number, 2)}{extension}"
    return f"Chapter {_number(number, 4)}{extension}"


def library_extension(extension: str) -> str:
    return _RENAMED_EXTENSION.get(extension.lower(), extension.lower())


def describe(volumes: set[float], chapters: set[float]) -> str:
    """What a set of volumes and chapters is, briefly: "Vol 1–12, 14 · Ch 138–147"."""
    parts = []
    for label, numbers in (("Vol", volumes), ("Ch", chapters)):
        if numbers:
            parts.append(f"{label} {_spans(sorted(numbers))}")
    return " · ".join(parts)


def _spans(numbers: list[float]) -> str:
    runs: list[list[float]] = []
    for value in numbers:
        if runs and value == int(value) and runs[-1][-1] == int(runs[-1][-1]) and value - runs[-1][-1] == 1:
            runs[-1].append(value)
        else:
            runs.append([value])

    def show(value: float) -> str:
        return str(int(value)) if value == int(value) else str(value)

    return ", ".join(show(run[0]) if len(run) == 1 else f"{show(run[0])}–{show(run[-1])}" for run in runs)
