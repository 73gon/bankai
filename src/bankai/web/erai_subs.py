"""German subtitles Erai-raws uploads after a release, from its members-only directory.

Erai-raws often adds an episode's German subtitle file hours or days after the
release, on its own, into a subtitle directory only members can read. It is
in neither the torrent nor the feed's subtitle flags, so bankai held such
releases for having no German and listed their episodes "without German".

Each episode page links its season's directory ("Subtitles"):

    https://www.erai-raws.info/subs/?dir=Sub/2024/Fall/Blue Lock

which has a folder per episode ("02"), and "HEVC"/"hevc" folders of the same
for the encodes. An episode folder holds a file per language; the German one
ends in ".ger.ass":

    [Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p].ger.ass
    [Erai-raws] Blue Lock vs U-20 Japan - 08 [1080p CR WEBRip HEVC EAC3][MultiSub][DB8A5588].9.ger.ass

Reading them takes the member's own login session -- the feed token does not
unlock them. The session is kept in memory and logged into on first need;
what was found, or not, is kept in ``erai_subs.json`` beside ``erai_site.json``.
Nothing here does anything while the login is not set.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx
from selectolax.parser import HTMLParser

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import erai_site

log = get_logger(__name__)

BASE_URL = erai_site.BASE_URL
SUBS_URL = f"{BASE_URL}/subs/"
_LOGIN_PATH = "/account-login/"
_LOGGED_OUT_TEXT = "log in to see the content"
# Spaced like the feed pages: one member account, one address, a site that
# locks out what asks too fast.
_REQUEST_DELAY_SECONDS = erai_site._PAGE_DELAY_SECONDS
# A release or library episode without a German file is asked again after a
# day: the file turns up hours or days after the release.
_MISS_SECONDS = 24 * 3600
# After a refused login, an hour before the next try: WP Defender locks out
# an address that keeps failing to log in.
_LOGIN_BACKOFF_SECONDS = 3600
# The site not answering at all (a timeout, a 5xx): left alone a while.
_UNREACHABLE_SECONDS = 600
# A directory listing is read again after this long within one process.
_LISTING_SECONDS = 3600
_GERMAN = {"ger", "deu"}
_HEVC = re.compile(r"(?i)(?:\bhevc\b|\bx265\b|\bh[.\s-]?265\b)")
_RESOLUTION = re.compile(r"(?i)(?<!\d)(2160|1080|720|480)p\b")
_EPISODE = re.compile(
    r"^(?P<show>.+?)\s+-\s+(?P<episode>\d{1,4})(?:v\d+)?(?:\s+END)?"
    r"(?:\s*\([^)]*\))*\s*(?:\[[^\]]*\]\s*)*$",
    re.IGNORECASE,
)

# The logged-in session's cookies, for this process only.
_COOKIES = httpx.Cookies()
_LAST_REQUEST = 0.0
_UNREACHABLE_UNTIL = 0.0
# directory -> (read at, sub-folders, files)
_LISTINGS: dict[str, tuple[float, list[str], list[str]]] = {}
_LOCK = asyncio.Lock()
# What the library pass is at, for the dashboard's scheduler card while it runs.
progress: dict[str, Any] = {}


def configured() -> bool:
    anime = get_settings().anime
    return bool(anime.erai_username.strip() and anime.erai_password)


class SubsError(RuntimeError):
    """The subtitle directory could not be read; the message never carries the login."""


class LoginFailed(SubsError):
    """Erai-raws did not take the login."""


# -- the store -----------------------------------------------------------------


def _store() -> Path:
    return erai_site._store().with_name("erai_subs.json")


def _empty() -> dict[str, Any]:
    return {"releases": {}, "dirs": {}, "library": {}, "login": {}}


def load() -> dict[str, Any]:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    return {**_empty(), **(data if isinstance(data, dict) else {})}


def _update(change: Callable[[dict[str, Any]], None]) -> None:
    """Change the store under the release state's lock, which holds across processes."""
    from bankai.web import erai

    with erai._STATE_LOCK:
        data = load()
        change(data)
        path = _store()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)


def reset() -> None:
    """Forget the session and a failed login's pause: the login was changed."""
    global _COOKIES, _UNREACHABLE_UNTIL
    _COOKIES = httpx.Cookies()
    _UNREACHABLE_UNTIL = 0.0
    _LISTINGS.clear()
    if _store().exists():
        _update(lambda data: data.update(login={}))


