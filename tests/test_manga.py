"""Manga and light novels: the library's folders, the series followed, releases found, reviewed, downloaded and filed."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import time

import pytest

from bankai.config import reset_settings_cache
from bankai.metadata import anilist
from bankai.metadata import anilist_manga as manga_meta
from bankai.web import anime as nyaa
from bankai.web import anime_files, manga_library, manga_releases, manga_series, openpanel

FRIEREN, FRIEREN_ANIME = 118586, 154587
MUSHOKU_NOVEL, MUSHOKU_MANGA, MUSHOKU_ANIME = 85470, 85564, 108465
OSHI = 117195


@pytest.fixture()
def library(tmp_path, monkeypatch):
    """A manga and a light novel library, qBittorrent's downloads, and a small AniList of manga."""
    manga, novels, downloads = tmp_path / "manga", tmp_path / "light_novels", tmp_path / "qbit"
    for folder in (manga, novels, downloads):
        folder.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[qbittorrent]\nurl = "http://127.0.0.1:9"\nsave_path = "{downloads}"\n'
        f'[manga]\nlibrary_dir = "{manga}"\nnovels_dir = "{novels}"\nrequest_delay_seconds = 2\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BANKAI_CONFIG", str(config))
    reset_settings_cache()
    monkeypatch.setattr(manga_library, "OPENPANEL_DATABASE", tmp_path / "openpanel.db")
    entries = {
        FRIEREN: {"english": "Frieren: Beyond Journey's End", "romaji": "Sousou no Frieren", "format": "MANGA", "year": 2020},
        MUSHOKU_NOVEL: {
            "english": "Mushoku Tensei: Jobless Reincarnation", "romaji": "Mushoku Tensei: Isekai Ittara Honki Dasu",
            "format": "NOVEL", "year": 2014,
        },
        MUSHOKU_MANGA: {
            "english": "Mushoku Tensei: Jobless Reincarnation", "romaji": "Mushoku Tensei: Isekai Ittara Honki Dasu",
            "format": "MANGA", "year": 2014,
        },
        OSHI: {"english": "[Oshi no Ko]", "romaji": "Oshi no Ko", "format": "MANGA", "year": 2020},
    }
    store = manga_meta._store()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps({str(aid): {"synonyms": [], "relations": [], "fetched_at": time.time(), **row} for aid, row in entries.items()}),
        encoding="utf-8",
    )
    manga_meta._MEDIA = None
    return {"manga": manga, "novels": novels, "downloads": downloads}


def nyaa_entry(title: str, *, size: int = 3 * 2**30, info_hash: str | None = None, seeders: int = 50) -> nyaa.NyaaEntry:
    digest = info_hash or format(abs(hash(title)), "040x")[:40]
    return nyaa.NyaaEntry(
        id=1, title=title, download_url="https://nyaa.si/download/1.torrent", detail_url="https://nyaa.si/view/1",
        magnet_uri=f"magnet:?xt=urn:btih:{digest}", info_hash=digest, category_id="3_1",
        category="Literature - English-translated", size=f"{size}", size_bytes=size, seeders=seeders, leechers=1,
        downloads=10, comments=0, trusted=True, remake=False, published_at=None, publisher=None, quality=None,
    )


# -- The library ------------------------------------------------------------------------


def test_a_folder_s_name_says_its_series_and_maybe_its_entry():
    assert manga_library.folder_id("Ao Haru Ride (2011) - 54294") == 54294
    assert manga_library.folder_id("Berserk (1989)") is None
    assert manga_library.folder_title("Tokyo Ghoul - re (2014)") == "Tokyo Ghoul - re"
    assert manga_library.folder_title("Ao Haru Ride (2011) - 54294") == "Ao Haru Ride"
    assert manga_library.folder_year("Berserk (1989)") == 1989


def test_folders_are_adopted_by_their_id_by_openpanel_and_by_an_exact_search(library, monkeypatch):
    for name in ("Ao Haru Ride (2011) - 54294", "Chainsaw Man (2018)", "Frieren (2020)", "Unknown Thing (2001)"):
        (library["manga"] / name).mkdir()
    with sqlite3.connect(manga_library.OPENPANEL_DATABASE) as db:
        db.execute("CREATE TABLE series (name TEXT, anilist_id INTEGER)")
        db.execute("INSERT INTO series VALUES ('Chainsaw Man (2018)', 105778)")

    async def search(title, limit=10, kind=None):
        return [{"anilist_id": FRIEREN, "english": "Frieren", "romaji": "Sousou no Frieren", "year": 2020}] if title == "Frieren" else []

    monkeypatch.setattr(manga_meta, "search", search)
    monkeypatch.setattr(manga_meta, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))

    asyncio.run(manga_library.adopt())

    series = manga_library.series()
    assert {aid: (row["folder"], row["how"]) for aid, row in series.items()} == {
        54294: ("Ao Haru Ride (2011) - 54294", "folder"),
        105778: ("Chainsaw Man (2018)", "openpanel"),
        FRIEREN: ("Frieren (2020)", "search"),
    }
    assert [row["folder"] for row in manga_library.unmatched()] == ["Unknown Thing (2001)"]
    manga_library.match("manga", "Unknown Thing (2001)", 30002)
    assert manga_library.unmatched() == []


def test_what_a_series_holds_is_read_from_its_files(library):
    folder = library["manga"] / "Naruto (1999)"
    folder.mkdir()
    for name in ("Vol 01.cbz", "Vol 02.cbz", "Vol 03.cbz", "Chapter 0700.cbz", "cover.jpg"):
        (folder / name).write_bytes(b"x")
    manga_library.match("manga", "Naruto (1999)", 30011)

    assert manga_library.owned(30011) == ({1.0, 2.0, 3.0}, {700.0})


def test_a_new_series_folder_carries_its_entry_as_openpanel_reads_it(library):
    path = manga_library.ensure_folder(FRIEREN, "manga")

    assert path == library["manga"] / "Frieren - Beyond Journey's End (2020) - 118586"
    assert manga_library.folder_of(FRIEREN) == path
    assert manga_library.ensure_folder(MUSHOKU_NOVEL, "novel").parent == library["novels"]


# -- Followed series ------------------------------------------------------------------------


def test_the_manga_of_anime_on_disk_and_of_the_library_are_followed_but_not_blacklisted_ones(library, monkeypatch):
    monkeypatch.setattr(anime_files, "by_entry", lambda rows=None: {FRIEREN_ANIME: {1: ["x"]}, MUSHOKU_ANIME: {1: ["y"]}})
    linked = {
        FRIEREN_ANIME: {"manga": [[FRIEREN, "ADAPTATION", "MANGA"], [999001, "SPIN_OFF", "MANGA"]]},
        MUSHOKU_ANIME: {"manga": [[MUSHOKU_NOVEL, "ADAPTATION", "NOVEL"], [MUSHOKU_MANGA, "ALTERNATIVE", "MANGA"]]},
    }
    monkeypatch.setattr(anilist, "media", lambda aid: linked.get(int(aid), {}))
    (library["manga"] / "[Oshi no Ko] (2020)").mkdir()
    manga_library.match("manga", "[Oshi no Ko] (2020)", OSHI)
    manga_series.set_policy(MUSHOKU_MANGA, "blacklisted", origin="review")

    followed = manga_series.followed()

    assert followed == {FRIEREN: ["anime"], MUSHOKU_NOVEL: ["anime"], OSHI: ["library"]}


