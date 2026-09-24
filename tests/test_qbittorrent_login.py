"""qBittorrent changed what a successful login looks like in 5.1."""

from __future__ import annotations

import httpx
import pytest

from bankai.torrent.qbittorrent import login_succeeded


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
