"""A failed publish must not abandon a finished download."""

from __future__ import annotations

import time
from dataclasses import dataclass

from bankai.web import erai


@dataclass
class FakeTorrent:
    progress: float = 1.0


def test_a_finished_download_whose_publish_failed_is_retried():
    """This is the state eighty-eight releases were stuck in.

    The download had completed, publishing failed, and nothing would ever
    look at it again -- while the torrent kept its space on a disk that was
    already too full for the next publish to succeed.
    """
    assert erai._worth_republishing({}, FakeTorrent()) is True


def test_an_unfinished_download_is_left_to_finish():
    assert erai._worth_republishing({}, FakeTorrent(progress=0.4)) is False


def test_a_release_whose_torrent_is_gone_is_not_retried():
    """Without the bytes there is nothing to republish from."""
    assert erai._worth_republishing({}, None) is False


def test_retries_stop_after_a_few_attempts():
    """A release that genuinely cannot publish gives up rather than cycling."""
    release = {"publish_attempts": erai._MAX_PUBLISH_ATTEMPTS}
    assert erai._worth_republishing(release, FakeTorrent()) is False
    release = {"publish_attempts": erai._MAX_PUBLISH_ATTEMPTS - 1}
    assert erai._worth_republishing(release, FakeTorrent()) is True


def test_a_retry_waits_for_its_backoff():
    """Otherwise a failing release is retried every twenty seconds forever."""
    assert erai._worth_republishing(
        {"publish_retry_after": time.time() + 600}, FakeTorrent()
    ) is False
    assert erai._worth_republishing(
        {"publish_retry_after": time.time() - 1}, FakeTorrent()
    ) is True


def test_the_backoff_widens_with_each_attempt():
    """Five attempts spread over hours, not five in a minute."""
    delays = [
        erai._PUBLISH_BACKOFF_SECONDS * (2 ** (attempt - 1))
        for attempt in range(1, erai._MAX_PUBLISH_ATTEMPTS + 1)
    ]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]
    # The whole allowance spans hours rather than minutes.
    assert sum(delays) > 4 * 3600