# -- Matching a release ---------------------------------------------------------------------


def index():
    return manga_releases.title_index([FRIEREN, MUSHOKU_NOVEL, MUSHOKU_MANGA, OSHI])


@pytest.mark.parametrize(
    ("title", "size", "expected"),
    [
        ("Sousou no Frieren | Frieren - Beyond Journey's End v01-14 + 138-147 (2021-2026) (Digital) (1r0n)", 3 * 2**30, (FRIEREN, False)),
        ("[Oshi No Ko] v13 (2026) (Digital) (ShelfLife)", 120 * 2**20, (OSHI, False)),
        # One title, two series: what it weighs says which.
        ("Mushoku Tensei - Jobless Reincarnation v01-23 (2015-2026) (Digital) (danke-Empire, Ushi)", 7 * 2**30, (MUSHOKU_MANGA, False)),
        ("Mushoku Tensei - Jobless Reincarnation v01-26 [Seven Seas] [LuCaZ]", 200 * 2**20, (MUSHOKU_NOVEL, False)),
        ("[Oshi No Ko] TV Anime 1st Season Official Guidebook - First Report (2026) (Digital) (1r0n)", 50 * 2**20, (None, False)),
        ("Koroshiya no Oshi | The Hitman Stans v01-02 (2025) (Digital) (1r0n)", 160 * 2**20, (None, False)),
    ],
)
def test_a_release_is_matched_to_a_series_by_its_titles(library, title, size, expected):
    assert manga_releases.match(manga_releases.manga_names.parse(title), size, index()) == expected


def test_searched_for_a_name_that_starts_the_series_title_is_a_guess_and_one_that_goes_on_is_not(library):
    parse = manga_releases.manga_names.parse

    assert manga_releases.match(parse("Frieren - (Chapters 141-147)"), 300 * 2**20, index(), searched=FRIEREN) == (FRIEREN, True)
    assert manga_releases.match(parse("Frieren - Beyond Journey's End -Prelude- [Yen Press] [Stick]"), 15 * 2**20, index(), searched=FRIEREN) == (None, False)


def test_a_release_whose_tags_say_novel_is_no_manga(library):
    parse = manga_releases.manga_names.parse

    assert manga_releases.match(parse("Sousou no Frieren [WN ∼ Complete ch.1-270]"), 5 * 2**20, index()) == (None, False)
    assert manga_releases.match(parse("Frieren (Novel) [01-30]"), 5 * 2**20, index(), searched=FRIEREN) == (None, False)
    assert manga_releases.match(parse("Mushoku Tensei - Jobless Reincarnation (Novel) [01-30]"), 5 * 2**20, index()) == (MUSHOKU_NOVEL, False)


def test_only_new_matched_releases_are_kept_and_owned_or_audio_ones_are_not_offered(library):
    folder = manga_library.ensure_folder(OSHI, "manga")
    for number in range(1, 13):
        (folder / f"Vol {number:02d}.cbz").write_bytes(b"x")
    entries = [
        nyaa_entry("[Oshi No Ko] v13 (2026) (Digital) (ShelfLife)", size=120 * 2**20),
        nyaa_entry("[Oshi No Ko] v01-12 (2023-2025) (Digital) (1r0n)", size=950 * 2**20),
        nyaa_entry("Mushoku Tensei - Redundant Reincarnation v03 [Audiobook] [Troglodyte]", size=340 * 2**20),
        nyaa_entry("Sousou no Frieren CH001-140 Mangadex-RIP", size=4 * 2**30, info_hash="a" * 40),
    ]
    followed = {OSHI: ["library"], MUSHOKU_NOVEL: ["anime"]}

    kept = manga_releases.consider(entries, index(), followed, {"a" * 40})

    statuses = {row["entry"]["title"]: row["status"] for row in kept.values()}
    assert statuses == {
        "[Oshi No Ko] v13 (2026) (Digital) (ShelfLife)": "review",
        "[Oshi No Ko] v01-12 (2023-2025) (Digital) (1r0n)": "owned",
    }


def test_a_series_is_searched_by_its_titles_up_to_the_subtitle(library):
    assert manga_releases.queries(MUSHOKU_NOVEL) == ["Mushoku Tensei"]
    assert manga_releases.queries(FRIEREN) == ["Frieren", "Sousou no Frieren"]
    assert manga_releases.queries(OSHI) == ["Oshi no Ko"]


# -- Review -------------------------------------------------------------------------------------


def found(library, *titles, anilist_id=FRIEREN):
    """Releases waiting for review, as discover would have kept them."""
    state = manga_releases._read()
    hashes = []
    for title in titles:
        entry = nyaa_entry(title)
        release = manga_releases.manga_names.parse(title)
        state["releases"][entry.info_hash] = manga_releases._row(entry, release, anilist_id, guess=False, how="feed")
        hashes.append(entry.info_hash)
    manga_releases._write(state)
    return hashes


def test_releases_wait_for_review_a_card_per_series_with_what_each_adds(library):
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    for number in range(1, 11):
        (folder / f"Vol {number:02d}.cbz").write_bytes(b"x")
    found(library, "Sousou no Frieren | Frieren - Beyond Journey's End v01-14 + 138-147 (Digital) (1r0n)", "Sousou no Frieren v01-10 (Digital) (LuCaZ)")

    cards = manga_releases.review()

    assert len(cards) == 1
    card = cards[0]
    assert (card["anilist_id"], card["owned"]) == (FRIEREN, "Vol 1–10")
    assert [view["adds"] for view in card["releases"]] == ["Vol 11–14 · Ch 138–147", ""]
    assert manga_releases.review_count() == 1


def test_a_release_is_approved_discarded_or_matched_to_another_series(library):
    first, second, third = found(library, "Frieren v01 (Digital)", "Frieren v02 (Digital)", "Mushoku Tensei v01 (Digital)")

    manga_releases.approve(first)
    manga_releases.discard(second)
    asyncio.run(manga_releases.rematch([third], MUSHOKU_MANGA))

    rows = manga_releases.releases()
    assert (rows[first]["status"], rows[second]["status"]) == ("queued", "discarded")
    assert (rows[third]["anilist_id"], rows[third]["how"], rows[third]["status"]) == (MUSHOKU_MANGA, "hand", "review")


