from __future__ import annotations

from bankai.web.reasons import classify_reason


def test_playwright_no_media_url_is_classified_as_extraction_failure() -> None:
    raw = (
        "bankai.queue.worker.WorkerError: playwright fallback failed: "
        "no media URL captured at https://filmpalast.to/stream/encanto"
    )

    assert classify_reason(raw) == ("extract", "Stream extraction failed")


def test_unavailable_indexers_are_classified_as_temporary_search_failure() -> None:
    raw = (
        "PermanentWorkerError: torrent indexers unavailable; rerun later: "
        "All indexers are unavailable due to failures for more than 6 hours"
    )

    assert classify_reason(raw) == (
        "indexer",
        "Torrent indexers unavailable — rerun later",
    )
