"""Wikidata as the maker-side signal: directors, writers, composers, cast, series.

Simkl answers *whose taste this is* and *what the same viewers watched*.
Wikidata answers *what connects titles by the people who made them and the
material they came from* (PLAN.md 4.4). This module is the second half.

Why Wikidata and not TMDb: all structured data is CC0 public domain, there is
no AI clause and no key, and a live probe on 2026-09-13 found it covers the
Indian catalogue well (5,377 films since 2015, 92% with an original language,
62% with a director) — which matters because that catalogue is where re-com's
music equivalent was weakest.

Query service limits, from the WDQS manual: a 60-second hard timeout, 60s of
processing per 60s per client, 5 parallel queries per IP, and a descriptive
User-Agent is required or the client gets blocked. A country-wide aggregate
over US films timed out at 60s in that same probe, so everything here looks up
titles individually or in small ID batches. There are no aggregate queries in
this file on purpose.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

import requests

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "re-com-video/0.1 (https://github.com/umsachde/re-com-video) "
    "personal recommendation engine"
)

# WDQS asks for 5 parallel queries per IP at most; this client is sequential
# and spaces requests, which keeps it far under that and under the processing
# budget too.
_MIN_INTERVAL = 0.35
_last_call = 0.0

# PLAN.md 6.1. `cast` is deliberately absent from the default set: a blockbuster
# lists dozens of cast members, so "shares a cast member" is noise unless it can
# be restricted to billed principals, and whether Wikidata's cast ordering
# supports that is unverified. Phase 0 measures it before it gets a vote.
PROPERTIES = {
    "director": "P57",
    "writer": "P58",
    "composer": "P86",
    "cast": "P161",
    "series": "P179",
    "follows": "P155",
    "followed_by": "P156",
    "based_on": "P144",
    "language": "P364",
    "country": "P495",
    "genre": "P136",
    "imdb": "P345",
}

MAKER_SIGNALS = ("director", "writer", "composer")


class WikidataError(RuntimeError):
    """A query failed in a way the caller should report rather than swallow."""


# WDQS sits behind an nginx that returns a bare 502/503 under load — observed
# on the first live run of this module, on a query that succeeded unchanged
# moments later. It is transient and says nothing about the query, so it is
# retried; a 429 or a timeout is not, because retrying either makes it worse.
_RETRY_STATUS = (502, 503, 504)
_RETRIES = 3


def _sparql(query: str, *, timeout: float = 60.0, sleep=None) -> list[dict[str, Any]]:
    """Run one query and return its bindings, flattened to plain values."""
    global _last_call
    # Resolved here rather than as a default argument, so that patching
    # time.sleep (in tests) actually takes effect.
    sleep = sleep or time.sleep
    last_detail = ""
    for attempt in range(_RETRIES):
        gap = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if gap > 0:
            sleep(gap)
        try:
            resp = requests.get(
                ENDPOINT,
                params={"query": query, "format": "json"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
                timeout=timeout,
            )
        except requests.exceptions.RequestException as e:
            raise WikidataError(f"Network error reaching the Wikidata query service: {e}") from e
        finally:
            _last_call = time.monotonic()

        if resp.status_code == 429:
            raise WikidataError(
                "Wikidata is throttling this client (429). It allows 60s of query "
                "processing per 60s per User-Agent + IP."
            )
        if resp.status_code in _RETRY_STATUS:
            last_detail = f"HTTP {resp.status_code}"
            if attempt < _RETRIES - 1:
                sleep(1.5 * (attempt + 1))
                continue
            raise WikidataError(
                f"The Wikidata query service is unavailable ({last_detail}) after "
                f"{_RETRIES} attempts. This is a service outage, not a bad query."
            )
        if resp.status_code != 200:
            # A timeout arrives as a 500 with a Java exception in the body.
            detail = resp.text[:200]
            if "TimeoutException" in resp.text or "QueryTimeout" in resp.text:
                raise WikidataError(
                    "The Wikidata query timed out at 60s. Look titles up "
                    f"individually rather than in aggregate. ({detail})"
                )
            raise WikidataError(f"Wikidata returned HTTP {resp.status_code}: {detail}")

        try:
            bindings = resp.json()["results"]["bindings"]
        except (ValueError, KeyError) as e:
            raise WikidataError(f"Unreadable response from Wikidata: {resp.text[:200]}") from e
        return [{k: v.get("value") for k, v in row.items()} for row in bindings]
    raise WikidataError(f"Wikidata query failed: {last_detail}")  # unreachable in practice


def _qid(uri: str | None) -> str | None:
    """Q-number out of an entity URI."""
    if not uri:
        return None
    return uri.rsplit("/", 1)[-1] or None


def _values_clause(imdb_ids: Iterable[str]) -> str:
    quoted = " ".join(f'"{i}"' for i in imdb_ids if i)
    return f"VALUES ?imdb {{ {quoted} }}"


def lookup_by_imdb(imdb_ids: Iterable[str], *, batch: int = 40) -> dict[str, dict[str, Any]]:
    """Resolve IMDb IDs to Wikidata items with their maker and origin facts.

    IMDb ID is the join key, and the only one used. Titles collide constantly —
    a measured probe found "Panchayat" matching four Wikidata items, "The
    Family Man" six, "The Bear" seven — so this never falls back to matching by
    name (PLAN.md 8.5). An ID with no Wikidata item is simply absent from the
    result, which the caller reports as thin enrichment rather than hiding.

    Returns {imdb_id: {qid, label, director: [...], writer: [...], ...}}.
    """
    ids = [i for i in dict.fromkeys(imdb_ids) if i]
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), batch):
        chunk = ids[start : start + batch]
        rows = _sparql(
            f"""
            SELECT ?imdb ?item ?itemLabel ?prop ?value ?valueLabel WHERE {{
              {_values_clause(chunk)}
              ?item wdt:P345 ?imdb .
              VALUES ?prop {{ wdt:P57 wdt:P58 wdt:P86 wdt:P161 wdt:P179
                             wdt:P155 wdt:P156 wdt:P144 wdt:P364 wdt:P495 wdt:P136 }}
              OPTIONAL {{ ?item ?prop ?value . }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}
            """
        )
        _collect(rows, out)
    return out


_PROP_BY_PID = {f"http://www.wikidata.org/prop/direct/{pid}": name for name, pid in PROPERTIES.items()}


def _collect(rows: list[dict[str, Any]], out: dict[str, dict[str, Any]]) -> None:
    """Fold (imdb, prop, value) triples into one record per title."""
    for row in rows:
        imdb = row.get("imdb")
        if not imdb:
            continue
        rec = out.setdefault(
            imdb,
            {"imdb": imdb, "qid": _qid(row.get("item")), "label": row.get("itemLabel")},
        )
        name = _PROP_BY_PID.get(row.get("prop") or "")
        value_qid = _qid(row.get("value"))
        if not name or not value_qid:
            continue
        bucket = rec.setdefault(name, [])
        entry = {"qid": value_qid, "label": row.get("valueLabel") or value_qid}
        if entry not in bucket:
            bucket.append(entry)


def other_works(qids: Iterable[str], relation: str, *, limit: int = 40) -> list[dict[str, Any]]:
    """Other films and shows connected to these people or works.

    relation: one of director, writer, composer, cast, series, based_on.

    Every result carries an IMDb ID, because a candidate that cannot cross back
    into Simkl's ID namespace cannot be excluded reliably (PLAN.md 6.2) and so
    has no business being recommended.
    """
    if relation not in PROPERTIES:
        raise WikidataError(
            f"Unknown relation {relation!r}. Known: {', '.join(sorted(PROPERTIES))}."
        )
    people = [q for q in dict.fromkeys(qids) if q]
    if not people:
        return []
    pid = PROPERTIES[relation]
    values = " ".join(f"wd:{q}" for q in people)
    rows = _sparql(
        f"""
        SELECT DISTINCT ?work ?workLabel ?imdb ?via ?viaLabel ?year WHERE {{
          VALUES ?via {{ {values} }}
          ?work wdt:{pid} ?via .
          ?work wdt:P345 ?imdb .
          OPTIONAL {{ ?work wdt:P577 ?date . BIND(YEAR(?date) AS ?year) }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT {int(limit)}
        """
    )
    return [
        {
            "qid": _qid(r.get("work")),
            "title": r.get("workLabel"),
            "imdb": r.get("imdb"),
            "via": {"qid": _qid(r.get("via")), "label": r.get("viaLabel")},
            "year": int(r["year"]) if r.get("year") and str(r["year"]).isdigit() else None,
            "signal": relation,
        }
        for r in rows
        if r.get("imdb")
    ]


def maker_neighbours(
    record: dict[str, Any],
    *,
    signals: Iterable[str] = MAKER_SIGNALS,
    per_person: int = 12,
) -> list[dict[str, Any]]:
    """Candidates reachable from one seed through its makers.

    `per_person` caps what a single director or writer contributes. re-com's
    longest-running defect was an artist-centric graph where two seeds sharing
    an artist returned nearly the same results (PLAN.md 6.1); the video
    equivalent is two seeds by one director agreeing on that director's whole
    filmography. The cap is the first mitigation, and it is a measured number
    to tune, not a solved problem.
    """
    found: list[dict[str, Any]] = []
    seed_imdb = record.get("imdb")
    for signal in signals:
        people = [p["qid"] for p in record.get(signal) or []]
        if not people:
            continue
        for person in people:
            for work in other_works([person], signal, limit=per_person):
                if work["imdb"] and work["imdb"] != seed_imdb:
                    found.append({**work, "seed_imdb": seed_imdb})
    return found


def coverage(record: dict[str, Any]) -> dict[str, bool]:
    """Which enrichment facts this title actually has.

    Reported alongside recommendations rather than kept internal: a seed with
    no director and no language is a thin signal, and PLAN.md's third hard
    requirement is that thin signals are stated, never silently degraded.
    """
    return {name: bool(record.get(name)) for name in
            ("director", "writer", "composer", "cast", "series", "language", "country", "genre")}