def test_a_blacklisted_series_loses_its_releases_until_it_is_restored(library):
    (waiting,) = found(library, "Frieren v01 (Digital)")

    manga_releases.blacklist(FRIEREN)

    assert manga_releases.releases()[waiting]["status"] == "discarded"
    assert FRIEREN in manga_series.blacklisted()
    manga_releases.restore(FRIEREN)
    assert manga_releases.releases()[waiting]["status"] == "review"
    assert FRIEREN not in manga_series.blacklisted()


# -- Downloading and filing ---------------------------------------------------------------------


def test_a_download_is_filed_under_the_library_s_names_leaving_out_what_the_folder_has(library):
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    (folder / "Vol 01.cbz").write_bytes(b"old")
    content = library["downloads"] / "manga" / "Frieren v01-02 + 138 (Digital) (1r0n)"
    content.mkdir(parents=True)
    for name in ("Frieren v01 (2021) (Digital) (1r0n).cbz", "Frieren v02 (2021) (Digital) (1r0n).zip", "Frieren - c138 (2026) (Digital) (1r0n).cbz", "readme.txt", "Extras.cbz"):
        (content / name).write_bytes(b"new")
    (row_hash,) = found(library, "Frieren v01-02 + 138 (Digital) (1r0n)")

    result = manga_releases.file_release(manga_releases.releases()[row_hash], content)

    assert sorted(result["copied"]) == ["Chapter 0138.cbz", "Vol 02.cbz"]
    assert sorted(result["left"]) == [
        ["Extras.cbz", "no volume or chapter in its name"],
        ["Frieren v01 (2021) (Digital) (1r0n).cbz", "the library has it"],
    ]
    assert (folder / "Vol 01.cbz").read_bytes() == b"old"
    assert not list(folder.glob(".*"))


def test_only_what_fills_a_gap_goes_into_the_library_and_the_review_says_so(library):
    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(FRIEREN)] |= {"status": "FINISHED", "volumes": 2, "chapters": 10}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    # Volume 1, then chapter files from 6 on: chapter 5 is in no book.
    cbz(folder / "Vol 01.cbz", [f"Frieren - c{n:03d} (v01) - p001.jpg" for n in range(1, 5)])
    for n in range(6, 11):
        cbz(folder / f"Chapter {n:04d}.cbz", [f"Frieren - c{n:03d} (v02) - p001.jpg"])
    (row_hash,) = found(library, "Frieren v01 + 004-006 + 005.5 (Digital) (1r0n)")
    row = manga_releases.releases()[row_hash]

    # The review offers it for chapter 5 alone.
    assert manga_releases.adds(row) == (set(), {5.0})

    content = library["downloads"] / "manga" / "Frieren v01 + 004-006"
    content.mkdir(parents=True)
    cbz(content / "Frieren v01 (2021) (Digital) (1r0n).cbz", [f"Frieren - c{n:03d} (v01) - p001.jpg" for n in range(1, 5)])
    for n in (4, 5, 6):
        cbz(content / f"Frieren {n:03d} (2022) (Digital) (1r0n).cbz", ["001.jpg"])
    cbz(content / "Frieren 005.5 (2022) (Digital) (1r0n).cbz", ["001.jpg"])

    result = manga_releases.file_release(row, content)

    assert result["copied"] == ["Chapter 0005.cbz"]
    assert sorted(result["left"]) == [
        ["Frieren 004 (2022) (Digital) (1r0n).cbz", "the library has it"],
        ["Frieren 005.5 (2022) (Digital) (1r0n).cbz", "in between, outside the count"],
        ["Frieren 006 (2022) (Digital) (1r0n).cbz", "the library has it"],
        ["Frieren v01 (2021) (Digital) (1r0n).cbz", "the library has it"],
    ]


def test_a_volume_holds_its_chapters_from_first_to_last_and_two_volumes_those_between_them(library):
    from bankai.web import manga_contents, manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(FRIEREN)] |= {"status": "FINISHED", "volumes": 3, "chapters": 25}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    # Chapter 5's pages named for chapter 4, and the last chapters of the series before
    # (JoJo Part 5's first volume opens with Part 4's 172-174).
    cbz(folder / "Vol 01.cbz", [f"Frieren - c{n:03d} (v01) - p001.jpg" for n in (172, 173, 174, 1, 2, 3, 4, 6, 7, 8)])
    # Chapter 9 named for chapter 10, every chapter of it one on (Jujutsu Kaisen's v22).
    cbz(folder / "Vol 02.cbz", [f"Frieren - c{n:03d} (v02) - p001.jpg" for n in range(10, 19)])
    # Its last chapter's pages named "c197", a slip (Tokyo Ghoul:re's v15).
    cbz(folder / "Vol 03.cbz", [f"Frieren - c{n:03d} (v03) - p001.jpg" for n in (19, 20, 21, 22, 23, 24, 197)])
    (row_hash,) = found(library, "Frieren 005-009 (Digital) (1r0n)")

    result = manga_views.breakdown(FRIEREN)

    assert [(row["from"], row["to"], row["via"]) for row in result["rows"]] == [
        (1, 9, "Vol 01.cbz"),
        (10, 18, "Vol 02.cbz"),
        (19, 24, "Vol 03.cbz"),
    ]
    assert (result["have"], result["missing"]) == (24, 0)
    assert manga_releases.adds(manga_releases.releases()[row_hash]) == (set(), set())
    assert manga_contents.own_chapters({125, 130, 131, 132, 133}) == list(range(125, 134))
    assert manga_contents.own_chapters(set()) == []


def test_a_volume_replaces_the_chapter_files_it_holds_and_its_chapter_files_follow_it(library):
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    for n in range(1, 6):
        cbz(folder / f"Chapter {n:04d}.cbz", [f"Frieren - c{n:03d} - p001.jpg"])

    verdicts = manga_releases.wanted(
        FRIEREN,
        ["Frieren v01.cbz", "Frieren v02.cbz", "Frieren - c007.cbz", "Frieren - c011.cbz", "Frieren - c011 (v2).cbz"],
        pages={"Frieren v01.cbz": [1.0, 2.0, 3.0, 4.0, 5.0], "Frieren v02.cbz": [6.0, 7.0, 8.0, 9.0, 10.0]},
    )

    assert verdicts == {
        # Volumes where they exist: it takes the place of chapter files 1-5.
        "Frieren v01.cbz": None,
        "Frieren v02.cbz": None,
        # In Vol 02, which goes in.
        "Frieren - c007.cbz": "the library has it",
        "Frieren - c011.cbz": None,
        "Frieren - c011 (v2).cbz": "the library has it",
    }
    # A release of books without numbers -- a one-shot -- goes in whole.
    assert manga_releases.wanted(FRIEREN, ["Frieren - Special.cbz"]) == {"Frieren - Special.cbz": None}


