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
    "--anidb-id",
    "2369",
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
        release_status: str = "held",
        enqueue_result: dict | None = None,
    ) -> dict:
        from bankai.metadata import anidb

        entry = anidb.AniDBAnime(aid=2369, title="Bleach", english_title="Bleach", titles=("Bleach",))

        async def known(aid: int):
            return entry if aid == entry.aid else None

        monkeypatch.setattr(app_mod.bgjobs_mod, "get_job", lambda _id: job)
        monkeypatch.setattr(anidb, "anime", known)
        monkeypatch.setattr(erai_mod, "_anidb_episode_on_disk", lambda *a, **k: on_disk)
        monkeypatch.setattr(
            erai_mod,
            "_load_state",
            lambda: {"releases": {"a" * 40: {"status": release_status}}},
        )

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


def _bleach_index():
    from xml.etree import ElementTree as ET

    from bankai.metadata import anidb

    return anidb.build_index(
        ET.fromstring(
            """<animetitles>
  <anime aid="15449"><title xml:lang="x-jat" type="main">Bleach: Sennen Kessen Hen</title></anime>
  <anime aid="17765"><title xml:lang="x-jat" type="main">Bleach: Sennen Kessen Hen - Ketsubetsu Tan</title></anime>
</animetitles>"""
        ),
        ET.fromstring(
            """<anime-list>
  <anime anidbid="15449" tvdbid="74796" defaulttvdbseason="17" episodeoffset="0"/>
  <anime anidbid="17765" tvdbid="74796" defaulttvdbseason="17" episodeoffset="13"/>
</anime-list>"""
        ),
    )


def test_a_job_from_the_tvdb_era_is_retried_as_its_anidb_episode(client, retry, monkeypatch):
    """Failed jobs keep the arguments they were queued with, TVDB ones included.

    Season 17 episode 15 is where Anime-Lists says the second cour starts plus
    two: AniDB entry 17765, episode 2.
    """
    from bankai.metadata import anidb

    table = _bleach_index()

    async def index():
        return table

    job = FakeJob(
        ["anime-download", "--info-hash", "a" * 40, "--kind", "show", "--tvdb-id", "74796",
         "--english-title", "Bleach", "--season", "17", "--episode", "15", "--year", "2001"]
    )
    calls = retry(job)
    monkeypatch.setattr(anidb, "index", index)

    body = client.post("/api/queue/job1/retry").json()

    assert body["status"] == "queued"
    args = calls["enqueued"][0]["args"]
    assert "--tvdb-id" not in args and "--season" not in args and "--year" not in args
    assert args[args.index("--anidb-id") + 1] == "17765"
    assert args[args.index("--episode") + 1] == "2"


def test_a_tvdb_job_anidb_cannot_place_is_refused_and_kept(client, retry, monkeypatch):
    from bankai.metadata import anidb

    table = _bleach_index()

    async def index():
        return table

    job = FakeJob(
        ["anime-download", "--info-hash", "a" * 40, "--kind", "show", "--tvdb-id", "81797",
         "--english-title", "One Piece", "--season", "22", "--episode", "5"]
    )
    calls = retry(job)
    monkeypatch.setattr(anidb, "index", index)

    response = client.post("/api/queue/job1/retry")

    assert response.status_code == 409
    assert "Anime Search" in response.json()["detail"]
    assert calls["enqueued"] == []
    assert job.deleted is False
