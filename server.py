"""MCP server: the re-com-video tool surface (PLAN.md 7.2).

Read-only by design. Nothing here writes to Simkl -- logging "I watched X"
belongs to `simkl-mcp`, so that a recommender is never the thing that also
mutated the history it claims to have excluded (PLAN.md 8.4).

Every tool either returns results or says why it couldn't, and every result
carries its Simkl link (Simkl's API rule 1), the signals behind it, and the
caveats needed to read it correctly.
"""

from __future__ import annotations

import functools
from typing import Any

from mcp.server.mcpserver import MCPServer

import filters
import recommend
import signals
import store as store_mod
import wikidata as wd
from simkl_source import SimklSource, SimklSourceError

mcp = MCPServer("re-com-video")

_source: SimklSource | None = None
_store: store_mod.Store | None = None


def source() -> SimklSource:
    global _source
    if _source is None:
        _source = SimklSource()
    return _source


def db() -> store_mod.Store:
    global _store
    if _store is None:
        _store = store_mod.Store()
    return _store


def handle_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (SimklSourceError, recommend.SeedError, filters.FilterError, wd.WikidataError) as e:
            raise RuntimeError(str(e)) from e

    return wrapper


def _require_history() -> None:
    """Refuse to recommend against an empty history rather than pretend.

    With nothing synced, exclusion has nothing to exclude, so the guarantee
    this project exists for would be vacuously true. Saying so beats returning
    confident results that might all be things the user watched last year.
    """
    if db().history_size() == 0:
        raise RuntimeError(
            "No watch history synced yet, so nothing can be excluded and the "
            "'never already seen' guarantee would be meaningless. Run "
            "refresh_library first (and if you haven't connected Simkl, run "
            "simkl-mcp's setup_auth.py)."
        )


# --- recommending -----------------------------------------------------------


