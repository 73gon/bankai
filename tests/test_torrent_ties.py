"""Every torrent of bankai's anime category is some job's until it is filed (web/torrent_ties)."""

import asyncio
from typing import ClassVar

from bankai.web import erai, torrent_ties
from tests.test_erai import _run_reconcile

NOW = 1_000_000.0
AID = 163139


class _Done:
    """A torrent qBittorrent has: finished at ``done`` unless ``progress`` says otherwise."""

    def __init__(self, info_hash, *, done=NOW - 3600, progress=1.0, name=None):
        self.hash = info_hash
        self.name = name or f"[Erai-raws] Boku no Hero Academia 7th Season - 05 [{info_hash[:6]}]"
        self.progress = progress
        self.state = "stalledUP" if progress >= 1 else "stoppedDL"
        self.size_bytes = 1024**3
        self.completion_on = done if progress >= 1 else 0
        self.added_on = done - 600


def _plan(torrents, releases, *, owned=(), library=None, blocked=(), cleanup=True):
    return torrent_ties.plan(
        torrents,
        releases,
        set(owned),
        identify=lambda title, release: [(AID, int(release["canonical"].split("|")[1]))] if release.get("canonical") else None,
        in_library=lambda aids: {aid: set((library or {}).get(aid, ())) for aid in aids},
        blocked=set(blocked),
        cleanup=cleanup,
        now=NOW,
    )


def _duplicate(episode):
    return {"status": "duplicate", "title": f"[Erai-raws] Boku no Hero Academia 7th Season - {episode:02d}", "canonical": f"anilist:{AID}|{episode}"}


def test_a_finished_torrent_no_job_has_and_the_library_lacks_is_taken_up():
    ties = _plan([_Done("a" * 40)], {"a" * 40: _duplicate(5)})
    assert [(tie.action, tie.pairs) for tie in ties] == [("adopt", ((AID, 5),))]


def test_one_a_job_reads_or_the_reconciler_drives_is_left_to_them():
    torrents = [_Done("a" * 40), _Done("b" * 40)]
    releases = {"a" * 40: _duplicate(5), "b" * 40: {**_duplicate(6), "status": "transferring"}}
    assert _plan(torrents, releases, owned={"a" * 40}) == []


def test_it_waits_a_while_after_the_download_ends_before_taking_it_up():
    assert _plan([_Done("a" * 40, done=NOW - 60)], {"a" * 40: _duplicate(5)}) == []


def test_one_whose_episodes_are_all_in_the_library_goes_once_it_has_seeded_a_while():
    library = {AID: {5}}
    assert [tie.action for tie in _plan([_Done("a" * 40, done=NOW - 3 * 3600)], {"a" * 40: _duplicate(5)}, library=library)] == ["remove"]
    assert _plan([_Done("a" * 40, done=NOW - 1800)], {"a" * 40: _duplicate(5)}, library=library) == []
    # Without cleanup after success a torrent stays, as a job would have left it.
    assert _plan([_Done("a" * 40, done=NOW - 3 * 3600)], {"a" * 40: _duplicate(5)}, library=library, cleanup=False) == []


def test_a_blacklisted_show_a_decided_release_or_an_unfinished_download_is_only_counted():
    torrents = [_Done("a" * 40), _Done("b" * 40), _Done("c" * 40, progress=0.4), _Done("d" * 40, name="Spider-Man.2026.1080p")]
    releases = {"a" * 40: _duplicate(5), "b" * 40: {**_duplicate(6), "status": "filtered"}, "c" * 40: _duplicate(7)}
    ties = _plan(torrents, releases, blocked={AID})
    assert [(tie.info_hash[0], tie.action, tie.why) for tie in ties] == [
        ("a", "leave", "the show is blacklisted"),
        ("b", "leave", "its release is filtered"),
        ("c", "leave", "not downloaded, and no job is waiting for it"),
        ("d", "leave", "its name says no episode"),
    ]