def _login_paused() -> bool:
    failed = float((load().get("login") or {}).get("failed_at") or 0)
    return time.time() - failed < _LOGIN_BACKOFF_SECONDS


def _ready() -> bool:
    """May the site be asked now?"""
    return (
        configured()
        and not erai_site.blocked()
        and not _login_paused()
        and time.monotonic() >= _UNREACHABLE_UNTIL
    )


# -- release names ------------------------------------------------------------


def parse_release(name: str) -> dict[str, Any] | None:
    """Show, episode as written, resolution, HEVC and checksum of a single-episode release; pure.

    A batch is None: its episodes are files of their own, each looked up by
    its own name once it is in the library.
    """
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", name or "").strip()
    value = re.sub(r"\.(?:mkv|mp4)$", "", value, flags=re.IGNORECASE)
    match = _EPISODE.match(value)
    if not match:
        return None
    resolution = _RESOLUTION.search(value)
    crc = re.findall(r"\[([0-9A-Fa-f]{8})\]", value)
    return {
        "show": match["show"].strip(),
        "episode": match["episode"],
        "resolution": resolution[0].casefold() if resolution else None,
        "hevc": _HEVC.search(value) is not None,
        "crc": crc[-1].casefold() if crc else None,
    }


def _show_key(show: str) -> str:
    return " ".join(show.casefold().split())


def _release_key(name: str, info_hash: str | None) -> str:
    if info_hash:
        return str(info_hash).casefold()
    stem = re.sub(r"\.(?:mkv|mp4)$", "", name or "", flags=re.IGNORECASE)
    return "name:" + " ".join(stem.casefold().split())


def cached(name: str, info_hash: str | None = None) -> str | None:
    """The German file found for this release earlier, without asking the site."""
    if not configured():
        return None
    row = load()["releases"].get(_release_key(name, info_hash)) or {}
    return row.get("url") or None


# -- reading the site ---------------------------------------------------------


def _transport() -> httpx.AsyncBaseTransport | None:
    """None is httpx's own; tests put a mock here."""
    return None


def _logged_in(client: httpx.AsyncClient) -> bool:
    return any(cookie.name.startswith("wordpress_logged_in") for cookie in client.cookies.jar)


def _logged_out(response: httpx.Response) -> bool:
    """A members-only answer given to someone not logged in.

    A file is a 403, a listing an empty page, an episode page says so, and
    some answers are the login page itself.
    """
    if response.status_code in {401, 403}:
        return True
    if response.url.path.rstrip("/") == _LOGIN_PATH.rstrip("/"):
        return True
    text = response.text
    if re.search(r"<body\b[^>]*\blogged-in\b", text[:200_000], re.IGNORECASE):
        return False  # WordPress marks a member's page so
    return _LOGGED_OUT_TEXT in text[:500_000].casefold() or len(text.strip()) < 64


def _guarded(response: httpx.Response) -> bool:
    """The bot protection answering rather than the site."""
    return erai_site._guarded(response) or (
        response.status_code == 200 and "ddos-guard" in response.text[:4096].casefold()
        and "<title>" in response.text[:4096].casefold()
    )