class FakeQbit:
    def __init__(self, torrents, files=None):
        self.torrents = torrents
        self.listed = files or {}
        self.added: list[dict] = []
        self.removed: list[str] = []
        self.priorities: list[tuple[str, list[int], int]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def list_torrents(self, *, category=None):
        return list(self.torrents)

    async def add(self, **kwargs):
        self.added.append(kwargs)
        return True

    async def remove(self, info_hash, *, delete_files=False):
        assert delete_files
        self.removed.append(info_hash)

    async def files(self, info_hash):
        return self.listed.get(info_hash, [])

    async def file_priority(self, info_hash, indexes, priority):
        self.priorities.append((info_hash, indexes, priority))


class Torrent:
    def __init__(self, info_hash, state, progress, content_path):
        self.hash, self.state, self.progress, self.content_path = info_hash, state, progress, str(content_path)


def test_an_approved_release_is_added_to_qbittorrent_then_filed_and_removed_once_complete(library, monkeypatch):
    from bankai.torrent import qbittorrent

    first, second = found(library, "Frieren v01 (Digital) (1r0n)", "Frieren v02 (Digital) (1r0n)")
    manga_releases.approve(first)
    manga_releases.approve(second)
    content = library["downloads"] / "manga" / "Frieren v02 (Digital) (1r0n).cbz"
    content.parent.mkdir(parents=True)
    content.write_bytes(b"volume two")
    fake = FakeQbit([Torrent(second, "uploading", 1.0, content)])
    monkeypatch.setattr(qbittorrent, "QBittorrentClient", lambda *a, **k: fake)
    scans: list[bool] = []
    monkeypatch.setattr(openpanel, "scan", lambda: asyncio.sleep(0, scans.append(True)))

    result = asyncio.run(manga_releases.reconcile())

    rows = manga_releases.releases()
    assert result == {"active": 2, "filed": 1}
    assert [call["category"] for call in fake.added] == ["bankai-manga"]
    assert fake.added[0]["save_path"] == library["downloads"] / "manga"
    assert rows[first]["status"] == "downloading"
    assert (rows[second]["status"], rows[second]["copied"]) == ("done", ["Vol 02.cbz"])
    assert fake.removed == [second]
    assert scans == [True]
    assert (manga_library.folder_of(FRIEREN) / "Vol 02.cbz").read_bytes() == b"volume two"


def test_qbittorrent_downloads_only_what_the_library_lacks_and_nothing_when_it_lacks_nothing(library, monkeypatch):
    from bankai.torrent import qbittorrent

    folder = manga_library.ensure_folder(FRIEREN, "manga")
    (folder / "Vol 01.cbz").write_bytes(b"x")
    some, none = found(library, "Frieren v01-02 (Digital) (1r0n)", "Frieren v01 (Digital) (1r0n)")
    manga_releases.approve(some)
    manga_releases.approve(none)
    pack = "Frieren v01-02 (Digital) (1r0n)/"
    files = {
        some: [
            {"index": 0, "name": pack + "Frieren v01 (2021) (Digital) (1r0n).cbz"},
            {"index": 1, "name": pack + "Frieren v02 (2021) (Digital) (1r0n).cbz"},
            {"index": 2, "name": pack + "cover.jpg"},
        ],
        none: [{"index": 0, "name": "Frieren v01 (2021) (Digital) (1r0n).cbz"}],
    }
    fake = FakeQbit([Torrent(some, "downloading", 0.1, "x"), Torrent(none, "downloading", 0.0, "y")], files)
    monkeypatch.setattr(qbittorrent, "QBittorrentClient", lambda *a, **k: fake)

    asyncio.run(manga_releases.reconcile())

    rows = manga_releases.releases()
    assert fake.priorities == [(some, [0, 2], 0)]
    assert (rows[some]["status"], rows[some]["picked"]) == ("downloading", True)
    assert rows[some]["left"] == [["Frieren v01 (2021) (Digital) (1r0n).cbz", "the library has it"]]
    # Nothing in it the library lacks: not downloaded at all.
    assert (rows[none]["status"], rows[none]["copied"]) == ("done", [])
    assert fake.removed == [none]


# -- What the pages show ------------------------------------------------------------------------


def test_the_library_page_lists_series_on_disk_followed_ones_folders_to_match_and_downloads(library, monkeypatch):
    from bankai.web import manga_views

    monkeypatch.setattr(anime_files, "by_entry", lambda rows=None: {FRIEREN_ANIME: {1: ["x"]}})
    monkeypatch.setattr(anilist, "media", lambda aid: {"manga": [[FRIEREN, "ADAPTATION", "MANGA"]]} if int(aid) == FRIEREN_ANIME else {})
    monkeypatch.setattr(anilist, "name", lambda aid: "Frieren (anime)")
    folder = manga_library.ensure_folder(OSHI, "manga")
    (folder / "Vol 01.cbz").write_bytes(b"x")
    (library["manga"] / "Mystery (1999)").mkdir()
    (queued,) = found(library, "Frieren v01 (Digital)")
    manga_releases.approve(queued)

    page = manga_views.library()

    assert [(row["anilist_id"], row["owned"]) for row in page["series"]] == [(OSHI, "Vol 1")]
    assert [(row["anilist_id"], row["why"], [anime["name"] for anime in row["anime"]]) for row in page["followed"]] == [
        (FRIEREN, ["anime"], ["Frieren (anime)"])
    ]
    assert [row["folder"] for row in page["unmatched"]] == ["Mystery (1999)"]
    assert [(row["info_hash"], row["status"], row["name"]) for row in page["downloads"]] == [(queued, "queued", "Frieren: Beyond Journey's End")]


def test_an_anime_shows_its_manga_with_what_bankai_does_about_each(library, monkeypatch):
    from bankai.web import manga_views

    monkeypatch.setattr(anilist, "media", lambda aid: {"manga": [[MUSHOKU_NOVEL, "ADAPTATION", "NOVEL"], [MUSHOKU_MANGA, "ALTERNATIVE", "MANGA"]]})
    monkeypatch.setattr(manga_meta, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))
    manga_library.ensure_folder(MUSHOKU_NOVEL, "novel")
    manga_series.set_policy(MUSHOKU_MANGA, "blacklisted", origin="review")

    rows = {row["anilist_id"]: row for row in asyncio.run(manga_views.linked([MUSHOKU_ANIME]))}

    assert (rows[MUSHOKU_NOVEL]["on_disk"], rows[MUSHOKU_NOVEL]["format"]) == (True, "NOVEL")
    assert rows[MUSHOKU_MANGA]["blacklisted"] is True
    # Which anime links it, so the relation graph draws its line from there.
    assert rows[MUSHOKU_NOVEL]["anime"] == [MUSHOKU_ANIME]