def test_the_reconciler_files_an_orphan_once_and_not_again_on_the_next_pass(monkeypatch, tmp_path):
    info_hash = "0202d6f0977031a8dab918f22ec8a1eed0350584"
    releases = {info_hash: _duplicate(5)}
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai.anilist, "cached_index", lambda: None)
    monkeypatch.setattr("bankai.web.anime_collections.entry_name", lambda aid: "My Hero Academia Season 7")
    monkeypatch.setattr("bankai.web.anime_files.in_library", lambda aids: {})
    monkeypatch.setattr(torrent_ties, "job_hashes", lambda *a, **k: set())
    torrent = _Done(info_hash, done=1.0)  # finished long ago

    state, counts, qbit, spawned = _run_reconcile(monkeypatch, tmp_path, releases, [torrent])
    assert counts.get("adopted") == 1 and len(spawned) == 1
    args = spawned[0]["args"]
    assert args[0] == "anime-download" and "--publish" in args and "--require-german-subtitles" not in args
    assert args[args.index("--info-hash") + 1] == info_hash
    assert (args[args.index("--anilist-id") + 1], args[args.index("--episode") + 1]) == (str(AID), "5")
    release = state["releases"][info_hash]
    assert release["status"] == "transferring" and release["job_id"] == "job1"
    assert qbit.removed == []

    class _Running:
        status = "running"

    # The next pass: the release is the reconciler's now, its job running -- nothing new.
    state2, counts2, _qbit2, spawned2 = _run_reconcile(
        monkeypatch, tmp_path, state["releases"], [torrent], jobs={"job1": _Running()}
    )
    assert spawned2 == [] and not counts2.get("adopted")
    assert state2["releases"][info_hash]["status"] == "transferring"


def test_a_failed_job_whose_release_reads_duplicate_is_not_tidied_away(monkeypatch):
    from bankai.cli import bgjobs
    from bankai.web import anime_files, jobs

    class _Failed:
        id, status, kind, title, finished_at = "f1", "failed", "show", "My Hero Academia Season 7 - 05", NOW - 7200
        args: ClassVar[list[str]] = ["anime-download", "--info-hash", "a" * 40, "--anilist-id", str(AID), "--episode", "5"]

        def delete(self):
            raise AssertionError("its torrent would be left with nobody to file it")

    monkeypatch.setattr(bgjobs, "list_jobs", lambda: [_Failed()])
    # A failure that is not run again by itself: the job stays in the queue for a look.
    monkeypatch.setattr(bgjobs, "failure_reason", lambda job: "the selected Nyaa release contained no recognizable anime files")
    monkeypatch.setattr(anime_files, "by_entry", lambda: {})
    monkeypatch.setattr(erai, "_load_state", lambda: {"releases": {"a" * 40: _duplicate(5)}})
    monkeypatch.setattr(erai, "_policy_anilist_ids", lambda: set())
    assert jobs.tidy_failed_anime(now=NOW) == {"removed": 0, "retried": 0, "kept": 1}


def test_deleting_a_job_takes_its_torrent_unless_something_else_reads_it(monkeypatch):
    from bankai.cli import bgjobs
    from bankai.torrent import actions

    class _Qbit:
        removed: ClassVar[list] = []

        async def login(self):
            pass

        async def list_torrents(self, *, category=None):
            return [_Done("a" * 40), _Done("b" * 40)] if category == "bankai" else []

        async def remove(self, info_hash, *, delete_files=False):
            _Qbit.removed.append((info_hash, delete_files))

        async def aclose(self):
            pass

    class _Other:
        id, status = "other", "failed"
        args: ClassVar[list[str]] = ["anime-download", "--info-hash", "b" * 40]

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: _Qbit())
    monkeypatch.setattr(bgjobs, "list_jobs", lambda: [_Other()])
    monkeypatch.setattr(actions, "get_active_torrent", lambda job_id: None)
    monkeypatch.setattr(erai, "state_for_reading", lambda: {"releases": {}})
    monkeypatch.setattr("bankai.web.jobs._load_pending", lambda: [])
    gone = asyncio.run(torrent_ties.drop_torrent_of(["anime-download", "--info-hash", "a" * 40], job_id="mine"))
    kept = asyncio.run(torrent_ties.drop_torrent_of(["anime-download", "--info-hash", "b" * 40], job_id="mine"))
    assert gone and kept is None
    assert _Qbit.removed == [("a" * 40, True)]


def test_the_direct_sweep_removes_an_unfinished_torrent_no_job_waits_for(monkeypatch):
    from bankai.cli import bgjobs
    from bankai.processor import direct
    from bankai.torrent import actions

    class _Qbit:
        def __init__(self):
            self.removed = []

        async def login(self):
            pass

        async def list_torrents(self, *, category=None):
            return [_Done("old" + "0" * 37, progress=0.02, done=NOW - 5 * 3600), _Done("new" + "0" * 37, progress=0.0, done=NOW - 600)]

        async def remove(self, info_hash, *, delete_files=False):
            self.removed.append(info_hash)

        async def aclose(self):
            pass

    qbit = _Qbit()
    monkeypatch.setattr(bgjobs, "list_jobs", lambda: [])
    monkeypatch.setattr(actions, "get_active_torrent", lambda job_id: None)
    assert len(asyncio.run(direct.sweep(qbit, now=NOW))) == 1
    assert qbit.removed == ["old" + "0" * 37]
