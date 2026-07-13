"""Torrent candidate scoring & selection.

Given a list of :class:`TorrentCandidate` and the user's
:class:`SelectorSettings`, pick the best one or return ``None``.

Scoring is intentionally explicit and explainable:

* Hard filters (resolution allow-list, min seeders, size bounds) drop a
  candidate before scoring.
* Soft preferences (codec, source, release group) contribute additive
  bonuses; preferred-list order is the tiebreaker (earlier = higher).
* Seeder count is a small additive bonus, capped, so it nudges between
  otherwise-equal candidates without dominating quality choices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bankai.config import SelectorSettings, get_settings
from bankai.torrent.prowlarr import TorrentCandidate

_GIB = 1024**3
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# A release that names a specific episode / season (SxxEyy, 1x01, "Season 2",
# "Staffel 2", "Complete Series"). Used to keep TV packs out of movie picks.
_EPISODIC_RE = re.compile(
    r"\b(s\d{1,2}\s*e\d{1,3}|\d{1,2}x\d{1,3}|season\s*\d+|staffel\s*\d+"
    r"|complete\s+series|s\d{1,2}\b)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _release_title_tokens(title: str) -> list[str]:
    """Return the leading 'movie title' tokens from a release name.

    Heuristic: take all word tokens up to (but not including) the first
    4-digit year. If no year is present, fall back to the first 6 tokens.
    """
    m = _YEAR_RE.search(title)
    head = title[: m.start()] if m else title
    toks = _normalize(head)
    return toks if toks else _normalize(title)[:6]


def _query_tokens(query: str) -> tuple[list[str], str | None]:
    m = _YEAR_RE.search(query)
    year = m.group(0) if m else None
    head = query[: m.start()] if m else query
    return _normalize(head), year


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    candidate: TorrentCandidate
    score: float
    reasons: tuple[str, ...]


class TorrentSelector:
    def __init__(self, settings: SelectorSettings | None = None) -> None:
        self._settings = settings or get_settings().selector

    # ---- public API --------------------------------------------------------

    def select(self, candidates: list[TorrentCandidate], *, query: str | None = None) -> ScoredCandidate | None:
        scored = self.rank(candidates, query=query)
        return scored[0] if scored else None

    def rank(self, candidates: list[TorrentCandidate], *, query: str | None = None) -> list[ScoredCandidate]:
        filtered = self._filter_by_query(candidates, query) if query else candidates
        scored = [s for s in (self._score(c) for c in filtered) if s is not None]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    # ---- title relevance --------------------------------------------------

    def _filter_by_query(self, candidates: list[TorrentCandidate], query: str) -> list[TorrentCandidate]:
        q_tokens, q_year = _query_tokens(query)
        # Drop trivial query-side stopwords too short to be informative.
        q_main = [t for t in q_tokens if len(t) >= 2]
        if not q_main:
            return candidates
        # If the query itself names no episode/season, treat it as a movie
        # search and reject episodic (TV) releases outright — this stops a
        # short movie title like "Get Out" from matching a random series.
        query_is_episodic = bool(_EPISODIC_RE.search(query))
        kept: list[TorrentCandidate] = []
        for c in candidates:
            if not query_is_episodic and _EPISODIC_RE.search(c.title):
                continue
            cand_tokens = _release_title_tokens(c.title)
            cand_set = set(cand_tokens)
            # All informative query tokens must appear in the candidate's
            # leading title segment (before the year tag).
            if not all(t in cand_set for t in q_main):
                continue
            # Reject candidates whose title carries *extra* content words the
            # query doesn't have -- e.g. query "Obsession" vs release "Toxic
            # Obsession", or "Smile" vs "Smile 2". That's a different movie, not
            # just a release tag. Common leading articles are ignored.
            if q_year and not query_is_episodic:
                extra_words = (cand_set - set(q_main)) - {"the", "a", "an"}
                if extra_words:
                    continue
            # If the query carries a year, the candidate must mention the
            # same year somewhere in its full title.
            if q_year and q_year not in c.title:
                continue
            kept.append(c)
        return kept

    # ---- scoring -----------------------------------------------------------

    def _score(self, c: TorrentCandidate) -> ScoredCandidate | None:
        s = self._settings
        reasons: list[str] = []

        # ---- hard filters --------------------------------------------------
        if c.seeders < s.min_seeders:
            return None
        size_gib = c.size_bytes / _GIB
        if size_gib < s.min_size_gib or size_gib > s.max_size_gib:
            return None
        res = c.resolution
        if s.preferred_resolutions and (res is None or res not in [r.lower() for r in s.preferred_resolutions]):
            return None

        score = 0.0

        # ---- resolution rank (preferred order) -----------------------------
        if res:
            try:
                idx = [r.lower() for r in s.preferred_resolutions].index(res)
                bonus = 1000 - (idx * 50)
                score += bonus
                reasons.append(f"resolution {res} (+{bonus:.0f})")
            except ValueError:
                pass

        # ---- codec ---------------------------------------------------------
        codec = c.codec
        if codec and s.preferred_codecs:
            lowered = [x.lower() for x in s.preferred_codecs]
            if codec.lower() in lowered:
                idx = lowered.index(codec.lower())
                bonus = 200 - (idx * 25)
                score += bonus
                reasons.append(f"codec {codec} (+{bonus:.0f})")

        # ---- source --------------------------------------------------------
        src = c.source
        if src and s.preferred_sources:
            lowered = [x.lower() for x in s.preferred_sources]
            if src.lower() in lowered:
                idx = lowered.index(src.lower())
                bonus = 150 - (idx * 20)
                score += bonus
                reasons.append(f"source {src} (+{bonus:.0f})")

        # ---- release group -------------------------------------------------
        grp = c.release_group
        if grp and s.preferred_groups:
            lowered = [x.lower() for x in s.preferred_groups]
            if grp.lower() in lowered:
                idx = lowered.index(grp.lower())
                bonus = 300 - (idx * 30)
                score += bonus
                reasons.append(f"group {grp} (+{bonus:.0f})")

        # ---- audio language preference ------------------------------------
        if s.preferred_audio_languages:
            title_l = c.title.lower()
            # Foreign-dub markers we want to penalize when our preference
            # is English (avoids picking a German/French/Italian-only dub
            # when the user wants English audio + the German dub overlay).
            foreign_markers = (
                "german dl",
                "german.dl",
                " ger ",
                ".ger.",
                " ger-",
                ".ger-",
                "italian",
                "french",
                "spanish",
                "russian",
                "hindi",
                "tamil",
                "polish",
                "turkish",
                "portuguese",
                "dutch",
                "japanese dub",
                "korean dub",
            )
            for idx, lang in enumerate(s.preferred_audio_languages):
                if lang.lower() in title_l:
                    bonus = 400 - (idx * 50)
                    score += bonus
                    reasons.append(f"audio {lang} (+{bonus:.0f})")
                    break
            else:
                # No preferred language token matched. If a foreign-dub
                # marker is present, that's a strong signal this isn't an
                # English release; penalize.
                if any(m in title_l for m in foreign_markers):
                    score -= 500
                    reasons.append("foreign-dub marker (-500)")

        # ---- seeders (small additive nudge, capped) ------------------------
        seed_bonus = min(c.seeders, 100) * 0.5
        score += seed_bonus
        reasons.append(f"seeders {c.seeders} (+{seed_bonus:.1f})")

        return ScoredCandidate(candidate=c, score=score, reasons=tuple(reasons))