class _Session:
    """One lookup's requests: logged in on first need, logged in again once if the site forgot."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.relogged = False

    async def _get(self, url: str) -> httpx.Response:
        global _LAST_REQUEST
        if erai_site.blocked():
            raise SubsError(erai_site.BLOCKED_MESSAGE)
        wait = _REQUEST_DELAY_SECONDS - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_REQUEST = time.monotonic()
        response = await self.client.get(url)
        if _guarded(response):
            erai_site._block()
            raise SubsError(erai_site.BLOCKED_MESSAGE)
        return response

    async def login(self) -> None:
        global _LAST_REQUEST
        if _login_paused():
            raise LoginFailed(summary().get("error") or "Erai-raws did not take the login")
        anime = get_settings().anime
        page = await self._get(_LOGIN_PATH)  # sets wordpress_test_cookie, sent back below
        fields: dict[str, str] = {}
        for node in HTMLParser(page.text).css("form input"):
            attrs = node.attributes
            if (attrs.get("type") or "").casefold() == "hidden" and attrs.get("name"):
                fields[str(attrs["name"])] = str(attrs.get("value") or "")
        fields.update(
            {
                "log": anime.erai_username.strip(),
                "pwd": anime.erai_password,
                "rememberme": "forever",
                "testcookie": "1",
                "redirect_to": f"{BASE_URL}/",
                "wp-submit": "Log In",
                # A honeypot: a person never fills it in, and a login with it
                # filled in is a bot's.
                "website_homepage": "",
            }
        )
        if not any(cookie.name == "wordpress_test_cookie" for cookie in self.client.cookies.jar):
            self.client.cookies.set("wordpress_test_cookie", "WP%20Cookie%20check", domain=urlsplit(BASE_URL).hostname)
        wait = _REQUEST_DELAY_SECONDS - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_REQUEST = time.monotonic()
        response = await self.client.post(_LOGIN_PATH, data=fields)
        if _guarded(response):
            erai_site._block()
            raise SubsError(erai_site.BLOCKED_MESSAGE)
        if not _logged_in(self.client):
            message = "Erai-raws did not accept the login -- check the username and password in Anime Settings"
            _update(lambda data: data.update(login={"failed_at": time.time(), "error": message}))
            log.warning("%s; asked again in %d min", message, _LOGIN_BACKOFF_SECONDS // 60)
            raise LoginFailed(message)
        if (load().get("login") or {}).get("failed_at"):
            _update(lambda data: data.update(login={}))
        log.info("Logged in to Erai-raws for its subtitle directory")

    async def get(self, url: str) -> httpx.Response:
        """A members-only page, logging in first and once more when it shows bankai logged out."""
        if not _logged_in(self.client):
            await self.login()
        response = await self._get(url)
        if response.status_code != 404 and _logged_out(response) and not self.relogged:
            self.relogged = True
            self.client.cookies.clear()
            await self.login()
            response = await self._get(url)
        if response.status_code != 404 and _logged_out(response):
            raise SubsError("Erai-raws shows bankai as logged out even after logging in")
        return response


@asynccontextmanager
async def _session() -> AsyncIterator[_Session]:
    """The session, its cookies kept for the next one in this process."""
    global _COOKIES
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(60, connect=15),
        follow_redirects=True,
        headers={"User-Agent": get_settings().scraper.user_agent},
        cookies=_COOKIES,
        transport=_transport(),
    ) as client:
        try:
            yield _Session(client)
        finally:
            _COOKIES = httpx.Cookies(client.cookies)


def _unreachable(exc: Exception) -> None:
    global _UNREACHABLE_UNTIL
    if isinstance(exc, LoginFailed) or (isinstance(exc, SubsError) and erai_site.blocked()):
        return  # paused by its own record
    _UNREACHABLE_UNTIL = time.monotonic() + _UNREACHABLE_SECONDS
    reason = str(exc) if isinstance(exc, SubsError) else type(exc).__name__
    log.warning("Erai-raws' subtitle directory could not be read: %s", reason)


# -- pages and listings -------------------------------------------------------


def subtitle_dir(html: str) -> str | None:
    """The season's subtitle directory an episode page links as "Subtitles"; pure."""
    found = None
    for node in HTMLParser(html).css("a"):
        href = str(node.attributes.get("href") or "")
        if "/subs/" not in href or "dir=" not in href:
            continue
        value = (parse_qs(urlsplit(href).query).get("dir") or [""])[0].strip().strip("/")
        if not value:
            continue
        if "sub_ddl_box" in str(node.attributes.get("class") or ""):
            return value
        found = found or value
    return found


def parse_listing(html: str, directory: str) -> tuple[list[str], list[str]]:
    """The sub-folders and files right inside ``directory`` of a subtitle listing; pure.

    Folders link as "?dir=Sub/.../02", files as "Sub/.../02.7z", both
    percent-encoded and relative to /subs/. Links elsewhere -- the parent
    folders, the site's menus -- are left out.
    """
    base = directory.strip("/").casefold()
    folders: list[str] = []
    files: list[str] = []
    for node in HTMLParser(html).css("a"):
        href = str(node.attributes.get("href") or "").strip()
        if not href:
            continue
        if "dir=" in href and ("?" in href):
            value = (parse_qs(urlsplit(href).query).get("dir") or [""])[0].strip().strip("/")
            parent, _, leaf = value.rpartition("/")
            if parent.casefold() == base and leaf and leaf not in folders:
                folders.append(leaf)
            continue
        href = href.removeprefix("./")
        if "/subs/" in href:
            href = href.split("/subs/", 1)[1]
        elif href.startswith(("http:", "https:", "/", "#", "?", "mailto:", "javascript:")):
            continue
        path = unquote(href.split("?", 1)[0]).strip("/")
        parent, _, leaf = path.rpartition("/")
        if parent.casefold() == base and leaf and leaf not in files:
            files.append(leaf)
    return folders, files


