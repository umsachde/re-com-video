"""Seeding, candidate gathering, and the two recommendation flows.

The pipeline, in the order PLAN.md fixes it:

    seeds -> gather candidates -> merge and score -> taste -> filters
          -> EXCLUDE (last, always) -> rank -> report what happened

Exclusion is last because a filter that runs after it could reintroduce
nothing, but a filter that runs after ranking could hide the fact that
exclusion is why a result is short. Everything that removes a candidate
reports how many it removed.

Two measured facts from Phase 0 shape this file (PLAN.md 9.1a):

- A viewer neighbour carries only `ids.simkl` and `ids.slug`, never an IMDb
  ID. Crossing into Wikidata needs one detail call per neighbour. Those are
  Cloudflare-cached, which is what makes it affordable -- and they are cached
  locally too, which is what makes the second request free.
- Maker signals are film signals. For a series they often return nothing, so
  the viewer signal has to stand alone and the result says so.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import filters
import signals
import wikidata as wd

# Simkl caps users_recommendations at 12 (measured across 20 seeds, every
# catalogue). Named here so the shortfall note and the ceiling arithmetic
# cannot drift from each other.
VIEWER_CAP = 12


class SeedError(ValueError):
    """A seed title could not be resolved to exactly one thing."""


# --- seeding ----------------------------------------------------------------


_YEAR_HINT = re.compile(r"^(?P<title>.+?)[\s(]+(?P<year>(?:19|20)\d{2})\)?\s*$")

_SEARCH_TYPES = ("movie", "tv", "anime")


def resolve_seed(source, store, title: str, type_hint: str | None = None) -> dict[str, Any]:
    """Turn a title the user typed into exactly one Simkl record.

    Titles collide, badly. Measured 2026-09-16: "Panchayat" matches 4 Wikidata
    items, "Parasite" 6, "Severance" 5 -- and on Simkl it is worse, because a
    2017 Bengali film and the 2020 Hindi web series share the name across two
    *different* endpoints. Simkl's /search takes one type at a time, so
    searching only movies finds the film and never learns the series exists.

    So: search every type, pool the results, and if more than one survives,
    refuse and list them. Guessing here means recommending from the wrong work
    entirely and never finding out.

    Accepts a disambiguating year in the title itself -- "Panchayat 2020" or
    "Panchayat (2020)" -- which is how the user answers the question this
    raises.
    """
    wanted_year = None
    match = _YEAR_HINT.match(title.strip())
    if match:
        title, wanted_year = match.group("title").strip(), int(match.group("year"))

    kinds = (type_hint,) if type_hint else _SEARCH_TYPES
    pool: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for kind in kinds:
        for r in source.search(title, type=kind, limit=8) or []:
            sid = (r.get("ids") or {}).get("simkl")
            if sid is None or int(sid) in seen_ids:
                continue
            seen_ids.add(int(sid))
            pool.append(r)

    if not pool:
        raise SeedError(
            f"Simkl has no title matching {title!r}. Check the spelling, add "
            "the year, or log it on Simkl first if it's missing there."
        )

    exact = [r for r in pool if (r.get("title") or "").strip().lower() == title.lower()]
    pool = exact or pool
    if wanted_year is not None:
        by_year = [r for r in pool if r.get("year") == wanted_year]
        if not by_year:
            raise SeedError(
                f"No {title!r} from {wanted_year} on Simkl. Found: "
                + "; ".join(f"{r.get('title')} ({r.get('year')}, {r.get('type')})" for r in pool[:5])
            )
        pool = by_year

    if len(pool) > 1:
        options = "; ".join(
            f"{r.get('title')} ({r.get('year')}, {r.get('type')})" for r in pool[:6]
        )
        raise SeedError(
            f"{title!r} matches more than one title on Simkl, and picking the "
            f"wrong one would recommend from the wrong work. Which did you "
            f"mean? {options}. Add the year, e.g. \"{title} "
            f"{pool[0].get('year')}\"."
        )

    chosen = pool[0]
    return {
        "simkl_id": int((chosen.get("ids") or {}).get("simkl")),
        "title": chosen.get("title"),
        "year": chosen.get("year"),
        "type": chosen.get("type") or "movie",
    }


def detail(source, store, simkl_id: int, type_: str) -> dict[str, Any]:
    """A Simkl detail record, from cache when we have it."""
    cached = store.get_title(simkl_id)
    if cached is not None:
        return cached
    record = source.title(simkl_id, type_)
    store.cache_title(simkl_id, type_, record)
    return record


def enrich(imdb: str | None, store) -> dict[str, Any] | None:
    """Wikidata facts for a title, from cache when we have them."""
    if not imdb:
        return None
    cached = store.get_enrichment(imdb)
    if cached is not None:
        return cached or None
    found = wd.lookup_by_imdb([imdb]).get(imdb)
    # An empty record is cached too: "Wikidata doesn't have this" is an answer
    # worth remembering, and re-asking costs a 60-second-budget query.
    store.cache_enrichment(imdb, found or {})
    return found


# --- candidate gathering ----------------------------------------------------


def gather_seed(
    source,
    store,
    seed: dict[str, Any],
    *,
    with_makers: bool = True,
    degraded: list[str] | None = None,
) -> dict[int, dict[str, Any]]:
    """Every candidate reachable from one seed, tagged with its provenance.

    Returns {simkl_id: {..., "sources": {(seed_id, signal), ...}}}. The tuple
    is what the score counts and what the explanation reads -- one structure,
    so the two cannot disagree.

    A failure in *one* signal degrades the result; it does not sink the
    request. Wikidata is a public service with a 60-second budget and it does
    time out -- which it did on the first run against a real library. The
    viewer signal alone is still a usable answer, and it is a far better one
    than an exception. What was lost is appended to `degraded` so the response
    can say so rather than quietly returning less (PLAN.md 1, requirement 3).
    """
    seed_id = seed["simkl_id"]
    found: dict[int, dict[str, Any]] = {}
    record = detail(source, store, seed_id, seed["type"])
    seed_imdb = (record.get("ids") or {}).get("imdb")

    # --- viewer signal: Simkl's users_recommendations, capped at 12
    for n in record.get("users_recommendations") or []:
        nid = (n.get("ids") or {}).get("simkl")
        if nid is None:
            continue
        nid = int(nid)
        found.setdefault(
            nid,
            {
                "title": n.get("title"),
                "year": n.get("year"),
                "type": n.get("type"),
                "imdb": None,
                "sources": set(),
                "seeds": set(),
                "via_by": {},
            },
        )
        found[nid]["sources"].add((seed_id, signals.VIEWER))
        found[nid]["seeds"].add(seed_id)

    if not with_makers:
        return found

    # --- maker signals: Wikidata, film-only in practice
    try:
        enrichment = enrich(seed_imdb, store)
        if not enrichment:
            return found
        neighbours = wd.maker_neighbours(enrichment)
    except wd.WikidataError as e:
        if degraded is not None:
            label = seed.get("title") or seed_id
            degraded.append(f"{label}: maker signals unavailable ({e})")
        return found
    if not neighbours:
        return found

    # Wikidata gives IMDb IDs; the engine keys on Simkl IDs. Resolve the ones
    # that are worth resolving -- a candidate that can't cross into the Simkl
    # namespace can't be excluded reliably, so it isn't recommended (6.2).
    for work in neighbours:
        resolved = _to_simkl(source, store, work.get("imdb"))
        if resolved is None:
            continue
        nid = resolved["simkl_id"]
        found.setdefault(
            nid,
            {
                "title": work.get("title"),
                "year": work.get("year"),
                "type": resolved.get("type"),
                "imdb": work.get("imdb"),
                "sources": set(),
                "seeds": set(),
                "via_by": {},
            },
        )
        found[nid]["sources"].add((seed_id, work["signal"]))
        found[nid]["seeds"].add(seed_id)
        # Who this came through, so one person votes once (signals.evidence_key).
        # setdefault, not direct assignment: this candidate may already exist
        # from the viewer signal, which carries no `via_by`.
        found[nid].setdefault("via_by", {})[work["signal"]] = (
            work.get("via") or {}
        ).get("qid")
        found[nid]["imdb"] = found[nid].get("imdb") or work.get("imdb")
    return found


_UNRESOLVABLE = object()


def _to_simkl(source, store, imdb: str | None) -> dict[str, Any] | None:
    """IMDb ID -> Simkl ID, cached, via simkl-mcp's /redirect wrapper."""
    if not imdb:
        return None
    key = f"imdb:{imdb}"
    cached = store.get_state(key)
    if cached is not None:
        if cached == "":
            return None
        sid, _, kind = cached.partition("|")
        return {"simkl_id": int(sid), "type": kind or "movie"}
    try:
        out = source.resolve_imdb(imdb)
    except Exception:
        return None
    sid = out.get("simkl_id")
    if sid is None:
        store.set_state(key, "")
        return None
    store.set_state(key, f"{sid}|{out.get('type') or 'movie'}")
    return {"simkl_id": int(sid), "type": out.get("type") or "movie"}