def test_a_series_graph_holds_its_anime_and_its_other_manga(library, monkeypatch):
    from bankai.web import manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(MUSHOKU_NOVEL)]["relations"] = [[MUSHOKU_ANIME, "ADAPTATION", "ANIME", "TV"], [MUSHOKU_MANGA, "ALTERNATIVE", "MANGA", "MANGA"]]
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    monkeypatch.setattr(manga_meta, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))
    monkeypatch.setattr(anilist, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))
    monkeypatch.setattr(anime_files, "by_entry", lambda rows=None: {MUSHOKU_ANIME: {1: ["x"]}})
    monkeypatch.setattr(anilist, "media", lambda aid: {"format": "TV", "year": 2021} if int(aid) == MUSHOKU_ANIME else {})
    monkeypatch.setattr(anilist, "name", lambda aid: "Mushoku Tensei")

    graph = asyncio.run(manga_views.graph(MUSHOKU_NOVEL))

    nodes = {(node["type"], node["anilist_id"]): node for node in graph["nodes"]}
    assert nodes[("ANIME", MUSHOKU_ANIME)]["on_disk"] is True
    assert nodes[("MANGA", MUSHOKU_MANGA)]["relation"] == "ALTERNATIVE"
    assert [edge["label"] for edge in graph["edges"]] == ["ADAPTATION", "ALTERNATIVE"]


def test_a_finished_series_with_every_volume_needs_nothing_and_dead_releases_are_not_offered(library):
    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(OSHI)] |= {"status": "FINISHED", "volumes": 16}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(OSHI, "manga")
    for number in range(1, 17):
        (folder / f"Vol {number:02d}.cbz").write_bytes(b"x")
    old = "Sat, 13 Sep 2015 10:00:00 -0000"
    entries = [
        nyaa_entry("[Oshi No Ko] 001-166 (Digital) (Oak) [Completed]"),
        nyaa_entry("[Oshi No Ko] (2020-2024) (Digital) (danke-Empire)"),
        dataclasses.replace(nyaa_entry("Frieren - Chapter 12 [MangaStream]", seeders=0), published_at=old),
    ]

    kept = manga_releases.consider(entries, index(), {OSHI: ["library"], FRIEREN: ["anime"]}, set())

    assert sorted(row["status"] for row in kept.values()) == ["owned", "owned"]


def test_waiting_releases_the_library_has_meanwhile_leave_the_review(library):
    (waiting,) = found(library, "Frieren v01-02 (Digital)")
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    for name in ("Vol 01.cbz", "Vol 02.cbz"):
        (folder / name).write_bytes(b"x")
    state = manga_releases._read()

    assert manga_releases.tidy(state) == 1
    assert state["releases"][waiting]["status"] == "owned"


def test_a_release_s_name_is_read_again_so_a_better_reading_reaches_those_found_before(library):
    (misread,) = found(library, "Solo Leveling {2021-2023} {Digital} {Tapas} {4str0}")
    state = manga_releases._read()
    # As the reading before had it: the years as chapters.
    state["releases"][misread]["chapters"] = [2021.0, 2023.0]

    assert manga_releases.tidy(state) == 1
    assert state["releases"][misread]["chapters"] is None
    assert manga_releases.tidy(state) == 0


def test_a_waiting_release_whose_tags_say_novel_leaves_a_manga_as_if_never_found(library):
    (novel,) = found(library, "Sousou no Frieren [WN ∼ Complete ch.1-270]")
    state = manga_releases._read()

    assert manga_releases.tidy(state) == 1
    assert novel not in state["releases"]


def test_two_folders_of_one_series_leave_the_second_to_be_matched_by_hand(library):
    for name in ("Berserk (1989) - 30002", "Berserk Deluxe (2019) - 30002"):
        (library["manga"] / name).mkdir()

    asyncio.run(manga_library.adopt())
    asyncio.run(manga_library.adopt())

    assert manga_library.series()[30002]["folder"] == "Berserk (1989) - 30002"
    assert [row["folder"] for row in manga_library.unmatched()] == ["Berserk Deluxe (2019) - 30002"]


# -- What a series' files hold, chapter by chapter ----------------------------------------------


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ("One Piece - Digital Colored Comics - c0036 (v005) - p000 [Just Kidding Productions].jpg", (36.0, 5)),
        ("Bleach - Digital Colored Comics v01 (2021) (Colored Council)/Chapter 001 - Death & Strawberry/001.jpg", (1.0, 1)),
        ("v01c01/000.jpg", (1.0, 1)),
        ("001/01.jpg", (1.0, None)),
        ("Vinland Saga - c016x6 (v02) - p471 [Kodansha Comics] [Digital] [lfp-DCP] {HQ}.png", (16.0, 2)),
        ("Solo Leveling - c000 (v01) - p000 [Yen Press] [Digital] [1r0n].jpg", (0.0, 1)),
        ("Some Comic 001 [c2c] p01.jpg", (None, None)),
        ("2021/001.jpg", (None, None)),
    ],
)
def test_a_page_s_name_says_its_chapter_and_volume(page, expected):
    from bankai.web import manga_contents

    assert manga_contents.page_number(page) == expected


def cbz(path, pages):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for page in pages:
            archive.writestr(page, b"x")


def test_a_series_breakdown_says_where_each_chapter_is_and_which_are_missing(library):
    from bankai.web import manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(FRIEREN)] |= {"status": "RELEASING", "volumes": None, "chapters": 30}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    cbz(folder / "Vol 01.cbz", [f"Frieren - c{n:03d} (v01) - p00{n}.jpg" for n in range(1, 8)])
    cbz(folder / "Vol 02.cbz", ["001.jpg", "002.jpg"])  # names no chapter: between its neighbours
    cbz(folder / "Vol 03.cbz", [f"Frieren - c{n:03d} (v03) - p001.jpg" for n in range(13, 19)])
    for n in (19, 20):
        cbz(folder / f"Chapter {n:04d}.cbz", [f"Frieren - c{n:03d} (v04) - p001.jpg"])
    cbz(folder / "Chapter 0021.cbz", ["Frieren - c021 - p001.jpg"])

    result = manga_views.breakdown(FRIEREN)

    rows = [(row["from"], row["to"], row["kind"], row["volume"], row["via"], row["guessed"]) for row in result["rows"]]
    assert rows == [
        (1, 7, "volume", 1, "Vol 01.cbz", False),
        (8, 12, "volume", 2, "Vol 02.cbz", True),
        (13, 18, "volume", 3, "Vol 03.cbz", False),
        (19, 20, "chapter", 4, "Chapter 0019.cbz", False),
        (21, 21, "chapter", None, "Chapter 0021.cbz", False),
        (22, 30, "missing", None, None, False),
    ]
    assert (result["have"], result["missing"]) == (21, 9)
    assert [row["state"] for row in result["volumes"]] == ["file", "file", "file", "chapters"]
    # The library's table counts the same.
    (card,) = manga_views.library()["series"]
    assert card["count"] == {"unit": "chapter", "have": 21, "total": 30}