def _same_episode(stem: str, episode: str) -> bool:
    found = re.search(r"\s-\s(\d{1,4})(?:v\d+)?(?=\s|\[|\(|$)", stem)
    return found is not None and int(found[1]) == int(episode)


def pick_german(files: list[str], release: dict[str, Any], name: str) -> str | None:
    """The German file of an episode folder that belongs to this release; pure.

    The release's own file first -- its name and checksum, "[...][DB8A5588].9.ger.ass"
    -- then the one named by resolution alone, "... - 02 [1080p].ger.ass".
    Another resolution's file is none of this release's, and neither is
    another encode's: one with another checksum. Only a release whose
    checksum is not known takes the single German file of its resolution.
    """
    own = " ".join(re.sub(r"\.(?:mkv|mp4)$", "", name, flags=re.IGNORECASE).casefold().split())
    found: dict[int, list[str]] = {}
    for file in files:
        parts = file.split(".")
        if len(parts) < 3 or parts[-1].casefold() != "ass" or parts[-2].casefold() not in _GERMAN:
            continue
        # The part before the language, and before Erai's track number if any.
        stem = re.sub(r"\.\d+$", "", ".".join(parts[:-2]))
        if not _same_episode(stem, release["episode"]):
            continue
        folded = " ".join(stem.casefold().split())
        resolution = _RESOLUTION.search(stem)
        if folded == own or (release["crc"] and f"[{release['crc']}]" in folded):
            score = 3
        elif release["resolution"] is not None and not (
            resolution and resolution[0].casefold() == release["resolution"]
        ):
            continue
        elif re.search(r"\s-\s\d{1,4}(?:v\d+)?\s*\[\d{3,4}p\]$", stem, re.IGNORECASE):
            score = 2
        elif release["crc"] is None:
            score = 1
        else:
            continue
        found.setdefault(score, []).append(file)
    for score in (3, 2):
        if found.get(score):
            return found[score][0]
    # Several encodes' files and nothing to tell them apart: none.
    return found[1][0] if len(found.get(1, [])) == 1 else None


def file_url(directory: str, file: str) -> str:
    return SUBS_URL + quote(f"{directory.strip('/')}/{file}", safe="/")


async def _listing(session: _Session, directory: str) -> tuple[list[str], list[str]]:
    kept = _LISTINGS.get(directory)
    if kept is not None and time.monotonic() - kept[0] < _LISTING_SECONDS:
        return kept[1], kept[2]
    response = await session.get(f"/subs/?dir={quote(directory, safe='/')}")
    if response.status_code >= 400:
        raise SubsError(f"Erai-raws answered {response.status_code} for a subtitle listing")
    folders, files = parse_listing(response.text, directory)
    _LISTINGS[directory] = (time.monotonic(), folders, files)
    return folders, files


def _pages(release: dict[str, Any], info_hash: str | None) -> list[str]:
    """Episode pages of this show that Erai-raws' feeds listed: this release's first."""
    key = _show_key(release["show"])
    releases = erai_site.load()["releases"]
    own = releases.get(str(info_hash or "").casefold()) or {}
    ranked: list[tuple[int, str]] = []
    if own.get("page"):
        ranked.append((0, str(own["page"])))
    for row in releases.values():
        page = str(row.get("page") or "")
        parsed = parse_release(str(row.get("name") or "")) if page else None
        if parsed is None or _show_key(parsed["show"]) != key:
            continue
        same = int(parsed["episode"]) == int(release["episode"])
        ranked.append((1 if same else 2 if "/episodes/" in page else 3, page))
    ranked.sort()
    # Only the site's own pages: the session's cookies are for nothing else.
    return [page for _rank, page in dict.fromkeys(ranked) if page.startswith(f"{BASE_URL}/")]