def hydrate(source, store, candidates: dict[int, dict[str, Any]], criteria: dict[str, Any]) -> None:
    """Fill in the metadata the requested filters need, and nothing else.

    Filtering needs runtime, language, genres and episode counts, which are
    only in the detail record -- one call per candidate. Skipped entirely when
    no filter was asked for, because 12 candidates x N seeds of unnecessary
    calls is the difference between a fast answer and a slow one.
    """
    wanted = {k for k, v in criteria.items() if v not in (None, False)}
    if not wanted & {"language", "runtime_max", "genre", "min_rating", "finishable"}:
        return
    for simkl_id, entry in candidates.items():
        record = store.get_title(simkl_id)
        if record is None:
            try:
                record = source.title(simkl_id, entry.get("type") or "tv")
            except Exception:
                continue
            store.cache_title(simkl_id, entry.get("type") or "tv", record)
        entry["runtime"] = record.get("runtime")
        entry["genres"] = record.get("genres") or []
        entry["series_status"] = record.get("status")
        entry["total_episodes"] = record.get("total_episodes")
        entry["community_rating"] = ((record.get("ratings") or {}).get("simkl") or {}).get("rating")
        entry["imdb"] = entry.get("imdb") or (record.get("ids") or {}).get("imdb")
        langs = []
        if record.get("language"):
            langs.append(record["language"])
        enrichment = store.get_enrichment(entry["imdb"]) if entry.get("imdb") else None
        for lang in (enrichment or {}).get("language") or []:
            langs.append(lang.get("label"))
        entry["languages"] = [l for l in langs if l]


