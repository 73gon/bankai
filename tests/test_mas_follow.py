"""Shows followed in English: what is searched for, which release is picked, where files go."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.processor import direct as direct_job
from bankai.web import direct, mas_follow


def _row(season, episode, state, extra=False):
    return {"season": season, "episode": episode, "state": state, "paths": [], "extra": extra}


def test_a_season_mostly_missing_is_one_pack_search_a_few_missing_are_searched_alone():
    rows = [_row(1, e, "missing") for e in range(1, 11)]
    rows += [_row(2, e, "german") for e in range(1, 9)] + [_row(2, 9, "missing"), _row(2, 10, "missing")]
    units = mas_follow.plan(rows, {}, now=1e9)
    assert [unit["key"] for unit in units] == ["S01", "S02E09", "S02E10"]
    assert len(units[0]["want"]) == 10


def test_a_pack_search_that_found_nothing_turns_into_episode_searches_and_retries_wait():
    rows = [_row(1, e, "missing") for e in range(1, 5)]
    now = 1e9
    searched = {"S01": {"at": now - 60, "found": False}, "S01E01": {"at": now - 60, "found": False}}
    assert [unit["key"] for unit in mas_follow.plan(rows, searched, now=now)] == ["S01E02", "S01E03", "S01E04"]


def test_a_season_held_only_in_english_is_looked_for_in_german_now_and_then():
    rows = [_row(1, e, "english") for e in range(1, 4)] + [_row(1, 4, "upcoming")]
    units = mas_follow.plan(rows, {}, now=1e9)
    assert [(unit["key"], unit["german_only"]) for unit in units] == [("S01:de", True)]
    later = mas_follow.plan(rows, {"S01:de": {"at": 1e9 - 3600, "found": False}}, now=1e9)
    assert later == []


def _release(title, seeders=20, **extra):
    row = {"id": title, "title": title, "seeders": seeders, "info_hash": None, "size_bytes": 10**9,
           "resolution": direct.resolution(title), "languages": direct.languages(title), "guess": direct.guess(title)}
    return {**row, **extra}


def test_pick_prefers_german_then_quality_and_keeps_to_the_show_and_season():
    rows = [
        _release("Modern.Family.S02.1080p.WEB-DL.x264-GRP", seeders=200),
        _release("Modern.Family.S02.German.DL.720p.WEB.x264-GRP", seeders=5),
        _release("Modern.Family.S03.German.DL.1080p.WEB.x264-GRP", seeders=50),
        _release("A.Modern.Family.S02.1080p.WEB-DL", seeders=500),
        _release("Modern.Family.S01-S11.Complete.1080p", seeders=900),
        _release("Modern.Family.S02.2160p.WEB-DL", seeders=900),
    ]
    unit = {"season": 2, "episode": None, "german_only": False}
    picked = mas_follow.pick(rows, unit=unit, names=["Modern Family"], quality="1080p", skip=set())
    assert picked["title"] == "Modern.Family.S02.German.DL.720p.WEB.x264-GRP"
    english_only = [row for row in rows if "German" not in row["title"]]
    picked = mas_follow.pick(english_only, unit=unit, names=["Modern Family"], quality="1080p", skip=set())
    assert picked["title"] == "Modern.Family.S02.1080p.WEB-DL.x264-GRP"
    german_only = {**unit, "german_only": True}
    assert mas_follow.pick(english_only, unit=german_only, names=["Modern Family"], quality="1080p", skip=set()) is None


def test_pick_for_one_episode_takes_it_before_its_seasons_pack_and_skips_failed_releases():
    rows = [
        _release("The.Office.US.S03E05.1080p.WEB", seeders=10),
        _release("The.Office.US.S03.1080p.BluRay", seeders=300),
        _release("The.Office.US.S03E06.1080p.WEB", seeders=400),
    ]
    unit = {"season": 3, "episode": 5, "german_only": False}
    picked = mas_follow.pick(rows, unit=unit, names=["The Office"], quality="1080p", skip=set())
    assert picked["title"] == "The.Office.US.S03E05.1080p.WEB"
    picked = mas_follow.pick(rows, unit=unit, names=["The Office"], quality="1080p", skip={"The.Office.US.S03E05.1080p.WEB"})
    assert picked["title"] == "The.Office.US.S03.1080p.BluRay"


@pytest.fixture
def library(tmp_path, monkeypatch):
    roots = {"shows": [tmp_path / "shows", tmp_path / "shows_en"], "movies": [tmp_path / "movies", tmp_path / "movies_en"]}
    for paths in roots.values():
        for path in paths:
            path.mkdir()
    monkeypatch.setattr("bankai.web.track_cleanup.roots", lambda scope: [str(p) for p in roots[scope]])
    monkeypatch.setattr("bankai.web.track_cleanup.load_inventory", lambda: {})
    return tmp_path


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_states_read_both_folders_the_jobs_and_the_air_dates(library):
    _touch(library / "shows" / "Modern Family" / "Season 01" / "Modern Family - S01E01.mkv")
    _touch(library / "shows_en" / "Modern Family (2009)" / "Season 01" / "Modern Family - S01E02.mkv")
    _touch(library / "shows_en" / "Modern Family (2009)" / "Season 01" / "Modern Family - S01E09.mkv")
    info = {"episodes": [
        {"season": 1, "episode": n, "aired": "2009-09-23", "name": f"E{n}"} for n in (1, 2, 3, 4)
    ] + [{"season": 1, "episode": 5, "aired": "2999-01-01"}, {"season": 0, "episode": 1, "aired": "2010-01-01"}]}
    show = {"title": "Modern Family", "year": 2009, "names": []}
    active = [{"episodes": [(1, 3)]}]
    rows = mas_follow.states(show, info, active, today="2026-10-28")
    by = {(row["season"], row["episode"]): row["state"] for row in rows}
    assert by == {(1, 1): "german", (1, 2): "english", (1, 3): "downloading", (1, 4): "missing",
                  (1, 5): "upcoming", (1, 9): "english"}
    assert next(row for row in rows if row["episode"] == 9)["extra"] is True


def test_the_estimate_comes_from_releases_else_from_the_running_time():
    wanted = [_row(1, e, "missing") for e in range(1, 11)]
    releases = [
        _release("Show.S01.1080p.WEB", size_bytes=10 * 2**30),
        _release("Show.S01E02.1080p.WEB", size_bytes=1 * 2**30),
        _release("Show.S01E03.1080p.WEB", size_bytes=1 * 2**30),
        _release("Other.S01E03.1080p.WEB", size_bytes=50 * 2**30),
    ]
    size = mas_follow.estimate(releases, wanted, names=["Show"], quality="1080p", runtime=22)
    assert (size["basis"], size["per_episode"], size["bytes"]) == ("releases", 2**30, 10 * 2**30)
    size = mas_follow.estimate([], wanted, names=["Show"], quality="1080p", runtime=22)
    assert size["basis"] == "runtime"
    assert size["bytes"] == 10 * 22 * 40 * 2**20


def test_each_file_goes_by_its_audio_and_german_replaces_english(library, monkeypatch):
    english_copy = _touch(library / "shows_en" / "Show" / "Season 01" / "Show - S01E01.mkv")
    _touch(library / "shows_en" / "Show" / "Season 01" / "Show - S01E01.nfo")
    _touch(library / "shows" / "Show" / "Season 01" / "Show - S01E03.mkv")
    downloaded = library / "dl"
    sources = [_touch(downloaded / f"Show.S01E0{n}.{'GERMAN' if n in (1, 3) else 'ENG'}.mkv") for n in (1, 2, 3, 4)]
    monkeypatch.setattr(direct, "has_german", lambda path, release_title="", probe=True: "GERMAN" in path.name)
    monkeypatch.setattr(direct, "has_english_subtitles", lambda path, probe=True: True)
    by_language = {"German": direct.target(str(library / "shows"), "show"),
                   "English": direct.target(str(library / "shows_en"), "show")}
    placed, left, replaces = direct_job._by_language(
        sources, release_title="Show.S01", kind="show", by_language=by_language, title="Show", year=None,
        season=1, only={(1, 1), (1, 2), (1, 3)}, names=(),
    )
    assert [(src.name, dest) for src, dest in placed] == [
        ("Show.S01E01.GERMAN.mkv", library / "shows" / "Show" / "Season 01" / "Show - S01E01.mkv"),
        ("Show.S01E02.ENG.mkv", library / "shows_en" / "Show" / "Season 01" / "Show - S01E02.mkv"),
    ]
    assert replaces[placed[0][1]] == [english_copy]
    assert sorted(reason for _src, reason in left) == ["not one of the episodes wanted", "the library has it in German"]
    removed = direct.remove_with_sidecars(english_copy, stop=library / "shows_en")
    assert {path.suffix for path in removed} == {".mkv", ".nfo"}
    assert not (library / "shows_en" / "Show").exists()


def test_an_english_file_never_goes_beside_an_english_or_german_copy(library, monkeypatch):
    _touch(library / "shows_en" / "Show" / "Show - S01E01.mkv")
    source = _touch(library / "dl" / "Show.S01E01.mkv")
    monkeypatch.setattr(direct, "has_german", lambda path, release_title="", probe=True: False)
    monkeypatch.setattr(direct, "has_english_subtitles", lambda path, probe=True: True)
    by_language = {"German": direct.target(str(library / "shows"), "show"),
                   "English": direct.target(str(library / "shows_en"), "show")}
    placed, left, _replaces = direct_job._by_language(
        [source], release_title="x", kind="show", by_language=by_language, title="Show", year=None,
        season=None, only=None, names=(),
    )
    assert placed == []
    assert left[0][1] == "the library has it in English"


def test_following_needs_a_folder_per_language(library, monkeypatch):
    monkeypatch.setattr(mas_follow, "_path", lambda: library / "mas_follow.json")
    with pytest.raises(ValueError, match="English"):
        mas_follow.follow(1, title="Show", year=None, quality="1080p", roots={"German": str(library / "shows")}, names=[])
    row = mas_follow.follow(1, title="Show", year=2009, quality="1080p",
                            roots={"German": str(library / "shows"), "English": str(library / "shows_en")}, names=["Show", "Die Show"])
    assert row["names"] == ["Die Show"]
    assert mas_follow.get(1)["roots"]["English"] == str(library / "shows_en")
    assert mas_follow.unfollow(1) and mas_follow.get(1) is None


def test_a_prowlarr_http_magnet_link_is_fetched_here_not_handed_to_qbittorrent(monkeypatch):
    import asyncio

    import bankai.torrent.worker as worker

    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return "magnet:?xt=urn:btih:" + "a" * 40, None

    monkeypatch.setattr(worker, "fetch_torrent", fake_fetch)
    proxy = "http://127.0.0.1:9696/1/download?link=x"
    magnet, _data, info_hash = asyncio.run(direct_job._link(proxy, "https://knaben.xyz/description.php?id=1"))
    assert fetched == [proxy] and magnet.startswith("magnet:") and info_hash == "a" * 40
    real = "magnet:?xt=urn:btih:" + "b" * 40
    assert asyncio.run(direct_job._link(real, None))[0] == real
    assert len(fetched) == 1


def test_an_english_season_pack_below_the_quality_chosen_gives_way_to_single_episodes():
    rows = [_release("Modern Family 2009 S08 720p AMZN WEBRip x264", seeders=65),
            _release("Modern.Family.S08E03.1080p.HEVC.x265-MeGusta", seeders=33)]
    season = {"season": 8, "episode": None, "german_only": False}
    assert mas_follow.pick(rows, unit=season, names=["Modern Family"], quality="1080p", skip=set()) is None
    german = [_release("Modern.Family.S08.German.DL.720p.WEB.x264-GRP", seeders=5)]
    assert mas_follow.pick(german, unit=season, names=["Modern Family"], quality="1080p", skip=set())["title"].startswith("Modern.Family.S08.German")
    one = {"season": 8, "episode": 3, "german_only": False}
    assert mas_follow.pick(rows, unit=one, names=["Modern Family"], quality="1080p", skip=set())["title"].endswith("MeGusta")


def test_an_english_file_replaces_an_english_copy_only_with_a_better_picture(library, monkeypatch):
    old = _touch(library / "shows_en" / "Show" / "Season 08" / "Show - S08E01.mkv")
    _touch(library / "shows_en" / "Show" / "Season 08" / "Show - S08E02.mkv")
    sources = [_touch(library / "dl" / f"Show.S08E0{n}.1080p.mkv") for n in (1, 2)]
    monkeypatch.setattr(direct, "has_german", lambda path, release_title="", probe=True: False)
    # E01 is 720p on disk, E02 already 1080p.
    ranks = {old: 1, library / "shows_en" / "Show" / "Season 08" / "Show - S08E02.mkv": 2}
    monkeypatch.setattr(direct, "picture_rank", lambda path, probe=True: ranks.get(path, 2))
    monkeypatch.setattr(direct, "has_english_subtitles", lambda path, probe=True: True)
    by_language = {"German": direct.target(str(library / "shows"), "show"),
                   "English": direct.target(str(library / "shows_en"), "show")}
    placed, left, replaces = direct_job._by_language(
        sources, release_title="Show.S08.1080p", kind="show", by_language=by_language, title="Show", year=None,
        season=8, only=None, names=(),
    )
    assert [src.name for src, _dest in placed] == ["Show.S08E01.1080p.mkv"]
    assert replaces[placed[0][1]] == [old]
    assert [reason for _src, reason in left] == ["the library has it in English"]



def test_an_english_file_without_english_subtitles_is_not_filed_and_a_copy_without_them_gives_way(library, monkeypatch):
    lacking = _touch(library / "shows_en" / "Show" / "Season 01" / "Show - S01E01.mkv")
    sources = [_touch(library / "dl" / f"Show.S01E0{n}.1080p.mkv") for n in (1, 2)]
    monkeypatch.setattr(direct, "has_german", lambda path, release_title="", probe=True: False)
    monkeypatch.setattr(direct, "picture_rank", lambda path, probe=True: 2)
    # The download's E02 has no English subtitles; the library's E01 has none either, the download's has.
    monkeypatch.setattr(direct, "has_english_subtitles", lambda path, probe=True: path.name != "Show.S01E02.1080p.mkv" and path != lacking)
    by_language = {"German": direct.target(str(library / "shows"), "show"),
                   "English": direct.target(str(library / "shows_en"), "show")}
    placed, left, replaces = direct_job._by_language(
        sources, release_title="Show.S01.1080p", kind="show", by_language=by_language, title="Show", year=None,
        season=1, only=None, names=(),
    )
    assert [src.name for src, _dest in placed] == ["Show.S01E01.1080p.mkv"]
    assert replaces[placed[0][1]] == [lacking]
    assert [(src.name, reason) for src, reason in left] == [("Show.S01E02.1080p.mkv", direct_job.NO_ENGLISH_SUBTITLES)]


def test_english_subtitles_are_found_beside_the_video_or_in_a_subs_folder(tmp_path):
    video = _touch(tmp_path / "Show.S01E01.[UTR].mkv")
    assert direct.english_subtitle_file(video) is None
    _touch(tmp_path / "Show.S01E01.[UTR].de.srt")
    assert direct.english_subtitle_file(video) is None
    side = _touch(tmp_path / "Show.S01E01.[UTR].en.srt")
    assert direct.english_subtitle_file(video) == side
    other = _touch(tmp_path / "b" / "Film.mkv")
    _touch(tmp_path / "b" / "Subs" / "Film" / "3_English.srt")
    full = _touch(tmp_path / "b" / "Subs" / "Film" / "2_English.srt")
    _touch(tmp_path / "b" / "Subs" / "Film" / "1_English_forced.srt")
    assert direct.english_subtitle_file(other) == full


def test_an_english_episode_without_english_subtitles_is_searched_for_again():
    rows = [{**_row(1, e, "english"), "english_subs": False} for e in range(1, 5)]
    units = mas_follow.plan(rows, {}, now=1e9)
    assert [(unit["key"], unit["german_only"]) for unit in units] == [("S01", False)]
    rows = [{**_row(1, e, "english"), "english_subs": True} for e in range(1, 5)]
    assert [unit["key"] for unit in mas_follow.plan(rows, {}, now=1e9)] == ["S01:de"]


def test_files_picked_to_be_replaced_go_whatever_the_library_holds_of_their_episode(library, monkeypatch):
    german = _touch(library / "shows" / "Show" / "Season 01" / "Show - S01E01.mkv")
    english = _touch(library / "shows_en" / "Show" / "Season 01" / "Show - S01E02.mkv")
    sources = [_touch(library / "dl" / f"Show.S01E0{n}.mkv") for n in (1, 2, 3)]
    monkeypatch.setattr(direct, "has_german", lambda path, release_title="", probe=True: "E01" in path.name)
    monkeypatch.setattr(direct, "has_english_subtitles", lambda path, probe=True: True)
    monkeypatch.setattr(direct, "picture_rank", lambda path, probe=True: 2)
    by_language = {"German": direct.target(str(library / "shows"), "show"),
                   "English": direct.target(str(library / "shows_en"), "show")}
    placed, left, replaces = direct_job._by_language(
        sources, release_title="Show.S01", kind="show", by_language=by_language, title="Show", year=None,
        season=1, only={(1, 1), (1, 2)}, names=(), replace_paths=(german, english),
    )
    assert {src.name: replaces[dest] for src, dest in placed} == {"Show.S01E01.mkv": [german], "Show.S01E02.mkv": [english]}
    assert [reason for _src, reason in left] == ["not one of the episodes wanted"]


def test_a_release_whose_download_was_only_cut_off_may_be_picked_again():
    assert mas_follow._passing({"reason": None})  # a restart: no error logged
    assert mas_follow._passing({"reason": "ReadTimeout: "})
    assert not mas_follow._passing({"reason": "RuntimeError: no file of the torrent could be filed"})


class _SeedingQbit:
    """qBittorrent with torrents already there, each finished: (hash, category, completed at)."""

    def __init__(self, rows):
        from bankai.torrent.qbittorrent import TorrentStatus

        self.rows = [
            (TorrentStatus(hash=h, name=h, state="stalledUP", progress=1.0, save_path="", content_path=content,
                           size_bytes=1, dlspeed=0, eta=0, completion_on=done), category)
            for h, category, done, content in rows
        ]
        self.removed = []

    async def login(self):
        pass

    async def aclose(self):
        pass

    async def list_torrents(self, *, category=None):
        return [row for row, cat in self.rows if category is None or cat == category]

    async def add(self, **kw):
        pass

    async def top_priority(self, h):
        pass

    async def resume(self, h):
        pass

    async def force_start(self, h, enabled=True):
        pass

    async def wait_until_complete(self, h, **kw):
        return next(row for row, _cat in self.rows if row.hash == h)

    async def remove(self, h, delete_files=False):
        self.removed.append(h)


def test_a_finished_torrent_an_earlier_attempt_added_goes_once_the_library_has_its_episode(tmp_path, monkeypatch):
    import asyncio

    root = tmp_path / "shows_en"
    episode = root / "Show (2009)" / "Season 01" / "Show - S01E16.mkv"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"there")
    download = tmp_path / "qbit" / "Show.S01E16.720p"
    download.mkdir(parents=True)
    (download / "Show.S01E16.720p.mkv").write_bytes(b"x" * 10)
    qbit = _SeedingQbit([("aa" * 20, direct_job.CATEGORY, 1, str(download))])
    monkeypatch.setattr(direct_job, "QBittorrentClient", lambda: qbit)
    monkeypatch.setattr(direct_job.direct, "target", lambda path, kind: {"root": str(root), "label": "Shows · English"})
    asyncio.run(direct_job.download_direct(
        release_title="Show.S01E16.720p", download_url=None, magnet_uri="magnet:?xt=urn:btih:" + "aa" * 20,
        info_hash="aa" * 20, info_url=None, media_kind="show", root=str(root), title="Show", year=2009, season=1,
    ))
    # It was in qBittorrent before this job (an earlier attempt's), but in bankai's category.
    assert qbit.removed == ["aa" * 20] and episode.read_bytes() == b"there"


def test_the_sweep_removes_finished_torrents_no_running_job_reads(monkeypatch):
    import asyncio

    from bankai.cli import bgjobs
    from bankai.torrent import actions

    now = 100_000.0
    qbit = _SeedingQbit([
        ("old", direct_job.CATEGORY, now - 3 * 3600, ""),
        ("fresh", direct_job.CATEGORY, now - 600, ""),
        ("read", direct_job.CATEGORY, now - 3 * 3600, ""),
        ("anime", "bankai", now - 3 * 3600, ""),
    ])

    class Job:
        id, status, args = "j1", "running", ["direct-download", "--release-title", "x"]

    monkeypatch.setattr(bgjobs, "list_jobs", lambda: [Job()])
    monkeypatch.setattr(actions, "get_active_torrent", lambda job_id: "read")
    assert asyncio.run(direct_job.sweep(qbit, now=now)) == ["old"]
    assert qbit.removed == ["old"]