async def _season_dir(session: _Session, release: dict[str, Any], info_hash: str | None) -> str | None:
    key = _show_key(release["show"])
    row = (load().get("dirs") or {}).get(key) or {}
    if row.get("dir") or time.time() - float(row.get("checked_at") or 0) < _MISS_SECONDS:
        return row.get("dir") or None
    pages = _pages(release, info_hash)
    if not pages and erai_site.configured():
        with suppress(Exception):
            await erai_site.fill_show(release["show"])
        pages = _pages(release, info_hash)
    found, used = None, None
    for page in list(dict.fromkeys(pages))[:2]:
        response = await session.get(page)
        if response.status_code >= 400:
            continue
        found, used = subtitle_dir(response.text), page
        if found:
            break
    _update(lambda data: data.setdefault("dirs", {}).update({key: {"dir": found, "page": used, "checked_at": time.time()}}))
    return found


async def _find(session: _Session, release: dict[str, Any], name: str, info_hash: str | None) -> str | None:
    directory = await _season_dir(session, release, info_hash)
    if not directory:
        return None
    folders, _files = await _listing(session, directory)
    # An encode's subtitles are in the season's HEVC folder, spelled either way.
    bases = [f"{directory}/{folder}" for folder in folders if folder.casefold() == "hevc"] if release["hevc"] else [directory]
    for base in bases:
        if base != directory:
            folders, _files = await _listing(session, base)
        episode = next(
            (folder for folder in folders if folder.isdigit() and int(folder) == int(release["episode"])), None
        )
        if episode is None:
            continue
        _folders, files = await _listing(session, f"{base}/{episode}")
        picked = pick_german(files, release, name)
        if picked:
            return file_url(f"{base}/{episode}", picked)
    return None


async def german_sub_file(name: str, info_hash: str | None = None) -> str | None:
    """The URL of the German subtitle file Erai-raws uploaded for this release, if any.

    ``name`` is the release's name (Nyaa's title or the file name); with the
    info hash, Erai-raws' own file name and episode page from its feed are
    used. A miss is remembered for a day, then asked again; a site that
    could not be asked remembers nothing.
    """
    if not configured():
        return None
    key = _release_key(name, info_hash)
    row = load()["releases"].get(key) or {}
    if row.get("url") or time.time() - float(row.get("checked_at") or 0) < _MISS_SECONDS:
        return row.get("url") or None
    site_row = erai_site.lookup(info_hash) if info_hash else None
    full = str((site_row or {}).get("name") or name)
    release = parse_release(full) or parse_release(name)
    if release is None or not _ready():
        return None
    try:
        async with _LOCK, _session() as session:
            url = await _find(session, release, full, info_hash)
    except (SubsError, httpx.HTTPError) as exc:
        _unreachable(exc)
        return None
    _update(
        lambda data: data.setdefault("releases", {}).update(
            {key: {"name": full, "url": url, "checked_at": time.time()}}
        )
    )
    if url:
        log.info("Erai-raws has German subtitles for %s", full)
    return url


async def download(url: str, target: Path) -> bool:
    """Fetch a subtitle file into ``target``, atomically; False when it could not be had."""
    if not configured() or not url.startswith(SUBS_URL):
        return False
    if not _ready():
        return False
    try:
        async with _LOCK, _session() as session:
            response = await session.get(url)
    except (SubsError, httpx.HTTPError) as exc:
        _unreachable(exc)
        return False
    head = response.content[:4096].decode("utf-8", "replace").casefold()
    # An .ass file opens with its [Script Info]; anything else is a page, not the file.
    if response.status_code != 200 or "[script info]" not in head:
        log.warning("Erai-raws did not hand over the subtitle file (%d)", response.status_code)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        tmp.write_bytes(response.content)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def summary() -> dict[str, Any]:
    data = load()
    login = data.get("login") or {}
    releases = data.get("releases") or {}
    failed = float(login.get("failed_at") or 0)
    return {
        "configured": configured(),
        "error": login.get("error") if time.time() - failed < _LOGIN_BACKOFF_SECONDS else None,
        "found": sum(1 for row in releases.values() if row.get("url")),
        "checked": len(releases),
    }


