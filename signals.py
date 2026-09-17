"""Candidate gathering, agreement scoring, and exclusion.

The scoring rule is re-com's, unchanged, because it gives a ranking and an
explanation at once and degrades gracefully when a signal is empty:

    score = the number of distinct (seed, signal) pairs that surfaced a title

What Phase 0 measured (PLAN.md §9.1a) changes how much that rule can carry,
and the honesty requirements that follow are implemented here rather than
left to the caller:

- **Simkl's viewer signal is hard-capped at 12 per seed.** Every seed in every
  catalogue returned 11 or 12. So one seed yields at most 12 viewer candidates,
  and a `limit` above that has to be met from maker signals or reported short.
- **The two sources rarely agree.** 13 of 20 seeds shared nothing between
  viewer and maker; 7 shared 1-5 titles. So on a *single* seed the score is
  almost always 1, and ranking by it is close to meaningless. `confidence`
  below says so out loud instead of dressing a flat list as a ranking.
- **Maker signals are film signals.** `Kota Factory` and `The Bear` return zero
  maker neighbours. For TV the viewer signal is usually the only one, and a
  single-source pick is labelled as such.
"""

from __future__ import annotations

from typing import Any, Iterable

# Everything that can surface a candidate. `viewers` is Simkl's; the rest are
# Wikidata's (PLAN.md §6.1).
VIEWER = "viewers"
MAKER_SIGNALS = ("director", "writer", "composer")
FRANCHISE_SIGNALS = ("series", "based_on")


# How much one piece of evidence is worth. The plan's original rule counted
# every (seed, signal) pair equally; a live run on a real library showed why
# that can't stand. Phase 0 (PLAN.md 9.1a) measured that the viewer signal is
# the reliable one and the maker signal is film-only and noisy, and on a
# franchise-heavy history the maker signal is mostly a director's unrelated
# back catalogue -- Nomadland reached through Eternals, Cop Land through Logan.
#
# Unweighted, two such coincidences outranked a genuine viewer agreement. At
# 0.35, a maker-only candidate needs three distinct people before it edges past
# a single viewer agreement, and it can never beat two. `score` is still
# reported as the honest count of distinct evidence; this only drives ranking.
_SIGNAL_WEIGHT = {VIEWER: 1.0}
_MAKER_WEIGHT = 0.35


def weight_of(signal: str) -> float:
    return _SIGNAL_WEIGHT.get(signal, _MAKER_WEIGHT)


def evidence_key(seed_id: int, signal: str, via: str | None) -> tuple:
    """What counts as *one* piece of evidence for a candidate.

    For the viewer signal, each seed is genuinely independent: two of your
    films whose audiences both also watched X are two separate observations.
    Key on (seed, signal).

    For maker signals it is the opposite, and getting this wrong was visible
    the first time the engine ran on a real library. Sam Raimi directed
    Spider-Man 1, 2 and 3; with three of those as seeds, his entire back
    catalogue -- Evil Dead, Darkman, a 1973 comedy -- scored 3 and outranked
    everything the viewer signal found. But that is not three films agreeing.
    It is one fact, "Raimi made this", counted three times. So maker evidence
    is keyed on (signal, person) and a person votes once however many of your
    seeds they worked on.

    This is PLAN.md §6.1's "don't let director + series + cast from the same
    franchise count as three independent votes", applied to the case that
    actually bites.
    """
    if via is None:
        return (seed_id, signal)
    return (signal, via)


def merge_and_score(per_seed: list[dict[int, dict[str, Any]]]) -> dict[int, dict[str, Any]]:
    """Combine candidates from several seeds.

    Score is the number of distinct pieces of evidence (see `evidence_key`).
    Keyed by Simkl ID throughout -- one namespace, so exclusion is an integer
    set operation rather than a text match (§6.2).
    """
    merged: dict[int, dict[str, Any]] = {}
    for found in per_seed:
        for simkl_id, data in found.items():
            entry = merged.get(simkl_id)
            if entry is None:
                entry = {
                    "simkl_id": simkl_id,
                    "title": data.get("title"),
                    "year": data.get("year"),
                    "type": data.get("type"),
                    "imdb": data.get("imdb"),
                    "sources": set(),
                    "seeds": set(),
                    "evidence": set(),
                    "score": 0,
                }
                merged[simkl_id] = entry
            entry["sources"] |= data["sources"]
            entry["seeds"] |= data["seeds"]
            entry["evidence"] |= {
                (evidence_key(seed, sig, (data.get("via_by") or {}).get(sig)), sig)
                for seed, sig in data["sources"]
            }
            entry["score"] = len(entry["evidence"])
            entry["weighted_score"] = round(
                sum(weight_of(sig) for _, sig in entry["evidence"]), 3
            )
            # Prefer a name over a None if a later seed knew more.
            for field in ("title", "year", "type", "imdb"):
                if entry.get(field) is None and data.get(field) is not None:
                    entry[field] = data[field]
    return merged


def apply_taste(
    candidates: dict[int, dict[str, Any]], dislikes: dict[int, float]
) -> dict[int, dict[str, Any]]:
    """Demote candidates reached through titles the user disliked.

    Demotes, never excludes (§5.2). A director whose other film you dropped is
    weaker evidence, not disqualifying evidence -- and turning a negative into
    an exclusion would quietly shrink the catalogue in a way nothing reports.
    """
    if not dislikes:
        return candidates
    for entry in candidates.values():
        penalty = sum(dislikes.get(seed, 0.0) for seed in entry["seeds"])
        base = entry.get("weighted_score", float(entry["score"]))
        if penalty:
            entry["taste_penalty"] = round(penalty, 3)
            entry["adjusted_score"] = round(base + penalty * 0.5, 3)
        else:
            entry["adjusted_score"] = float(base)
    return candidates


