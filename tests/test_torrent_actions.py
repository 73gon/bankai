from __future__ import annotations

from pathlib import Path

import pytest

from bankai.torrent import actions
from bankai.torrent.prowlarr import TorrentCandidate


def _candidate(title: str, seeders: int) -> TorrentCandidate:
    return TorrentCandidate(
        title=title,
        indexer="test",
        indexer_id=1,
        download_url=f"https://example.test/{seeders}.torrent",
        info_url=None,
        magnet_uri=None,
        info_hash=f"{seeders:040x}",
        size_bytes=5 * 1024**3,
        seeders=seeders,
        leechers=0,
        publish_date=None,
    )


def test_torrent_action_persists_candidates_and_explicit_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(actions, "_root", lambda: tmp_path)
    first = _candidate("Movie.2024.1080p.WEB-DL-A", 12)
    second = _candidate("Movie.2024.1080p.BluRay-B", 4)

    created = actions.create_request(
        "job1",
        query="Movie 2024",
        candidates=[second, first],
        eligible_ids=set(),
        target_runtime_seconds=7_740,
    )

    assert created["status"] == "waiting"
    assert [row["seeders"] for row in created["candidates"]] == [12, 4]
    selected = actions.choose("job1", created["candidates"][1]["id"])
    assert selected is not None
    assert selected["title"] == second.title
    assert actions.get_request("job1")["status"] == "selected"  # type: ignore[index]