def test_a_finished_series_with_every_volume_is_whole_even_where_its_pages_name_no_chapter(library):
    from bankai.web import manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(OSHI)] |= {"status": "FINISHED", "volumes": 3, "chapters": 30}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(OSHI, "manga")
    cbz(folder / "Vol 01.cbz", [f"Oshi no Ko - c{n:03d} (v01) - p001.jpg" for n in range(1, 11)])
    cbz(folder / "Vol 02.cbz", ["a/001.jpg"])
    cbz(folder / "Vol 03.cbz", ["b/001.jpg"])

    result = manga_views.breakdown(OSHI)

    # The rest shared out between the two, a row each: each can be ticked on its own.
    assert [(row["from"], row["to"], row["via"], row["guessed"]) for row in result["rows"]] == [
        (1, 10, "Vol 01.cbz", False),
        (11, 20, "Vol 02.cbz", True),
        (21, 30, "Vol 03.cbz", True),
    ]
    assert (result["missing"], result["complete"]) == (0, True)


def test_every_file_of_a_series_is_in_a_row_beside_where_it_belongs(library):
    from bankai.web import manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(FRIEREN)] |= {"status": "RELEASING", "volumes": None, "chapters": 30}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    cbz(folder / "Vol 00.cbz", ["Frieren - c000 (v00) - p001.jpg"])
    cbz(folder / "Vol 01.cbz", [f"Frieren - c{n:03d} (v01) - p001.jpg" for n in range(1, 6)])
    cbz(folder / "Vol 01.zip", [f"Frieren - c{n:03d} (v01) - p001.jpg" for n in range(1, 6)])
    # Vol 02 and Vol 04 name no chapter, Vol 03 is not on disk: 6-20 shared out by volume number.
    cbz(folder / "Vol 02.cbz", ["001.jpg"])
    cbz(folder / "Vol 04.cbz", ["001.jpg"])
    cbz(folder / "Vol 05.cbz", [f"Frieren - c{n:03d} (v05) - p001.jpg" for n in range(21, 26)])
    for name in ("Frieren 022 (2022) (Digital) (1r0n).cbz", "Frieren 023 (2022) (Digital) (1r0n).cbz", "Chapter 0025.5.cbz", "Chapter 0026.cbz", "Extras.cbz"):
        cbz(folder / name, ["001.jpg"])

    result = manga_views.breakdown(FRIEREN)

    rows = [(row["from"], row["to"], row["kind"], row["names"], row["guessed"], row["note"]) for row in result["rows"]]
    assert rows == [
        (0, 0, "volume", ["Vol 00.cbz"], False, "its own numbering, before the series"),
        (1, 5, "volume", ["Vol 01.cbz"], False, None),
        (1, 5, "volume", ["Vol 01.zip"], False, "the same volume as Vol 01.cbz"),
        (6, 10, "volume", ["Vol 02.cbz"], True, None),
        (11, 15, "missing", [], False, None),
        (16, 20, "volume", ["Vol 04.cbz"], True, None),
        (21, 25, "volume", ["Vol 05.cbz"], False, None),
        (22, 23, "chapter", ["Frieren 022 (2022) (Digital) (1r0n).cbz", "Frieren 023 (2022) (Digital) (1r0n).cbz"], False,
         "chapters 22–23 are in Vol 05.cbz too"),
        (25.5, 25.5, "chapter", ["Chapter 0025.5.cbz"], False, "in between, outside the count"),
        (26, 26, "chapter", ["Chapter 0026.cbz"], False, None),
        (27, 30, "missing", [], False, None),
        (None, None, "other", ["Extras.cbz"], False, "its name gives no single volume or chapter"),
    ]
    assert (result["unit"], result["have"], result["missing"]) == ("chapter", 21, 9)


def test_a_light_novel_s_breakdown_counts_volumes(library):
    from bankai.web import manga_views

    store = json.loads(manga_meta._store().read_text(encoding="utf-8"))
    store[str(MUSHOKU_NOVEL)] |= {"status": "FINISHED", "volumes": 5}
    manga_meta._store().write_text(json.dumps(store), encoding="utf-8")
    manga_meta._MEDIA = None
    folder = manga_library.ensure_folder(MUSHOKU_NOVEL, "novel")
    for name in ("Vol 01.epub", "Vol 02.epub", "Vol 05.epub", "Vol 05.5.epub"):
        (folder / name).write_bytes(b"x")

    result = manga_views.breakdown(MUSHOKU_NOVEL)

    assert [(row["from"], row["to"], row["kind"], row["names"], row["note"]) for row in result["rows"]] == [
        (1, 1, "volume", ["Vol 01.epub"], None),
        (2, 2, "volume", ["Vol 02.epub"], None),
        (3, 4, "missing", [], None),
        (5, 5, "volume", ["Vol 05.epub"], None),
        (None, None, "volume", ["Vol 05.5.epub"], "in between, outside the count"),
    ]
    assert (result["unit"], result["have"], result["missing"]) == ("volume", 3, 2)
    (card,) = manga_views.library()["series"]
    assert card["count"] == {"unit": "volume", "have": 3, "total": 5}


def test_books_of_another_series_move_into_its_folder_under_their_new_names(library):
    source = manga_library.ensure_folder(FRIEREN, "manga")
    for name in ("Vol 00.cbz", "Vol 01.cbz"):
        (source / name).write_bytes(name.encode())

    result = manga_library.move(FRIEREN, ["Vol 00.cbz"], OSHI, {"Vol 00.cbz": "Vol 01"})

    target = manga_library.folder_of(OSHI)
    assert result == {"moved": 1, "folder": target.name, "names": ["Vol 01.cbz"]}
    assert (target / "Vol 01.cbz").read_bytes() == b"Vol 00.cbz"
    assert sorted(path.name for path in source.iterdir()) == ["Vol 01.cbz"]
    # A name the other series has already: nothing moves.
    with pytest.raises(ValueError, match="already"):
        manga_library.move(FRIEREN, ["Vol 01.cbz"], OSHI)
    assert (source / "Vol 01.cbz").exists()
    # A download of the series that holds the moved book again does not bring it back.
    content = library["downloads"] / "manga" / "Frieren v00-02 (Digital) (1r0n)"
    content.mkdir(parents=True)
    for name in ("Frieren v00 (2021) (Digital) (1r0n).cbz", "Frieren v02 (2021) (Digital) (1r0n).cbz"):
        (content / name).write_bytes(b"new")
    result = manga_releases.file_release({"anilist_id": FRIEREN, "kind": "manga"}, content)
    assert (result["copied"], result["left"]) == (["Vol 02.cbz"], [["Frieren v00 (2021) (Digital) (1r0n).cbz", "moved to another series"]])