@mcp.tool()
@handle_errors
def recommend_from_titles(
    titles: list[str],
    type: str | None = None,
    language: str | None = None,
    runtime_max: int | None = None,
    genre: str | None = None,
    min_rating: float | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """"More like Panchayat" -- recommend from one or more titles you name.

    titles: one or more titles you've watched and liked. Several seeds are
      much better than one: agreement across your own titles is the only thing
      that makes the ranking mean much (Simkl's viewer neighbours and
      Wikidata's maker links rarely overlap, so a single seed scores almost
      everything 1).
    type: movie, tv or anime -- crosses freely, so "like Past Lives but a show"
      works.
    language: original language, e.g. Hindi, Korean, en.
    runtime_max: minutes. For a series this is per episode.

    Never returns something already in your history. If the list is short, the
    response says which step shortened it.
    """
    _require_history()
    if not titles:
        raise RuntimeError("Give at least one title to recommend from.")
    seeds = [recommend.resolve_seed(source(), db(), t) for t in titles]
    return recommend.recommend(
        source(), db(), seeds,
        limit=limit, type=type, language=language,
        runtime_max=runtime_max, genre=genre, min_rating=min_rating,
    )


@mcp.tool()
@handle_errors
def recommend_for_tonight(
    type: str | None = None,
    language: str | None = None,
    runtime_max: int | None = None,
    genre: str | None = None,
    finishable: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """"What should I watch tonight?" -- seeded from your own best titles.

    Seeds come from what you rated 8-10 and recently finished, weighted by how
    much of a series you actually watched. Nothing you've seen comes back.

    finishable: only series that have ended and are short enough to finish in
      a weekend.
    runtime_max: e.g. 120 for "a movie under two hours".
    """
    _require_history()
    seeds = recommend.tonight_seeds(db())
    if not seeds:
        raise RuntimeError(
            "Nothing in your history is a strong enough signal to seed from -- "
            "rate a few titles you loved (8-10), or name them directly with "
            "recommend_from_titles."
        )
    return recommend.recommend(
        source(), db(), seeds,
        limit=limit, type=type, language=language,
        runtime_max=runtime_max, genre=genre, finishable=finishable,
    )


@mcp.tool()
@handle_errors
def explain_recommendation(title: str) -> dict[str, Any]:
    """"Why did you recommend this?" -- which of your titles reached it, how.

    Re-derives the answer from your current history rather than replaying a
    stored one, so it reflects what the engine would say now.
    """
    seed = recommend.resolve_seed(source(), db(), title)
    record = recommend.detail(source(), db(), seed["simkl_id"], seed["type"])
    imdb = (record.get("ids") or {}).get("imdb")
    enrichment = recommend.enrich(imdb, db())

    reached_by = []
    for s in recommend.tonight_seeds(db(), limit=12):
        found = recommend.gather_seed(source(), db(), s)
        if seed["simkl_id"] in found:
            reached_by.append(
                {
                    "seed": s.get("title"),
                    "signals": sorted(
                        sig for _, sig in found[seed["simkl_id"]]["sources"]
                    ),
                }
            )
    return {
        "title": seed.get("title"),
        "year": seed.get("year"),
        "simkl_url": signals.simkl_url(seed["simkl_id"], seed["type"]),
        "reached_by": reached_by,
        "in_your_history": db().get_history(seed["simkl_id"]) is not None,
        "enrichment_coverage": wd.coverage(enrichment) if enrichment else None,
        "note": (
            "Nothing in your current taste profile reaches this title -- it "
            "may have come from seeds you named directly."
            if not reached_by
            else None
        ),
    }


@mcp.tool()
@handle_errors
def read_my_taste() -> dict[str, Any]:
    """What your history actually says you like.

    The analogue of re-com's read_my_mood: the seeds "tonight" would use, the
    titles that point away, and the shape of the history behind both.
    """
    _require_history()
    s = db()
    seeds = s.seeds(limit=12)
    dislikes = s.dislikes()
    disliked_rows = [s.get_history(i) for i in list(dislikes)[:10]]
    return {
        "summary": s.taste_summary(),
        "strongest_seeds": [
            {"title": r.get("title"), "year": r.get("year"), "type": r.get("type"),
             "rating": r.get("rating"), "weight": r["taste_weight"]}
            for r in seeds
        ],
        "points_away_from": [
            {"title": r.get("title"), "status": r.get("status"), "rating": r.get("rating")}
            for r in disliked_rows if r
        ],
        "how_this_is_used": (
            "Seeds drive 'what should I watch tonight'. Titles you rated low "
            "or dropped demote what they reach -- they never exclude it."
        ),
    }


# --- keeping the mirror honest ----------------------------------------------


@mcp.tool()
@handle_errors
def refresh_library(ids: list[int] | None = None, full: bool = False) -> dict[str, Any]:
    """Resync the watch history from Simkl.

    ids: Simkl IDs to pull in immediately -- use this straight after logging
      something with simkl-mcp, so the thing you just watched is excluded now
      rather than at the next sync.
    full: ignore the saved timestamp and pull the whole library.

    Otherwise this is the cheap path: it checks Simkl's activity timestamps
    first and does nothing if nothing moved.
    """
    s, src = db(), source()

    if ids:
        found = src.lookup_watched([{"simkl": int(i)} for i in ids])
        rows = []
        for item in found:
            sid = ((item.get("ids") or {}).get("simkl")) or item.get("simkl")
            if sid is None:
                continue
            rows.append(
                {
                    "simkl_id": int(sid),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "status": item.get("status"),
                    "last_watched_at": item.get("last_watched_at"),
                }
            )
        written = s.upsert_history(rows)
        return {
            "mode": "targeted",
            "requested": len(ids),
            "written": written,
            "not_in_your_library": len(ids) - written,
            "history_size": s.history_size(),
        }

    activities = src.activities()
    latest = activities.get("all")
    last = s.get_state("last_activity_all")
    if not full and latest and last and latest == last:
        return {
            "mode": "no-op",
            "reason": "Simkl's activity timestamp hasn't moved since the last sync.",
            "last_sync": last,
            "history_size": s.history_size(),
        }

    date_from = None if (full or not last) else last
    payload = src.library(date_from=date_from)
    written = s.upsert_history(payload.get("items") or [])
    skipped = payload.get("skipped_without_simkl_id") or 0

    removed = 0
    # A delta never contains removals, so when Simkl says something left a
    # list, the full ID set is refetched and diffed. Skipping this is how a
    # title stays excluded forever after the user removed it.
    moved = any(
        (activities.get(t) or {}).get("removed_from_list")
        for t in ("movies", "tv_shows", "anime")
    )
    if moved or full:
        live = set(src.library_ids())
        if live:
            removed = s.remove_history(s.exclusion_ids() - live)

    if latest:
        s.set_state("last_activity_all", latest)
    return {
        "mode": "full" if date_from is None else "delta",
        "written": written,
        "removed": removed,
        "skipped_without_simkl_id": skipped,
        "history_size": s.history_size(),
        "warning": (
            f"{skipped} history rows carried no Simkl ID and could not be "
            "mirrored -- those titles cannot be excluded."
            if skipped else None
        ),
    }


@mcp.tool()
@handle_errors
def record_feedback(title: str, reaction: str) -> dict[str, Any]:
    """Note a reaction to a recommendation. Local only -- never sent to Simkl.

    reaction: free text, e.g. "loved it", "not for me", "already seen".
    To actually mark something watched or rated, use simkl-mcp -- this server
    never writes to your account.
    """
    seed = recommend.resolve_seed(source(), db(), title)
    db().record_feedback(seed["simkl_id"], reaction)
    return {
        "title": seed.get("title"),
        "recorded": reaction,
        "scope": "local to this machine; nothing was written to Simkl",
    }


@mcp.tool()
@handle_errors
def index_status() -> dict[str, Any]:
    """History size, last sync, cache coverage, and the known gaps."""
    out = db().status()
    try:
        out["simkl_mcp"] = "connected"
        source().activities()
    except Exception as e:  # noqa: BLE001 - status must report, not raise
        out["simkl_mcp"] = f"unavailable: {e}"
    return out


if __name__ == "__main__":
    mcp.run()