# -- the library pass ---------------------------------------------------------


def sidecar_path(video: Path) -> Path:
    """Where an episode's German subtitles go: "<video stem>.de.ass", as the downloader names them."""
    return video.with_name(f"{video.stem}.de.ass")


def _library_releases(rows: dict[str, list[tuple[int, int]]]) -> dict[tuple[int, int], tuple[str, str | None]]:
    """The release each library episode came from, where bankai knows it: (name, info hash).

    The automation's published releases first, by the episode they were
    filed as; then what the downloader wrote down for a file it staged.
    A batch is none: its name is not one episode's.
    """
    from bankai.web import anime_files, erai, review

    found: dict[tuple[int, int], tuple[str, str | None, float]] = {}
    state = erai.state_for_reading()
    for info_hash, release in (state.get("releases") or {}).items():
        if not isinstance(release, dict) or release.get("status") != "done":
            continue
        title = str(release.get("title") or "")
        head, _, episode = str(release.get("canonical") or "").partition("|")
        aid = head.removeprefix("anilist:")
        if not (aid.isdigit() and episode.isdigit()) or parse_release(title) is None:
            continue
        at = float(release.get("updated_at") or 0)
        key = (int(aid), int(episode))
        if key not in found or at > found[key][2]:
            found[key] = (title, str(info_hash).casefold(), at)
    out = {key: (title, info_hash) for key, (title, info_hash, _at) in found.items()}
    for path, raw in review._load_for_reading().items():
        title = str((raw or {}).get("torrent_source_title") or "")
        rel = anime_files.key((raw or {}).get("path") or path) if title else None
        pairs = rows.get(rel or "") or []
        if len(pairs) == 1 and parse_release(title) is not None:
            out.setdefault(pairs[0], (title, None))
    return out


def _feed_releases(wanted: dict[tuple[int, int], bool]) -> dict[tuple[int, int], tuple[str, str]]:
    """Erai-raws' own release of each episode bankai knows no release for: (name, info hash).

    A file that came in a batch, or whose notes a re-layout left behind, no
    longer says which release it is -- 33 library episodes without German had
    nothing to look up by. Erai-raws' feed index lists every release it put
    out, and any of them of the same episode, encode and resolution names the
    same German file. ``wanted`` is (AniList id, episode) -> whether the file
    is HEVC. Episodes are matched by the number in the release's own name, as
    Erai numbers per AniList entry, settled into a sequel past an entry's end.
    """
    from bankai.metadata import anilist
    from bankai.web import erai

    if not wanted:
        return {}
    index = anilist.cached_index()
    if index is None:
        return {}
    mappings = erai._load_mappings()
    numbers = {episode for _aid, episode in wanted}
    resolved: dict[str, Any] = {}
    best: dict[tuple[int, int], tuple[tuple[bool, bool], str, str]] = {}
    for info_hash, row in (erai_site.load().get("releases") or {}).items():
        name = str((row or {}).get("name") or "")
        parsed = parse_release(name)
        if parsed is None or not parsed["episode"].isdigit():
            continue
        identity = erai._erai_identity_name(name)
        if identity is None:
            continue
        show = identity[0]
        if show not in resolved:
            saved = mappings.get(erai._mapping_key(name)) or {}
            # The user's choice for this Erai name first, as the automation reads it.
            if str(saved.get("anilist_id") or "").isdigit():
                resolved[show] = index.anime.get(int(saved["anilist_id"]))
            if resolved.get(show) is None:
                resolved[show] = anilist.resolve_in(index, show).anime
        entry = resolved[show]
        if entry is None:
            continue
        settled, number = anilist.settle_in(entry, int(parsed["episode"]))
        key = (settled.id, number)
        if key not in wanted or number not in numbers:
            continue
        # The file's own encode first, then 1080p.
        rank = (parsed["hevc"] == wanted[key], parsed["resolution"] == "1080p")
        if key not in best or rank > best[key][0]:
            best[key] = (rank, name, str(info_hash).casefold())
    return {key: (name, info_hash) for key, (_rank, name, info_hash) in best.items()}