def test_a_removed_series_loses_its_folder_and_downloads_and_is_blacklisted(library, monkeypatch):
    from bankai.torrent import qbittorrent

    folder = manga_library.ensure_folder(FRIEREN, "manga")
    (folder / "Vol 01.cbz").write_bytes(b"volume one")
    other = manga_library.ensure_folder(OSHI, "manga")
    (other / "Vol 01.cbz").write_bytes(b"another series")
    downloading, waiting = found(library, "Frieren v02 (Digital) (1r0n)", "Frieren v03 (Digital) (1r0n)")
    manga_releases.approve(downloading)
    fake = FakeQbit([])
    monkeypatch.setattr(qbittorrent, "QBittorrentClient", lambda *a, **k: fake)
    monkeypatch.setattr(openpanel, "scan", lambda: asyncio.sleep(0))

    result = asyncio.run(manga_releases.remove_series(FRIEREN))

    rows = manga_releases.releases()
    assert (result["deleted_files"], result["stopped"], result["discarded"]) == (1, 1, 1)
    assert not folder.exists() and manga_library.folder_of(FRIEREN) is None
    assert (other / "Vol 01.cbz").read_bytes() == b"another series"
    assert fake.removed == [downloading]
    assert rows[downloading]["status"] == rows[waiting]["status"] == "discarded"
    assert FRIEREN in manga_series.blacklisted()
    # Nothing of it is downloaded again: the downloads pass has nothing left to follow.
    assert asyncio.run(manga_releases.reconcile()) == {}


def test_a_download_stopped_while_the_pass_runs_is_not_brought_back(library):
    (info_hash,) = found(library, "Frieren v02 (Digital) (1r0n)")
    manga_releases.approve(info_hash)
    manga_releases._change(info_hash, status="discarded", reason="series removed from the library")

    manga_releases._change(info_hash, only=manga_releases.ACTIVE, status="downloading", progress=0.5)

    assert manga_releases.releases()[info_hash]["status"] == "discarded"


# -- The plan: what to download of a series ------------------------------------------------------


def roles_of(card):
    return {view["info_hash"]: view["role"] for view in card["releases"]}


def test_the_plan_is_the_fewest_releases_that_bring_everything_and_the_rest_are_other_versions(library):
    whole, older, single, rip = found(
        library,
        "Frieren v01-14 + 138-147 (2021-2026) (Digital) (1r0n)",
        "Frieren v01-10 (2021-2024) (Digital) (LuCaZ)",
        "Frieren v12 (Digital)",
        "Sousou no Frieren CH001-140 Mangadex-RIP",
    )

    (card,) = manga_releases.review()

    # The rip's chapters up to 137 are in the pack's volumes, as its name says; 138-140 it holds too.
    assert roles_of(card) == {whole: "plan", older: "covered", single: "covered", rip: "covered"}
    assert card["releases"][0]["info_hash"] == whole


def test_a_download_under_way_counts_as_had_so_its_other_versions_wait_behind_it(library):
    whole, older = found(library, "Frieren v01-14 (Digital) (1r0n)", "Frieren v01-10 (Digital) (LuCaZ)")
    manga_releases.approve(whole)

    (card,) = manga_releases.review()

    assert roles_of(card) == {older: "covered"}


def test_taking_a_plan_approves_its_releases_and_never_a_guessed_match(library):
    whole, single, guessed = found(library, "Frieren v01-14 (Digital) (1r0n)", "Frieren v15 (Digital)", "Frieren v16 (Digital)")
    state = manga_releases._read()
    state["releases"][guessed]["guess"] = True
    manga_releases._write(state)

    assert manga_releases.take_plan(FRIEREN) == [whole, single]

    rows = manga_releases.releases()
    assert [rows[key]["status"] for key in (whole, single, guessed)] == ["queued", "queued", "review"]
    (card,) = manga_releases.review()
    assert roles_of(card) == {guessed: "extra"}


def test_a_volume_the_folder_holds_as_chapter_files_is_still_wanted_to_replace_them(library):
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    for chapter in (1, 2):
        cbz(folder / f"Chapter {chapter:04d}.cbz", [f"Frieren - c{chapter:03d} (v01) - p{page:03d}.jpg" for page in range(3)])
    volume_one, volume_two = found(library, "Frieren v01 (Digital)", "Frieren v02 (Digital)")

    found_now = manga_releases.holdings(FRIEREN)
    rows = manga_releases.releases()

    assert manga_releases.adds(rows[volume_one], found_now) == ({1.0}, set())
    assert manga_releases.release_view(volume_one, rows[volume_one], found_now)["replaces"] == "Vol 1"
    assert manga_releases.release_view(volume_two, rows[volume_two], found_now)["replaces"] is None


# -- Collections and the layout on disk ---------------------------------------------------------

GHOUL, GHOUL_RE, GHOUL_JACK = 63327, 85611, 77777


def ghouls(*, jack=False):
    """Tokyo Ghoul and its sequel in AniList's cache (and Jack, a side story, when asked)."""
    store = manga_meta._store()
    data = json.loads(store.read_text(encoding="utf-8"))
    entries = {
        GHOUL: {"english": "Tokyo Ghoul", "year": 2011, "relations": [[GHOUL_RE, "SEQUEL", "MANGA", "MANGA"], [GHOUL_JACK, "SIDE_STORY", "MANGA", "MANGA"]]},
        GHOUL_RE: {"english": "Tokyo Ghoul:re", "year": 2014, "relations": [[GHOUL, "PREQUEL", "MANGA", "MANGA"]]},
    }
    if jack:
        entries[GHOUL_JACK] = {"english": "Tokyo Ghoul: Jack", "year": 2013, "relations": [[GHOUL, "PARENT", "MANGA", "MANGA"]]}
    for aid, row in entries.items():
        data[str(aid)] = {"synonyms": [], "fetched_at": time.time(), "format": "MANGA", "romaji": row["english"], **row}
    store.write_text(json.dumps(data), encoding="utf-8")
    manga_meta._MEDIA = None


def shelve(library, folder, aid, *books):
    path = library["manga"] / folder
    path.mkdir(parents=True)
    for book in books:
        (path / book).write_bytes(b"x")
    manga_library._remember(aid, "manga", folder, "hand")
    return path


def test_the_main_line_joins_a_collection_named_after_what_its_entries_share(library):
    from bankai.web import manga_collections

    ghouls(jack=True)
    store = manga_collections.Store()

    manga_collections.build(store, {GHOUL, GHOUL_RE, GHOUL_JACK, FRIEREN})

    # The side story stays out, and an entry alone is no collection.
    (row,) = store.collections.values()
    assert row.members == [GHOUL, GHOUL_RE]
    assert manga_collections.name(row) == "Tokyo Ghoul"
    manga_collections.exclude(store, GHOUL_RE)
    assert store.collections == {}
    manga_collections.build(store, {GHOUL, GHOUL_RE})
    assert store.collections == {}


