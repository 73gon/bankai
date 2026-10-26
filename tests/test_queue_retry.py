"""Retrying a failed job replaces it rather than joining it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankai.config import reset_settings_cache
from bankai.web.app import create_app


class FakeJob:
    """Just enough of a background job to be retried."""

    def __init__(self, args: list[str], kind: str = "anime", title: str = "Bleach - 47"):
        self.id = "job1"
        self.kind = kind
        self.title = title
        self.args = args
        self.status = "failed"
        self.deleted = False

    def delete(self) -> bool:
        self.deleted = True
        return True


ANIME_ARGS = [
    "anime-download",
    "--info-hash",
    "a" * 40,
    "--anilist-id",
    "269",
    "--english-title",
    "Bleach",
    "--episode",
    "47",
]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    reset_settings_cache()
    with TestClient(create_app()) as value:
        yield value
    reset_settings_cache()


@pytest.fixture()
def retry(monkeypatch: pytest.MonkeyPatch):
    """Wire a failed job up to the retry endpoint and record what happens."""
    from bankai.web import app as app_mod
    from bankai.web import erai as erai_mod

    calls: dict = {"enqueued": []}

    def arrange(
        job: FakeJob,
        *,
        on_disk: bool = False,
        release_status: str | None = "held",
        enqueue_result: dict | None = None,
        in_qbittorrent: bool = False,
    ) -> dict:
        monkeypatch.setattr(app_mod.bgjobs_mod, "get_job", lambda _id: job)
        monkeypatch.setattr(erai_mod, "_episode_on_disk", lambda *a, **k: on_disk)
        releases = {} if release_status is None else {"a" * 40: {"status": release_status}}
        monkeypatch.setattr(erai_mod, "_load_state", lambda: {"releases": releases})

        class Qbit:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def list_torrents(self, **_kwargs):
                from types import SimpleNamespace

                return [SimpleNamespace(hash="a" * 40)] if in_qbittorrent else []

        monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", Qbit)

        def enqueue(**kwargs):
            calls["enqueued"].append(kwargs)
            return enqueue_result or {"status": "queued", "id": "new", "title": kwargs["title"]}

        monkeypatch.setattr(app_mod.webjobs, "enqueue", enqueue)
        return calls

    return arrange


def test_a_retried_job_replaces_the_row_it_came_from(client, retry):
    """Three attempts at one episode used to leave three rows behind."""
    job = FakeJob(ANIME_ARGS)
    calls = retry(job)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "queued"
    assert len(calls["enqueued"]) == 1
    # The failure is gone, so the episode is one row again.
    assert job.deleted is True


def test_an_episode_already_in_the_library_is_not_downloaded_again(client, retry):
    job = FakeJob(ANIME_ARGS)
    calls = retry(job, on_disk=True)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "resolved"
    assert "already in the library" in body["detail"]
    assert calls["enqueued"] == []
    assert job.deleted is True


@pytest.mark.parametrize("status", ["queued", "downloading", "transferring", "deleting"])
def test_a_release_already_moving_is_not_started_a_second_time(client, retry, status):
    """The queued row beside two failures was the pipeline already working."""
    job = FakeJob(ANIME_ARGS)
    calls = retry(job, release_status=status)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "resolved"
    assert status in body["detail"]
    assert calls["enqueued"] == []
    assert job.deleted is True


def test_a_torrent_the_automation_follows_is_left_to_it(client, retry):
    job = FakeJob(ANIME_ARGS)
    calls = retry(job, in_qbittorrent=True)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "resolved"
    assert calls["enqueued"] == []


def test_a_hand_picked_download_still_in_qbittorrent_is_run_again(client, retry):
    """No reconciler files a torrent picked by hand: dropping its retry left a finished batch unfiled."""
    job = FakeJob(ANIME_ARGS)
    calls = retry(job, release_status=None, in_qbittorrent=True)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "queued"
    assert len(calls["enqueued"]) == 1


def test_a_duplicate_still_clears_the_failure(client, retry):
    """Refusing to queue twice is a reason to drop the failed row, not keep it."""
    job = FakeJob(ANIME_ARGS)
    retry(job, enqueue_result={"status": "duplicate", "title": "Bleach S17E47"})

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "duplicate"
    assert job.deleted is True


def test_a_job_that_will_not_queue_keeps_its_row(client, retry):
    """Nothing replaced it, so removing it would lose the failure entirely."""
    job = FakeJob(ANIME_ARGS)
    retry(job, enqueue_result={"status": "paused", "title": "Bleach S17E47"})

    client.post("/api/queue/job1/retry")

    assert job.deleted is False


def test_a_non_anime_job_is_retried_without_the_anime_checks(client, retry):
    job = FakeJob(["movie", "--title", "Arrival"], kind="movie", title="Arrival")
    calls = retry(job, on_disk=True)

    body = client.post("/api/queue/job1/retry").json()

    # on_disk is about anime episodes and must not intercept a movie.
    assert body["status"] == "queued"
    assert len(calls["enqueued"]) == 1
    assert job.deleted is True


def test_retrying_something_that_is_gone_is_a_404(client, monkeypatch):
    from bankai.web import app as app_mod

    monkeypatch.setattr(app_mod.bgjobs_mod, "get_job", lambda _id: None)
    assert client.post("/api/queue/nope/retry").status_code == 404


def _tybw(anilist_data) -> None:
    """Bleach: Thousand-Year Blood War's first two cours on AniList, and the AniDB entries that were them."""
    anilist_data.add(
        116674, "Bleach: Sennen Kessen-hen", episodes=13, start="2022-10-11",
        relations=[(159322, "SEQUEL")], anidb=[15449],
    )
    anilist_data.add(
        159322, "Bleach: Sennen Kessen-hen - Ketsubetsu-tan", episodes=13, start="2023-07-08",
        relations=[(116674, "PREQUEL")], anidb=[17765],
    )


