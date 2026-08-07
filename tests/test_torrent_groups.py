from __future__ import annotations

from pathlib import Path

from bankai.torrent import groups


def test_episode_group_retains_pack_until_every_distinct_member_finishes(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(groups, "_root", lambda: tmp_path / "groups")

    selected = {"title": "Show S02 COMPLETE", "info_hash": "AAA"}
    assert groups.remember_candidate("batch-1", selected) == selected
    assert groups.remember_candidate("batch-1", {"title": "other"}) == selected
    assert groups.get_candidate("batch-1") == selected

    first = groups.mark_complete(
        "batch-1", member_id="S02E01", expected=3, torrent_hash="AAA"
    )
    duplicate = groups.mark_complete(
        "batch-1", member_id="S02E01", expected=3, torrent_hash="AAA"
    )
    second = groups.mark_complete(
        "batch-1", member_id="S02E02", expected=3, torrent_hash="BBB"
    )
    final = groups.mark_complete(
        "batch-1", member_id="S02E03", expected=3, torrent_hash="AAA"
    )

    assert first.cleanup is False
    assert duplicate.completed == 1
    assert second.cleanup is False
    assert final.cleanup is True
    assert final.torrent_hashes == ("aaa", "bbb")

    groups.finish_cleanup("batch-1", member_id="S02E03", success=True)
    assert not (tmp_path / "groups" / "batch-1.json").exists()