async def add_library_subtitles(*, episodes: int = 5) -> dict[str, Any]:
    """Give library episodes without German the German file Erai-raws uploaded later.

    An episode counts when neither its subtitles nor its audio are German --
    the library's "N without German". Its release is looked up in the
    members' directory, a few a pass; a German file found is written beside
    the video as "<stem>.de.ass" and Jellyfin is told to read the episode
    again. A miss is asked again after a day. An episode whose release bankai
    does not know is skipped and counted.
    """
    from bankai.web import anime_files, anime_library, jellyfin

    if not configured():
        return {"skipped": "No Erai-raws login set"}
    if not _ready():
        return {"skipped": summary().get("error") or "Erai-raws is not asked for now", "error": summary().get("error")}
    rows = await asyncio.to_thread(anime_files.load)
    probes = await asyncio.to_thread(anime_library._load_codec_cache)
    releases = await asyncio.to_thread(_library_releases, rows)
    records = load().get("library") or {}
    now = time.time()
    root = anime_files.anime_root()
    due: list[tuple[float, str, Path, tuple[str, str | None]]] = []
    without: list[tuple[str, list[tuple[int, int]], Path, dict]] = []
    for rel, pairs in rows.items():
        path = root.joinpath(*PurePosixPath(rel).parts)
        probe = probes.get(str(path))
        if not probe or anime_library.has_german_audio(probe) or anime_library.has_german_subtitles(probe) is not False:
            continue
        without.append((rel, pairs, path, probe))
    # Episodes bankai kept no release for: Erai-raws' own release of each.
    unknown = {
        pairs[0]: probe.get("codec") == "hevc"
        for _rel, pairs, _path, probe in without
        if len(pairs) == 1 and pairs[0] not in releases
    }
    if unknown:
        from bankai.metadata import anilist

        await anilist.index()  # the title index the feed's show names are read against
        releases = {**await asyncio.to_thread(_feed_releases, unknown), **releases}
    unidentified = 0
    for rel, pairs, path, _probe in without:
        release = releases.get(pairs[0]) if len(pairs) == 1 else None
        if release is None:
            unidentified += 1
            continue
        record = records.get(rel) or {}
        at = float(record.get("checked_at") or 0)
        # One found before whose note in the probe cache was lost: no lookup needed.
        if record.get("url") or now - at >= _MISS_SECONDS:
            due.append((at, rel, path, release))
    due.sort(key=lambda row: row[0])
    checked = added = looked = 0
    done: dict[str, dict[str, Any]] = {}
    try:
        for index, (_at, rel, path, (name, info_hash)) in enumerate(due):
            if looked >= episodes or not _ready():
                break
            progress.update(step=f"German subtitles: {PurePosixPath(rel).stem}", done=index, total=min(len(due), episodes))
            if not await asyncio.to_thread(path.is_file):
                continue
            existing = await asyncio.to_thread(anime_library.german_sidecar, path)
            if existing:
                # Already beside it -- published with the episode, or by an
                # earlier pass -- and only the probe did not know.
                await asyncio.to_thread(anime_library.note_german_sidecar, path, existing)
                done[rel] = {"checked_at": time.time(), "url": (records.get(rel) or {}).get("url"), "release": name}
                checked += 1
                continue
            looked += 1
            url = await german_sub_file(name, info_hash)
            if url is None and not _ready():
                break  # not a miss: the site could not be asked
            checked += 1
            if url and await download(url, sidecar_path(path)):
                await asyncio.to_thread(anime_library.note_german_sidecar, path, ".de.ass")
                await asyncio.to_thread(jellyfin.queue, [path])
                added += 1
                log.info("German subtitles from Erai-raws added to %s", path.name)
            elif url:
                break  # found but not handed over: asked again next pass
            done[rel] = {"checked_at": time.time(), "url": url, "release": name}
    finally:
        progress.clear()
        if done:
            _update(lambda data: data.setdefault("library", {}).update(done))
    error = summary().get("error")
    return {
        "checked": checked,
        "added": added,
        "remaining": max(0, len(due) - len(done)),
        "unidentified": unidentified,
        **({"error": error} if error else {}),
    }


__all__ = [
    "LoginFailed",
    "SubsError",
    "add_library_subtitles",
    "cached",
    "configured",
    "download",
    "file_url",
    "german_sub_file",
    "parse_listing",
    "parse_release",
    "pick_german",
    "reset",
    "sidecar_path",
    "subtitle_dir",
    "summary",
]
