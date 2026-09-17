"""Filters that report what they removed, and refuse to pretend.

PLAN.md §6.3: "Every filter reports what it removed. A filter that silently
falls back to 'all' is a bug." That is not hypothetical here — Simkl's browse
endpoints do exactly that, treating an unknown country, network, year or genre
as "all" and still returning 200 (§4.2). `simkl-mcp` refuses unknown values
before they are sent; this module handles the other half, filtering locally
over candidates whose metadata we already hold.

The rule every filter follows: **a candidate whose metadata is missing is not
silently dropped, and not silently kept.** It is counted as `unknown` and
reported. Dropping it would quietly shrink the catalogue in a way that looks
like a thin signal; keeping it would break the filter's promise.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

TYPES = ("movie", "tv", "anime")


class FilterError(ValueError):
    """A filter value that cannot be honoured. Raised, never silently widened."""


def _apply(
    candidates: dict[int, dict[str, Any]],
    name: str,
    predicate: Callable[[dict[str, Any]], bool | None],
    *,
    keep_unknown: bool,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run one predicate, counting matches, misses and unknowns separately.

    A predicate returns True (keep), False (drop) or None (can't tell).
    """
    kept: dict[int, dict[str, Any]] = {}
    removed = 0
    unknown = 0
    for simkl_id, entry in candidates.items():
        verdict = predicate(entry)
        if verdict is None:
            unknown += 1
            if keep_unknown:
                kept[simkl_id] = entry
            continue
        if verdict:
            kept[simkl_id] = entry
        else:
            removed += 1
    report = {
        "filter": name,
        "removed": removed,
        "kept": len(kept),
        "unknown_metadata": unknown,
    }
    if unknown:
        report["note"] = (
            f"{unknown} candidate(s) had no {name} metadata and were "
            f"{'kept' if keep_unknown else 'removed'}; this filter cannot "
            "vouch for them either way."
        )
    return kept, report


def by_type(candidates, type_: str | None):
    """movie, tv or anime. Simkl's own classification, which it can change."""
    if not type_:
        return candidates, None
    t = str(type_).strip().lower()
    if t in ("show", "series", "tv show"):
        t = "tv"
    if t == "movies":
        t = "movie"
    if t not in TYPES:
        raise FilterError(f"type must be one of {', '.join(TYPES)} (got {type_!r}).")
    return _apply(
        candidates, "type", lambda e: (e.get("type") == t) if e.get("type") else None,
        keep_unknown=False,
    )


def by_language(candidates, language: str | None):
    """Original language, as an ISO 639-1 code or an English name.

    Matched against Simkl's `language` and Wikidata's original-language labels,
    whichever the candidate has. Measured 2026-09-15: 20/20 probe seeds carried
    an original language in Wikidata, so this filter has something to work with
    far more often than not.
    """
    if not language:
        return candidates, None
    want = str(language).strip().lower()

    def predicate(entry):
        langs = entry.get("languages")
        if not langs:
            return None
        return any(want == str(l).lower() or want in str(l).lower() for l in langs)

    return _apply(candidates, "language", predicate, keep_unknown=False)


def by_runtime(candidates, runtime_max: int | None):
    """Minutes, from Simkl's `runtime`. For a series this is per episode."""
    if runtime_max is None:
        return candidates, None
    cap = int(runtime_max)
    if cap <= 0:
        raise FilterError("runtime_max must be a positive number of minutes.")

    def predicate(entry):
        rt = entry.get("runtime")
        return None if not rt else int(rt) <= cap

    return _apply(candidates, "runtime", predicate, keep_unknown=False)


def by_genre(candidates, genre: str | None):
    if not genre:
        return candidates, None
    want = str(genre).strip().lower()

    def predicate(entry):
        genres = entry.get("genres")
        if not genres:
            return None
        return any(want in str(g).lower() for g in genres)

    return _apply(candidates, "genre", predicate, keep_unknown=False)


def by_min_rating(candidates, min_rating: float | None):
    """Simkl's community rating, not the user's."""
    if min_rating is None:
        return candidates, None
    floor = float(min_rating)

    def predicate(entry):
        r = entry.get("community_rating")
        return None if r is None else float(r) >= floor

    return _apply(candidates, "min_rating", predicate, keep_unknown=False)


def finishable(candidates, enabled: bool | None, max_episodes: int = 30):
    """"A short show I can finish this weekend": ended, and not too long.

    Two conditions, because either alone is wrong -- a 12-episode show still
    airing isn't finishable, and an ended 200-episode show isn't a weekend.
    """
    if not enabled:
        return candidates, None

    def predicate(entry):
        if entry.get("type") == "movie":
            return False
        status = (entry.get("series_status") or "").lower()
        total = entry.get("total_episodes")
        if not status and total is None:
            return None
        if status and status not in ("ended", "released", "canceled", "cancelled"):
            return False
        if total is None:
            return None
        return int(total) <= max_episodes

    return _apply(candidates, "finishable", predicate, keep_unknown=False)


def run_all(candidates: dict[int, dict[str, Any]], **criteria) -> tuple[dict, list[dict]]:
    """Apply every requested filter, collecting a report from each.

    Order matters only for the report's readability, not the result: filters
    are independent predicates over the same candidate set.
    """
    reports: list[dict[str, Any]] = []
    steps = (
        (by_type, "type"),
        (by_language, "language"),
        (by_runtime, "runtime_max"),
        (by_genre, "genre"),
        (by_min_rating, "min_rating"),
        (finishable, "finishable"),
    )
    for fn, key in steps:
        value = criteria.get(key)
        candidates, report = fn(candidates, value)
        if report:
            reports.append(report)
    return candidates, reports
