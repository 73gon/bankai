from __future__ import annotations

from bankai.web.reasons import classify_reason


def test_playwright_no_media_url_is_classified_as_extraction_failure() -> None:
    raw = (
        "bankai.queue.worker.WorkerError: playwright fallback failed: "
        "no media URL captured at https://filmpalast.to/stream/encanto"
    )

    assert classify_reason(raw) == ("extract", "Stream extraction failed")