def test_a_collection_s_entries_move_into_its_folder_their_books_renamed_and_undo_puts_it_back(library, monkeypatch):
    from bankai.web import manga_layout

    ghouls()
    monkeypatch.setattr(manga_series, "followed", lambda: {})
    shelve(library, "Tokyo Ghoul (2011)", GHOUL, "Tokyo Ghoul v01 (2015) (Digital).cbz", "Vol 02.cbz")
    shelve(library, "Tokyo Ghoul - re (2014)", GHOUL_RE, "Vol 01.cbz")
    alone = shelve(library, "Frieren (2020)", FRIEREN, "Frieren v01-02 (Digital).cbz")

    result = manga_layout.apply()

    first, second = "Tokyo Ghoul/Tokyo Ghoul (2011) - 63327", "Tokyo Ghoul/Tokyo Ghoul -re (2014) - 85611"
    assert [move["to"] for move in result["folders"]] == [first, second]
    assert sorted(path.name for path in (library["manga"] / first).iterdir()) == ["Vol 01.cbz", "Vol 02.cbz"]
    assert sorted(path.name for path in alone.iterdir()) == ["Vol 01-02.cbz"]
    assert manga_library.folder_of(GHOUL_RE) == library["manga"] / second
    assert manga_library.folders("manga") == ["Frieren (2020)", first, second]
    assert manga_layout.plan()["folders"] == []

    manga_layout.undo(result["journal"])

    assert sorted(path.name for path in library["manga"].iterdir()) == ["Frieren (2020)", "Tokyo Ghoul (2011)", "Tokyo Ghoul - re (2014)"]
    assert (library["manga"] / "Tokyo Ghoul (2011)" / "Tokyo Ghoul v01 (2015) (Digital).cbz").exists()
    assert manga_library.folder_of(GHOUL) == library["manga"] / "Tokyo Ghoul (2011)"


def test_a_collection_of_which_the_library_has_one_entry_keeps_it_where_it_is(library, monkeypatch):
    from bankai.web import manga_layout

    ghouls()
    monkeypatch.setattr(manga_series, "followed", lambda: {GHOUL_RE: ["anime"]})
    shelve(library, "Tokyo Ghoul (2011)", GHOUL, "Vol 01.cbz")

    todo = manga_layout.plan()

    assert [(row["name"], row["folder"]) for row in todo["collections"]] == [("Tokyo Ghoul", None)]
    assert todo["folders"] == []


def test_a_new_entry_of_a_collection_is_filed_into_its_folder_and_the_one_on_disk_moves_in(library, monkeypatch):
    from bankai.web import manga_collections

    ghouls()
    monkeypatch.setattr(manga_series, "followed", lambda: {GHOUL_RE: ["anime"]})
    shelve(library, "Tokyo Ghoul (2011)", GHOUL, "Vol 01.cbz")
    with manga_collections.editing() as store:
        manga_collections.build(store, {GHOUL, GHOUL_RE})

    folder = manga_library.ensure_folder(GHOUL_RE, "manga")

    assert folder == library["manga"] / "Tokyo Ghoul" / "Tokyo Ghoul -re (2014) - 85611"
    assert manga_library.folder_of(GHOUL) == library["manga"] / "Tokyo Ghoul" / "Tokyo Ghoul (2011) - 63327"
    assert (manga_library.folder_of(GHOUL) / "Vol 01.cbz").exists()


def test_a_filed_volume_takes_the_place_of_its_chapter_files_and_a_second_copy_stays_out(library):
    folder = manga_library.ensure_folder(FRIEREN, "manga")
    for chapter in (1, 2, 3):
        cbz(folder / f"Chapter {chapter:04d}.cbz", [f"Frieren - c{chapter:03d} (v01) - p001.jpg"])
    content = library["downloads"] / "manga" / "Frieren v01-02"
    content.mkdir(parents=True)
    cbz(content / "Frieren v01 (Digital).cbz", [f"Frieren - c{chapter:03d} (v01) - p001.jpg" for chapter in (1, 2, 3)])
    cbz(content / "Frieren - c004 (Digital).cbz", ["Frieren - c004 (v02) - p001.jpg"])

    result = manga_releases.file_release({"anilist_id": FRIEREN, "kind": "manga"}, content)

    assert sorted(result["copied"]) == ["Chapter 0004.cbz", "Vol 01.cbz"]
    assert result["replaced"] == ["Chapter 0001.cbz", "Chapter 0002.cbz", "Chapter 0003.cbz"]
    assert sorted(path.name for path in folder.iterdir()) == ["Chapter 0004.cbz", "Vol 01.cbz"]
    # Its chapters in a volume now, another copy of that volume stays out.
    assert manga_releases.wanted(FRIEREN, ["Frieren v01 (Other).cbz"], pages={"Frieren v01 (Other).cbz": [1.0, 2.0, 3.0]}) == {
        "Frieren v01 (Other).cbz": "the library has it"}


def test_entries_split_off_and_collections_merge_by_hand(library):
    from bankai.web import manga_collections

    ghouls(jack=True)
    store = manga_collections.Store()
    manga_collections.build(store, {GHOUL, GHOUL_RE})
    (row,) = store.collections.values()
    other = manga_collections.together(store, [GHOUL_JACK, FRIEREN])

    manga_collections.merge(store, row.id, other.id)

    assert store.collections[row.id].members == [GHOUL, GHOUL_JACK, GHOUL_RE, FRIEREN]
    assert other.id not in store.collections
    assert manga_collections.split(store, [GHOUL_JACK, FRIEREN]) is not None
    assert manga_collections.split(store, [GHOUL_RE]) is None
    assert {tuple(c.members) for c in store.collections.values()} == {(GHOUL_JACK, FRIEREN)}
    assert GHOUL_RE in store.alone


def test_every_entry_of_a_collection_shows_the_same_graph_its_main_line_in_order(library, monkeypatch):
    from bankai.web import manga_views

    ghouls(jack=True)
    shelve(library, "Tokyo Ghoul (2011)", GHOUL, "Vol 01.cbz")
    shelve(library, "Tokyo Ghoul - re (2014)", GHOUL_RE, "Vol 01.cbz")
    monkeypatch.setattr(manga_series, "followed", lambda: {})
    monkeypatch.setattr(manga_meta, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))
    monkeypatch.setattr(anilist, "ensure", lambda *_a, **_k: asyncio.sleep(0, 0))

    first, second = asyncio.run(manga_views.graph(GHOUL)), asyncio.run(manga_views.graph(GHOUL_RE))

    shape = lambda graph: (sorted(node["anilist_id"] for node in graph["nodes"]), graph["members"])  # noqa: E731
    assert shape(first) == shape(second) == (sorted([GHOUL, GHOUL_RE, GHOUL_JACK]), [GHOUL, GHOUL_RE])
    assert (first["root"], second["root"]) == (GHOUL, GHOUL_RE)
