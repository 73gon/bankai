"""German subtitles Erai-raws uploads later, from its members-only subtitle directory."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_files, anime_library, erai, erai_site, erai_subs, jellyfin, review

SEASON = "Sub/2024/Fall/Blue Lock"
PAGE = "https://www.erai-raws.info/episodes/blue-lock-vs-u-20-japan-02/"
AVC_HASH = "a" * 40
HEVC_HASH = "b" * 40
AVC_NAME = "[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p][Multiple Subtitle][56827752].mkv"
HEVC_NAME = "[Erai-raws] Blue Lock vs U-20 Japan - 08 [1080p CR WEBRip HEVC EAC3][MultiSub][DB8A5588].mkv"
GERMAN_02 = "[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p].ger.ass"
GERMAN_08 = "[Erai-raws] Blue Lock vs U-20 Japan - 08 [1080p CR WEBRip HEVC EAC3][MultiSub][DB8A5588].9.ger.ass"
ASS = "﻿[Script Info]\nTitle: Deutsch\n\n[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hallo\n"

EPISODE_PAGE = f"""<html><body class="single logged-in">
<a class="sub_ddl_box" href="https://www.erai-raws.info/subs/?dir={SEASON}">Subtitles</a>
<a class="sub_ddl_box" href="https://www.erai-raws.info/nzbs/?dir=NZB/2024/Fall/Blue Lock">NZB</a>
</body></html>"""


def listing(folders: list[str], files: list[str], base: str) -> str:
    """The directory listing as the site renders it: percent-encoded, relative to /subs/."""
    from urllib.parse import quote

    links = ['<a href="?dir=Sub/2024/Fall">Fall</a>']
    links += [f'<a href="?dir={quote(base + "/" + name)}">{name}</a>' for name in folders]
    links += [f'<a href="{quote(base + "/" + name)}">{name} 58.88KB 2024-10-12 19:11:36</a>' for name in files]
    return "<html><body class='logged-in'><table>" + "".join(f"<tr><td>{link}</td></tr>" for link in links) + "</table></body></html>"


LISTINGS = {
    SEASON: listing(["01", "02", "HEVC", "hevc"], ["01.7z", "02.7z"], SEASON),
    f"{SEASON}/02": listing(
        [],
        [
            GERMAN_02,
            "[Erai-raws] Blue Lock vs U-20 Japan - 02 [720p].ger.ass",
            "[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p][Multiple Subtitle][56827752].3.eng.ass",
            "[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p][Multiple Subtitle][56827752].4.por.ass",
        ],
        f"{SEASON}/02",
    ),
    f"{SEASON}/HEVC": listing(["08"], [], f"{SEASON}/HEVC"),
    f"{SEASON}/hevc": listing([], [], f"{SEASON}/hevc"),
    f"{SEASON}/HEVC/08": listing(
        [],
        [
            GERMAN_08,
            "[Erai-raws] Blue Lock vs U-20 Japan - 08 [1080p CR WEBRip HEVC EAC3][MultiSub][DB8A5588].3.eng.ass",
        ],
        f"{SEASON}/HEVC/08",
    ),
}


class Site:
    """erai-raws.info as far as bankai uses it: the WordPress login and the members' pages."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.logins: list[dict[str, list[str]]] = []
        self.login_cookies: list[str] = []
        self.generation = 0
        self.password = "hunter2"

    def expire(self) -> None:
        """The site forgets every session: the next members' page is a logged-out one."""
        self.generation += 1

    def logged_in(self, request: httpx.Request) -> bool:
        return f"wordpress_logged_in_x=session{self.generation}" in request.headers.get("cookie", "")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/account-login/" and request.method == "GET":
            form = (
                '<form method="post"><input type="text" name="log"><input type="password" name="pwd">'
                '<input type="text" name="website_homepage" value="">'
                '<input type="hidden" name="_wpnonce" value="n0nce"></form>'
            )
            return httpx.Response(200, text=form, headers={"set-cookie": "wordpress_test_cookie=WP%20Cookie%20check; path=/"})
        if path == "/account-login/":
            fields = parse_qs(request.content.decode(), keep_blank_values=True)
            self.logins.append(fields)
            self.login_cookies.append(request.headers.get("cookie", ""))
            if fields.get("pwd") == [self.password] and fields.get("website_homepage") == [""]:
                return httpx.Response(
                    302,
                    headers={"location": "/", "set-cookie": f"wordpress_logged_in_x=session{self.generation}; path=/"},
                )
            return httpx.Response(200, text="<html><body>Wrong password. Log in to see the content of the website.</body></html>")
        if path == "/":
            return httpx.Response(200, text="<html><body class='logged-in'>home</body></html>")
        if not self.logged_in(request):
            if path == "/subs/" and "dir" in request.url.params:
                return httpx.Response(200, text=" " * 20)
            if path.startswith("/subs/"):
                return httpx.Response(403, text="Forbidden")
            return httpx.Response(200, text="<html><body>Log in to see the content of the website.</body></html>")
        if path == "/episodes/blue-lock-vs-u-20-japan-02/":
            return httpx.Response(200, text=EPISODE_PAGE)
        if path == "/subs/" and "dir" in request.url.params:
            directory = request.url.params["dir"]
            if directory in LISTINGS:
                return httpx.Response(200, text=LISTINGS[directory])
            return httpx.Response(404, text="")
        if path.startswith("/subs/Sub/") and path.endswith(".ger.ass"):
            return httpx.Response(200, text=ASS)
        return httpx.Response(404, text="")


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A member login set, the state under tmp_path, and the site above in place of the network."""
    monkeypatch.setattr(erai_site, "_store", lambda: tmp_path / "erai_site.json")
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(erai_site, "_LOADED", None)
    monkeypatch.setattr(erai_site, "configured", lambda: False)
    monkeypatch.setattr(erai_subs, "_REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(erai_subs, "_COOKIES", httpx.Cookies())
    monkeypatch.setattr(erai_subs, "_LISTINGS", {})
    monkeypatch.setattr(erai_subs, "_UNREACHABLE_UNTIL", 0.0)
    monkeypatch.setenv("BANKAI_ANIME__ERAI_USERNAME", "member@example.com")
    monkeypatch.setenv("BANKAI_ANIME__ERAI_PASSWORD", "hunter2")
    reset_settings_cache()
    fake = Site()
    monkeypatch.setattr(erai_subs, "_transport", lambda: httpx.MockTransport(fake))
    erai_site._merge(
        [
            {"hash": AVC_HASH, "name": AVC_NAME, "subs": ["us", "br"], "res": "1080p", "page": PAGE},
            {"hash": HEVC_HASH, "name": HEVC_NAME, "subs": ["us"], "res": "1080p",
             "page": "https://www.erai-raws.info/encodes/blue-lock-vs-u-20-japan-08-hevc/"},
        ]
    )
    yield fake
    reset_settings_cache()


def test_the_season_folder_and_episode_folders_are_read_from_a_listing():
    folders, files = erai_subs.parse_listing(LISTINGS[SEASON], SEASON)
    assert folders == ["01", "02", "HEVC", "hevc"]
    assert files == ["01.7z", "02.7z"]
    folders, files = erai_subs.parse_listing(LISTINGS[f"{SEASON}/HEVC/08"], f"{SEASON}/HEVC/08")
    assert folders == [] and files[0] == GERMAN_08
    assert erai_subs.subtitle_dir(EPISODE_PAGE) == SEASON


def test_the_german_file_of_the_release_is_picked():
    files = erai_subs.parse_listing(LISTINGS[f"{SEASON}/02"], f"{SEASON}/02")[1]
    avc = erai_subs.parse_release(AVC_NAME)
    assert avc == {"show": "Blue Lock vs U-20 Japan", "episode": "02", "resolution": "1080p", "hevc": False, "crc": "56827752"}
    assert erai_subs.pick_german(files, avc, AVC_NAME) == GERMAN_02
    sd = erai_subs.parse_release("[Erai-raws] Blue Lock vs U-20 Japan - 02 [720p][Multiple Subtitle]")
    assert erai_subs.pick_german(files, sd, "x") == "[Erai-raws] Blue Lock vs U-20 Japan - 02 [720p].ger.ass"

    hevc_files = erai_subs.parse_listing(LISTINGS[f"{SEASON}/HEVC/08"], f"{SEASON}/HEVC/08")[1]
    hevc = erai_subs.parse_release(HEVC_NAME)
    assert hevc["hevc"] and hevc["crc"] == "db8a5588"
    assert erai_subs.pick_german(hevc_files, hevc, HEVC_NAME) == GERMAN_08
    # Another encode of the episode, by its checksum: not this file.
    other = erai_subs.parse_release(HEVC_NAME.replace("DB8A5588", "0BADF00D"))
    assert erai_subs.pick_german(hevc_files, other, "x") is None
    # Nyaa's title of it, without a checksum: the single German file at its resolution.
    nyaa = erai_subs.parse_release("[Erai-raws] Blue Lock vs U-20 Japan - 08 [1080p CR WEBRip HEVC EAC3][MultiSub][ENG][GER]")
    assert erai_subs.pick_german(hevc_files, nyaa, "x") == GERMAN_08
    # Another episode's file never.
    assert erai_subs.pick_german(hevc_files, erai_subs.parse_release(AVC_NAME), AVC_NAME) is None
    # A batch is no single episode.
    assert erai_subs.parse_release("[Erai-raws] Blue Lock - 01 ~ 14 [1080p]") is None


def test_the_login_sends_the_honeypot_empty_and_its_cookies_are_reused(site):
    url = asyncio.run(erai_subs.german_sub_file("[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p CR WEB-DL AVC AAC]", AVC_HASH))

    assert url == "https://www.erai-raws.info/subs/Sub/2024/Fall/Blue%20Lock/02/%5BErai-raws%5D%20Blue%20Lock%20vs%20U-20%20Japan%20-%2002%20%5B1080p%5D.ger.ass"
    assert len(site.logins) == 1
    fields = site.logins[0]
    assert fields["log"] == ["member@example.com"] and fields["pwd"] == ["hunter2"]
    assert fields["website_homepage"] == [""]
    assert fields["rememberme"] == ["forever"] and fields["testcookie"] == ["1"] and fields["wp-submit"] == ["Log In"]
    assert fields["_wpnonce"] == ["n0nce"]
    # The test cookie WordPress set on the login page went back with the login.
    assert "wordpress_test_cookie=" in site.login_cookies[0]

    # The HEVC encode of another episode, and the file itself: the same session.
    assert asyncio.run(erai_subs.german_sub_file(HEVC_NAME, HEVC_HASH)).endswith(
        "/HEVC/08/" + "%5BErai-raws%5D%20Blue%20Lock%20vs%20U-20%20Japan%20-%2008%20%5B1080p%20CR%20WEBRip%20HEVC%20EAC3%5D%5BMultiSub%5D%5BDB8A5588%5D.9.ger.ass"
    )
    assert len(site.logins) == 1
    # Nothing that was sent or kept carries the password.
    assert "hunter2" not in erai_subs._store().read_text(encoding="utf-8")


def test_a_logged_out_listing_logs_in_once_more(site, tmp_path):
    asyncio.run(erai_subs.german_sub_file(AVC_NAME, AVC_HASH))
    assert len(site.logins) == 1

    site.expire()
    erai_subs._LISTINGS.clear()
    url = asyncio.run(erai_subs.german_sub_file(HEVC_NAME, HEVC_HASH))

    assert url and url.endswith(".9.ger.ass")
    assert len(site.logins) == 2

    # A file asked for logged out is a 403: logged in again, and fetched.
    site.expire()
    target = tmp_path / "Show - S01E02.de.ass"
    assert asyncio.run(erai_subs.download(url, target)) is True
    assert target.read_text(encoding="utf-8").lstrip("﻿").startswith("[Script Info]")
    assert len(site.logins) == 3


def test_a_refused_login_pauses_and_is_reported_without_the_password(site):
    site.password = "something else"

    assert asyncio.run(erai_subs.german_sub_file(AVC_NAME, AVC_HASH)) is None
    summary = erai_subs.summary()
    assert "did not accept the login" in summary["error"]
    assert "hunter2" not in json.dumps(summary)
    # Paused: no second login is tried, and no miss was remembered.
    before = len(site.requests)
    assert asyncio.run(erai_subs.german_sub_file(HEVC_NAME, HEVC_HASH)) is None
    assert len(site.requests) == before
    assert erai_subs.load()["releases"] == {}
    # A new login is tried at once.
    erai_subs.reset()
    site.password = "hunter2"
    assert asyncio.run(erai_subs.german_sub_file(AVC_NAME, AVC_HASH))


def test_a_miss_is_asked_again_after_a_day(site):
    name = "[Erai-raws] Blue Lock vs U-20 Japan - 02 [2160p][Multiple Subtitle]"
    assert asyncio.run(erai_subs.german_sub_file(name, "c" * 40)) is None
    row = erai_subs.load()["releases"]["c" * 40]
    assert row["url"] is None and row["checked_at"] > 0

    before = len(site.requests)
    assert asyncio.run(erai_subs.german_sub_file(name, "c" * 40)) is None
    assert len(site.requests) == before

    erai_subs._update(lambda data: data["releases"]["c" * 40].update(checked_at=time.time() - 2 * 86400))
    erai_subs._LISTINGS.clear()
    asyncio.run(erai_subs.german_sub_file(name, "c" * 40))
    assert len(site.requests) > before


def test_nothing_happens_without_the_login(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(erai_site, "_store", lambda: tmp_path / "erai_site.json")
    monkeypatch.setattr(erai_subs, "_transport", lambda: httpx.MockTransport(lambda request: asked.append(request)))
    monkeypatch.delenv("BANKAI_ANIME__ERAI_USERNAME", raising=False)
    monkeypatch.delenv("BANKAI_ANIME__ERAI_PASSWORD", raising=False)
    reset_settings_cache()

    assert not erai_subs.configured()
    assert asyncio.run(erai_subs.german_sub_file(AVC_NAME, AVC_HASH)) is None
    assert asyncio.run(erai_subs.download(erai_subs.SUBS_URL + "x.ger.ass", tmp_path / "x.de.ass")) is False
    assert erai_subs.cached(AVC_NAME, AVC_HASH) is None
    assert asyncio.run(erai_subs.add_library_subtitles())["skipped"]
    assert asked == []
    assert not (tmp_path / "erai_subs.json").exists()


# -- the library pass -----------------------------------------------------------


@pytest.fixture
def library(site, tmp_path, monkeypatch):
    """An episode in the library without German, filed by the automation from the AVC release."""
    root = tmp_path / "shows_anime"
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(root))
    reset_settings_cache()
    monkeypatch.setattr(anime_library, "_CODEC_CACHE", None)
    monkeypatch.setattr(review, "_store_path", lambda: tmp_path / "review.json")
    queued: list[Path] = []
    monkeypatch.setattr(jellyfin, "queue", lambda paths: queued.extend(paths) or len(paths))
    video = root / "Blue Lock" / "Season 02" / "Blue Lock - S02E02.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    anime_files.record(video, [(500, 2)])
    probes = {str(video): {"size": 5, "mtime": 0, "codec": "avc", "audio": ["jpn"], "subs": ["eng", "por"]}}
    anime_library._save_codec_cache(probes)
    state = erai._default_state()
    state["releases"][AVC_HASH] = {
        "status": "done",
        "title": "[Erai-raws] Blue Lock vs U-20 Japan - 02 [1080p CR WEB-DL AVC AAC][MultiSub]",
        "canonical": "anilist:500|2",
        "updated_at": 1.0,
    }
    erai._save_state(state)
    return {"video": video, "queued": queued}


def test_the_pass_puts_german_beside_a_library_episode_and_it_counts_as_german(library, site):
    video = library["video"]
    files = [{"season_number": 2, "episode": 2, "path": str(video)}]
    assert anime_library.german_subtitled_episodes(files) == {(2, 2): False}

    result = asyncio.run(erai_subs.add_library_subtitles(episodes=5))

    assert result == {"checked": 1, "added": 1, "remaining": 0, "unidentified": 0}
    sidecar = video.with_name("Blue Lock - S02E02.de.ass")
    assert sidecar.read_text(encoding="utf-8").lstrip("﻿").startswith("[Script Info]")
    assert library["queued"] == [video]
    anime_library._CODEC_CACHE = None  # as read again from disk
    assert anime_library.german_subtitled_episodes(files) == {(2, 2): True}
    record = erai_subs.load()["library"]["Blue Lock/Season 02/Blue Lock - S02E02.mkv"]
    assert record["url"].endswith("%5B1080p%5D.ger.ass")

    # Done: the next pass has nothing to do and asks nothing.
    before = len(site.requests)
    assert asyncio.run(erai_subs.add_library_subtitles())["checked"] == 0
    assert len(site.requests) == before


def test_the_pass_skips_an_episode_whose_release_is_unknown(library, site):
    state = erai._load_state()
    state["releases"] = {}
    erai._save_state(state)

    result = asyncio.run(erai_subs.add_library_subtitles())

    assert result["unidentified"] == 1 and result["checked"] == 0
    assert site.requests == []


def test_a_sidecar_already_beside_the_episode_counts_without_asking(library, site):
    video = library["video"]
    video.with_name(f"{video.stem}.ger.ass").write_text(ASS, encoding="utf-8")

    result = asyncio.run(erai_subs.add_library_subtitles())

    assert result["added"] == 0 and result["checked"] == 1
    assert site.requests == []
    assert anime_library.has_german_subtitles(anime_library._load_codec_cache()[str(video)]) is True


def test_the_probe_sweep_counts_a_german_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(anime_library, "_CODEC_CACHE", None)
    root = tmp_path / "anime"
    video = root / "Show" / "Season 01" / "Show [JP] - S01E01.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    video.with_name("Show [JP] - S01E01.de.ass").write_text(ASS, encoding="utf-8")
    other = video.with_name("Show [JP] - S01E02.mkv")
    other.write_bytes(b"x")
    other.with_name("Show [JP] - S01E02.eng.ass").write_text(ASS, encoding="utf-8")
    monkeypatch.setattr(anime_library, "probe_streams", lambda path: {"codec": "hevc", "audio": ["jpn"], "subs": ["eng"]})

    anime_library.sweep_codecs(root, limit=10)

    cache = anime_library._load_codec_cache()
    assert anime_library.has_german_subtitles(cache[str(video)]) is True
    assert anime_library.has_german_subtitles(cache[str(other)]) is False