# --- the two flows ----------------------------------------------------------


def recommend(
    source,
    store,
    seeds: list[dict[str, Any]],
    *,
    limit: int = 10,
    **criteria,
) -> dict[str, Any]:
    """The whole pipeline, once the seeds are known.

    Shared by both flows, because "more like these" and "what should I watch
    tonight" differ only in where the seeds come from.
    """
    if not seeds:
        raise SeedError("No seeds to recommend from.")

    degraded: list[str] = []
    per_seed = [gather_seed(source, store, s, degraded=degraded) for s in seeds]
    merged = signals.merge_and_score(per_seed)

    # The seeds themselves are never results.
    for s in seeds:
        merged.pop(s["simkl_id"], None)

    merged = signals.apply_taste(merged, store.dislikes())

    hydrate(source, store, merged, criteria)
    filtered, filter_reports = filters.run_all(merged, **criteria)

    # Measured before exclusion, so the report below can tell the difference
    # between "the viewer signal found nothing" and "it found things you have
    # all already seen" -- which need different sentences and, for the user,
    # different actions.
    viewer_found = {
        sid for sid, e in merged.items() if any(sig == signals.VIEWER for _, sig in e["sources"])
    }

    # Exclusion last, always.
    kept, report = signals.exclude(filtered, store.exclusion_ids(), store.planned_ids())
    report["filters"] = filter_reports
    viewer_survived = len(viewer_found & set(kept))

    results = signals.rank(kept, seed_count=len(seeds), limit=limit)
    store.log_served([r["simkl_id"] for r in results], [s["simkl_id"] for s in seeds])

    note = signals.shortfall_note(len(results), limit, len(seeds), report)
    # A proportion, not a count. The first version used len//20, which called
    # 3 survivors out of 42 healthy -- it plainly isn't.
    saturated = bool(viewer_found) and viewer_survived < 0.25 * len(viewer_found)
    single_source = all(
        len({sig for _, sig in kept[r["simkl_id"]]["sources"]}) == 1 for r in results
    ) if results else False

    return {
        "results": results,
        "seeds": [
            {"simkl_id": s["simkl_id"], "title": s.get("title"), "year": s.get("year")}
            for s in seeds
        ],
        "exclusion": {
            "already_seen_removed": report["excluded_already_seen"],
            "on_your_list": report["excluded_on_your_list"],
            "history_size": store.history_size(),
        },
        "filters": filter_reports,
        "note": note,
        "signal_health": {
            "viewer_candidates": len(viewer_found),
            "viewer_surviving_exclusion": viewer_survived,
            "saturated": saturated,
        },
        "degraded": degraded or None,
        "caveats": _caveats(seeds, results, single_source)
        + ([_saturation_caveat(len(viewer_found), viewer_survived)] if saturated else [])
        + (
            [
                f"Wikidata was unreachable for {len(degraded)} of {len(seeds)} "
                "seed(s), so those contributed only Simkl's viewer signal. "
                "These results are thinner than they would normally be."
            ]
            if degraded
            else []
        ),
    }


