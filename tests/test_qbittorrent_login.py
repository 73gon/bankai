"""qBittorrent changed what a successful login looks like in 5.1."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from bankai.config import QBittorrentSettings
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError, login_succeeded


@pytest.mark.parametrize(
    ("status", "body", "ok"),
    [
        (200, "Ok.", True),  # up to 5.0
        (204, "", True),  # 5.1 onwards; the container on seireitei runs 5.2
        (200, "Fails.", False),  # wrong password, old
        (401, "Unauthorized", False),  # wrong password, new
        (403, "Forbidden", False),  # banned after too many attempts
    ],
)
def test_both_generations_of_login_answer(status, body, ok):
    assert login_succeeded(httpx.Response(status, text=body)) is ok


def _client(answer: httpx.Response) -> QBittorrentClient:
    client = QBittorrentClient(
        QBittorrentSettings(url="http://qbit.test", username="u", password="p")
    )
    client._client = httpx.AsyncClient(
        base_url="http://qbit.test", transport=httpx.MockTransport(lambda request: answer)
    )
    client._logged_in = True
    return client


def test_a_torrent_qbittorrent_already_has_is_not_an_error():
    """5.x answers 409 where 4.x answered 200.

    Treated as a failure, 259 finished downloads spent every publish retry on
    it and were never moved into the library.
    """
    client = _client(httpx.Response(409, text="Conflict"))
    assert asyncio.run(client.add(magnet="magnet:?xt=urn:btih:" + "a" * 40)) is False


def test_a_new_torrent_is_added():
    client = _client(httpx.Response(200, text="Ok."))
    assert asyncio.run(client.add(magnet="magnet:?xt=urn:btih:" + "a" * 40)) is True


def test_a_real_rejection_is_still_an_error():
    client = _client(httpx.Response(415, text="Fails."))
    with pytest.raises(QBittorrentError):
        asyncio.run(client.add(magnet="magnet:?xt=urn:btih:" + "a" * 40))