def exclude(
    candidates: dict[int, dict[str, Any]],
    seen: set[int],
    planned: set[int] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Remove everything already in the history. Runs last, always.

    Returns the survivors and a report. The report exists because §1's third
    hard requirement is that a shortfall is stated: if exclusion is why only
    four results came back, the user is told that rather than left to guess
    the engine is bad at its job.

    `planned` is excluded too, but counted separately -- "already on your list"
    is a different sentence from "you've seen it".
    """
    planned = planned or set()
    kept: dict[int, dict[str, Any]] = {}
    removed_seen = 0
    removed_planned = []
    for simkl_id, entry in candidates.items():
        if simkl_id in planned:
            removed_planned.append(
                {"simkl_id": simkl_id, "title": entry.get("title")}
            )
            continue
        if simkl_id in seen:
            removed_seen += 1
            continue
        kept[simkl_id] = entry
    return kept, {
        "excluded_already_seen": removed_seen,
        "excluded_on_your_list": removed_planned,
        "candidates_before_exclusion": len(candidates),
        "candidates_after_exclusion": len(kept),
    }


def confidence(entry: dict[str, Any], seed_count: int) -> str:
    """How much the score actually means for this title.

    Phase 0 measured that cross-source agreement is rare, so a score of 1 is
    the norm rather than a weak result. Saying "single signal" out loud is more
    honest than implying a 1-vs-1 ordering carries information it does not.
    """
    sources = {s for _, s in entry["sources"]}
    if len(entry["seeds"]) > 1:
        return "agreed across several of your titles"
    if len(sources) > 1:
        return "two independent signals agreed"
    if sources == {VIEWER}:
        return "one signal: viewers of your title also watched it"
    return f"one signal: {next(iter(sources), 'unknown')}"


def rank(
    candidates: dict[int, dict[str, Any]], seed_count: int, limit: int
) -> list[dict[str, Any]]:
    """Order by agreement, then present the result honestly.

    Ties are broken by how many distinct seeds contributed, then by title, so
    a run is reproducible rather than dependent on dict ordering.
    """
    rows = list(candidates.values())
    # At equal evidence, prefer what the viewer signal found. Phase 0 measured
    # that the maker signal is film-only and, on a franchise-heavy history,
    # mostly a director's unrelated back catalogue -- a 1966 comedy reached
    # through a composer is not really a recommendation. Viewer backing is a
    # tiebreak, not an override: it never reorders across different scores.
    rows.sort(
        key=lambda e: (
            e.get("adjusted_score", e.get("weighted_score", e["score"])),
            1 if any(sig == VIEWER for _, sig in e["sources"]) else 0,
            len(e["seeds"]),
            -(e.get("year") or 0),
        ),
        reverse=True,
    )
    out = []
    for entry in rows[:limit]:
        out.append(
            {
                "simkl_id": entry["simkl_id"],
                "title": entry.get("title"),
                "year": entry.get("year"),
                "type": entry.get("type"),
                "imdb": entry.get("imdb"),
                "score": entry["score"],
                "why": explain(entry),
                "confidence": confidence(entry, seed_count),
                "simkl_url": simkl_url(entry["simkl_id"], entry.get("type") or "tv"),
                **(
                    {"taste_penalty": entry["taste_penalty"]}
                    if entry.get("taste_penalty")
                    else {}
                ),
            }
        )
    return out


def explain(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Which seed reached this title, through which signal.

    Built from the same (seed, signal) pairs the score counts, so the
    explanation cannot drift from the ranking -- they are the same data.
    """
    by_seed: dict[int, list[str]] = {}
    for seed, source in sorted(entry["sources"], key=lambda p: (p[0], p[1])):
        by_seed.setdefault(seed, []).append(source)
    return [{"seed_simkl_id": seed, "signals": sigs} for seed, sigs in by_seed.items()]


def simkl_url(simkl_id: int, type_: str) -> str:
    """Simkl's API rules require linking back to the page for any title shown."""
    kind = {"movie": "movies", "movies": "movies", "anime": "anime"}.get(type_, "tv")
    return f"https://simkl.com/{kind}/{simkl_id}"


def shortfall_note(
    returned: int, limit: int, seed_count: int, report: dict[str, Any]
) -> str | None:
    """Say why there are fewer results than asked for, or say nothing.

    Never silently returns a short list (§1, hard requirement 3). The viewer
    cap is named explicitly because it is a property of the data source, not
    of the request, and a user who asked for 20 from one seed deserves to know
    that 12 was the ceiling before filtering even started.
    """
    if returned >= limit:
        return None
    reasons = []
    excluded = report.get("excluded_already_seen", 0)
    if excluded:
        reasons.append(f"{excluded} candidates were removed because you've already seen them")
    planned = len(report.get("excluded_on_your_list") or [])
    if planned:
        reasons.append(f"{planned} are already on your Plan to Watch list")
    ceiling = seed_count * 12
    if limit > ceiling:
        reasons.append(
            f"Simkl returns at most 12 viewer-based neighbours per title, so "
            f"{seed_count} seed(s) can yield at most ~{ceiling} from that signal"
        )
    for f in report.get("filters") or []:
        if f.get("removed"):
            reasons.append(f"the {f['filter']} filter removed {f['removed']}")
    if not reasons:
        reasons.append("the signals simply did not surface more")
    return (
        f"Returning {returned} of the {limit} requested. "
        + "; ".join(reasons)
        + "."
    )