def _caveats(seeds, results, single_source: bool) -> list[str]:
    """What the user needs to know to read this result correctly."""
    out = [
        "The 'never already seen' guarantee is only as complete as your Simkl "
        "history. Prime Video, Disney+, Max and Crave can't be imported, so "
        "anything watched there and never logged can still appear."
    ]
    if len(seeds) == 1 and results:
        out.append(
            "One seed, so nearly every result scores 1 -- Simkl's viewer "
            "neighbours and Wikidata's maker links rarely overlap (measured: "
            "13 of 20 titles shared nothing). Read this as a list, not a ranking."
        )
    if single_source and results:
        out.append(
            "Every result here came from a single signal, with no second "
            "source to corroborate it."
        )
    return out


def _saturation_caveat(viewer_found: int, viewer_survived: int) -> str:
    """The sentence for a history that has swallowed its own neighbourhood.

    Measured on a real library of 70 Marvel films: all 72 viewer candidates
    but one were themselves films already watched. That is not a thin signal
    and not a bug -- it is what "you have seen this entire corner of the
    catalogue" looks like from the inside, and the honest move is to say so
    rather than pad the list with a director's back catalogue and let it read
    like a recommendation.
    """
    return (
        f"You've seen almost everything the viewer signal found "
        f"({viewer_found - viewer_survived} of {viewer_found} candidates were "
        "already in your history), so these results lean on shared directors, "
        "writers and composers instead. That signal is much weaker -- it will "
        "surface a film-maker's unrelated older work. Seeding from titles "
        "outside this franchise would give better answers."
    )


def tonight_seeds(store, limit: int = 6) -> list[dict[str, Any]]:
    """Seeds for "what should I watch tonight" -- the user's own best titles."""
    rows = store.seeds(limit=limit)
    return [
        {"simkl_id": r["simkl_id"], "title": r.get("title"),
         "year": r.get("year"), "type": r.get("type") or "movie"}
        for r in rows
    ]
