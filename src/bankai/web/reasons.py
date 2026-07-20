"""Classify raw failure strings into a small set of friendly categories.

Job logs surface raw exception messages (e.g.
``PermanentWorkerError: no candidate met selector criteria (tried ...)``).
The UI is nicer when those collapse into a handful of understandable buckets.
Anything we don't recognise is passed through verbatim so no information is
ever lost -- an unclassified reason simply shows its raw text.
"""

from __future__ import annotations

import re

# Ordered list of (code, label, keyword-substrings). The first bucket whose
# keywords appear in the (lower-cased) reason wins, so put the more specific
# categories first. Keywords are matched case-insensitively as plain
# substrings against the message with any leading ``SomeError:`` stripped.
_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("timeout", "Timed out", ("timed out", "timeout")),
    (
        "interrupted",
        "Interrupted",
        ("stopped before completing", "interrupted", "service restart", "sigterm", "sigkill"),
    ),
    (
        "no_release",
        "No suitable release found",
        (
            "no candidate",
            "selector criteria",
            "no suitable",
            "no release",
            "no results",
            "no torrent",
            "nothing matched",
        ),
    ),
    (
        "audio_track",
        "Audio track not found",
        ("no german", "no english", "audio track", "no audio", "reference track", "no reference"),
    ),
    (
        "extract",
        "Stream extraction failed",
        (
            "yt-dlp",
            "ytdlp",
            "ytdlperror",
            "voe",
            "no video url",
            "no media url captured",
            "playwright fallback failed",
            "no stream",
            "stream url",
            "extract",
            "hoster",
        ),
    ),
    (
        "download_client",
        "Download client error",
        ("qbittorrent", "qbit", "login failed"),
    ),
    ("indexer", "Indexer error", ("prowlarr", "indexer")),
    ("metadata", "Metadata lookup failed", ("tvdb", "bearer token", "metadata")),
    ("remux", "Remux failed", ("mkvmerge", "remux", "mux failed")),
    (
        "sync",
        "Audio sync failed",
        ("ffprobe", "ffmpeg", "alass", "visualsync", "syncerror", "not a video container", "align"),
    ),
    (
        "transfer",
        "Transfer failed",
        ("rsync", "transfer", "destination", "copy failed", "move failed"),
    ),
    (
        "network",
        "Network error",
        ("connection refused", "econnrefused", "unreachable", "network", "connect", "connection"),
    ),
    (
        "payload",
        "Internal job error",
        ("payload missing", "payload requires", "missing '", "requires '", "keyerror"),
    ),
    ("cancelled", "Cancelled", ("cancel",)),
]

_ERROR_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning)\b:?\s*")

# Public: stable set of category codes (an "other" bucket covers the rest).
REASON_CODES: tuple[str, ...] = tuple(code for code, _label, _kw in _RULES) + ("other",)


def classify_reason(raw: str | None) -> tuple[str, str] | None:
    """Map a raw reason string to ``(code, label)``.

    Returns ``None`` when ``raw`` is empty. When nothing matches, returns
    ``("other", raw)`` so the caller can still show the original text.
    """
    if not raw:
        return None
    body = _ERROR_PREFIX_RE.sub("", raw).strip()
    haystack = body.lower()
    for code, label, keywords in _RULES:
        if any(kw in haystack for kw in keywords):
            return code, label
    return "other", raw