def test_a_job_from_the_anidb_era_is_retried_as_its_anilist_episode(client, retry, anilist_data):
    """Failed jobs keep the arguments they were queued with, AniDB ones included."""
    _tybw(anilist_data)
    job = FakeJob(
        ["anime-download", "--info-hash", "a" * 40, "--kind", "show", "--anidb-id", "17765",
         "--anidb-title", "Bleach: Sennen Kessen Hen - Ketsubetsu Tan", "--english-title", "Bleach",
         "--episode", "2"]
    )
    calls = retry(job)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "queued"
    args = calls["enqueued"][0]["args"]
    assert "--anidb-id" not in args and "--anidb-title" not in args
    assert args[args.index("--anilist-id") + 1] == "159322"
    assert args[args.index("--episode") + 1] == "2"


def test_an_anidb_episode_past_the_entry_s_end_is_retried_in_its_sequel(client, retry, anilist_data):
    """AniDB kept the first two cours as one entry where AniList has two: episode 15 is the second's 2."""
    _tybw(anilist_data)
    job = FakeJob(
        ["anime-download", "--info-hash", "a" * 40, "--kind", "show", "--anidb-id", "15449",
         "--english-title", "Bleach", "--episode", "15"]
    )
    calls = retry(job)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "queued"
    args = calls["enqueued"][0]["args"]
    assert args[args.index("--anilist-id") + 1] == "159322"
    assert args[args.index("--episode") + 1] == "2"


def test_a_tvdb_job_is_refused_and_kept(client, retry):
    job = FakeJob(
        ["anime-download", "--info-hash", "a" * 40, "--kind", "show", "--tvdb-id", "81797",
         "--english-title", "One Piece", "--season", "22", "--episode", "5"]
    )
    calls = retry(job)

    response = client.post("/api/queue/job1/retry")

    assert response.status_code == 409
    assert "Anime Search" in response.json()["detail"]
    assert calls["enqueued"] == []
    assert job.deleted is False


def test_removing_a_waiting_release_takes_it_and_its_torrent_out_for_good(client, monkeypatch):
    """The anime queue's waiting rows are releases: their bin answered "job not found"."""
    from bankai.web import app as app_mod
    from bankai.web import erai as erai_mod

    state = {"releases": {"ab" * 20: {"status": "queued", "title": "[Erai-raws] Dr. Stone - 03"}}}
    saved: list[dict] = []
    removed: list[tuple[str, bool]] = []
    monkeypatch.setattr(app_mod.bgjobs_mod, "get_job", lambda _id: None)
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda _id: None)
    monkeypatch.setattr(erai_mod, "_load_state", lambda: state)
    monkeypatch.setattr(erai_mod, "_save_state", lambda value: saved.append(value))

    class Qbit:
        async def login(self): ...

        async def list_torrents(self, **_kwargs):
            from types import SimpleNamespace

            return [SimpleNamespace(hash="ab" * 20)]

        async def remove(self, info_hash, *, delete_files=False):
            removed.append((info_hash, delete_files))

        async def aclose(self): ...

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", Qbit)

    body = client.delete("/api/queue/abababab").json()

    assert body["deleted"] is True and body["torrent_removed"] is True
    assert removed == [("ab" * 20, True)]
    # Kept as removed: the next cycle must not find it new and queue it again.
    assert state["releases"]["ab" * 20]["status"] == "removed"
    assert not erai_mod._needs_consideration(state, type("Entry", (), {"title": "[Erai-raws] Dr. Stone - 03", "info_hash": "ab" * 20})())
    assert client.delete("/api/queue/abababab").status_code == 404
