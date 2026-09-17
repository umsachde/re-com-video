"""Local SQLite: the history mirror, the title caches, feedback and the served log.

Three jobs, in order of how much they matter:

1. **The exclusion set.** Every Simkl ID the user has any relationship with —
   watched, watching, on hold, dropped, plan-to-watch. This is what makes the
   guarantee in PLAN.md §1 checkable, and it is keyed by Simkl ID alone,
   because that is the one namespace everything resolves into (§6.2).
2. **The taste model.** §5.1's table, turned into weights. A rating of 9 and a
   show dropped after two episodes are both signals; they point opposite ways.
3. **The caches.** Simkl detail records and Wikidata enrichment, so a second
   request for the same title costs nothing. Measured 2026-09-16: joining
   Simkl's viewer neighbours to Wikidata needs one detail call *per neighbour*
   (§9.1a), so without a cache a 5-seed request would make 60 of them.

Nothing here talks to the network. It is handed rows and asked questions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PATH = os.environ.get(
    "RECOM_VIDEO_DB", str(Path.home() / ".re-com-video" / "store.db")
)

# §5.1, as numbers. Positive promotes, negative demotes, and nothing here ever
# decides *exclusion* -- that is status-independent and absolute.
_STATUS_WEIGHT = {
    "completed": 0.6,
    "watching": 0.5,
    "hold": 0.0,
    "dropped": -1.0,
    "plantowatch": 0.2,
}

# The threshold a title must clear to seed "what should I watch tonight".
# §5.1 reserves that for *strong* positives -- a rating of 8-10, or a series
# actually finished. A movie you completed and never rated is a weak positive:
# real evidence, but not enough to build a night's recommendation on when the
# alternative is something you gave a 9.
SEED_THRESHOLD = 1.0

# A rating overrides the status weight entirely: it is the user speaking
# directly, where a status is mostly a side effect of what they clicked.
_RATING_WEIGHT = {
    10: 2.0, 9: 2.0, 8: 1.6,
    7: 0.6, 6: 0.4, 5: 0.2,
    4: -0.8, 3: -1.2, 2: -1.6, 1: -2.0,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    simkl_id         INTEGER PRIMARY KEY,
    type             TEXT,
    title            TEXT,
    year             INTEGER,
    status           TEXT,
    rating           INTEGER,
    rated_at         TEXT,
    last_watched_at  TEXT,
    added_at         TEXT,
    watched_episodes INTEGER,
    total_episodes   INTEGER,
    imdb             TEXT,
    url              TEXT,
    synced_at        REAL
);
CREATE INDEX IF NOT EXISTS history_imdb ON history(imdb);
CREATE INDEX IF NOT EXISTS history_status ON history(status);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Simkl detail records, keyed by the one namespace that matters.
CREATE TABLE IF NOT EXISTS titles (
    simkl_id   INTEGER PRIMARY KEY,
    type       TEXT,
    title      TEXT,
    year       INTEGER,
    imdb       TEXT,
    payload    TEXT,
    fetched_at REAL
);
CREATE INDEX IF NOT EXISTS titles_imdb ON titles(imdb);

-- Wikidata enrichment, keyed by IMDb ID because that is the join key and the
-- only one this project will join on (§8.5).
CREATE TABLE IF NOT EXISTS enrichment (
    imdb       TEXT PRIMARY KEY,
    qid        TEXT,
    payload    TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS feedback (
    simkl_id INTEGER,
    reaction TEXT,
    at       REAL,
    PRIMARY KEY (simkl_id, at)
);

-- What was recommended, so §5.2's implicit feedback has something to diff
-- against later. Recorded from day one even though nothing reads it yet:
-- re-com learned this history is impossible to reconstruct after the fact.
CREATE TABLE IF NOT EXISTS served (
    simkl_id INTEGER,
    seeds    TEXT,
    at       REAL
);
CREATE INDEX IF NOT EXISTS served_id ON served(simkl_id);
"""


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_PATH
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- history ------------------------------------------------------------

    def upsert_history(self, items: Iterable[dict[str, Any]]) -> int:
        """Merge library rows in. Returns how many were written.

        Merge, never replace: a Simkl delta only carries what changed, so
        overwriting the table with a delta would silently empty the exclusion
        set (§4.2). Removals go through `remove_history`.
        """
        rows = [
            (
                int(i["simkl_id"]), i.get("type"), i.get("title"), i.get("year"),
                i.get("status"), i.get("rating"), i.get("rated_at"),
                i.get("last_watched_at"), i.get("added_at"),
                i.get("watched_episodes"), i.get("total_episodes"),
                i.get("imdb"), i.get("url"), time.time(),
            )
            for i in items
            if i.get("simkl_id") is not None
        ]
        self.conn.executemany(
            "INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(simkl_id) DO UPDATE SET "
            "type=excluded.type, title=excluded.title, year=excluded.year, "
            "status=excluded.status, rating=excluded.rating, "
            "rated_at=excluded.rated_at, last_watched_at=excluded.last_watched_at, "
            "added_at=excluded.added_at, watched_episodes=excluded.watched_episodes, "
            "total_episodes=excluded.total_episodes, imdb=excluded.imdb, "
            "url=excluded.url, synced_at=excluded.synced_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def remove_history(self, simkl_ids: Iterable[int]) -> int:
        """Drop titles the user removed from Simkl.

        Removing an item on Simkl also wipes its rating, and the row goes with
        it here -- which correctly makes the title recommendable again.
        """
        ids = [(int(i),) for i in simkl_ids]
        cur = self.conn.executemany("DELETE FROM history WHERE simkl_id = ?", ids)
        self.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(ids)

    def exclusion_ids(self) -> set[int]:
        """Every Simkl ID the user has any relationship with.

        Status-independent on purpose. One episode watched counts as seen
        (§5.3); a dropped show is still a show they have seen; plan-to-watch is
        excluded from discovery too, but the caller reports those differently
        ("already on your list") rather than silently.
        """
        return {r[0] for r in self.conn.execute("SELECT simkl_id FROM history")}

    def planned_ids(self) -> set[int]:
        """Plan-to-watch, which is excluded but deserves its own wording."""
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT simkl_id FROM history WHERE status = 'plantowatch'"
            )
        }

    def history_size(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    def get_history(self, simkl_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM history WHERE simkl_id = ?", (int(simkl_id),)
        ).fetchone()
        return dict(row) if row else None

    # --- taste --------------------------------------------------------------

    @staticmethod
    def weight(row: dict[str, Any]) -> float:
        """How strongly this history entry says "more like this" (§5.1).

        A rating speaks for itself and overrides the status. Otherwise the
        status decides, scaled by how much of a series was actually watched --
        2 episodes of 60 is not evidence of anything (§5.3).
        """
        rating = row.get("rating")
        if rating:
            base = _RATING_WEIGHT.get(int(rating), 0.0)
        else:
            base = _STATUS_WEIGHT.get(row.get("status") or "", 0.0)

        watched, total = row.get("watched_episodes"), row.get("total_episodes")

        # Finishing a series is a choice in a way finishing a film isn't -- it
        # is hours of deliberate return visits (§5.1). A series carried all the
        # way to the end earns a seed even unrated; a completed film doesn't.
        if (
            base > 0
            and row.get("status") == "completed"
            and row.get("type") in ("tv", "anime")
            and watched and total and watched >= total
        ):
            base = max(base, SEED_THRESHOLD)

        if base > 0 and watched and total and total > 1:
            # Only scales positives down. A show dropped after 2 of 60 episodes
            # is a *stronger* negative, not a weaker one, so negatives pass
            # through untouched.
            base *= max(0.15, min(1.0, watched / total))
        return round(base, 3)

    def seeds(self, limit: int = 12, min_weight: float = SEED_THRESHOLD) -> list[dict[str, Any]]:
        """The user's own titles, best first — the seeds for "tonight".

        Ties break on how recently it was watched, so "tonight" drifts with
        what they are actually into rather than returning the same answer
        forever.
        """
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM history")]
        scored = [(self.weight(r), r) for r in rows]
        picked = [
            (w, r) for w, r in scored
            if w >= min_weight and r.get("status") != "plantowatch"
        ]
        picked.sort(key=lambda p: (p[0], p[1].get("last_watched_at") or ""), reverse=True)

        # Spread the picks across the qualifying set rather than taking the top
        # `limit` contiguously. Measured on a real library: 70 films all rated
        # 8 produced six seeds that were all Spider-Man or early MCU, so every
        # one of them had the same neighbours and the request explored one
        # corner of the taste profile. Striding is deterministic, so a run
        # stays reproducible.
        if len(picked) > limit:
            stride = len(picked) / limit
            picked = [picked[int(i * stride)] for i in range(limit)]
        return [{**r, "taste_weight": w} for w, r in picked[:limit]]

    def dislikes(self) -> dict[int, float]:
        """Titles that point away — low ratings and drops. Demote, never exclude."""
        out = {}
        for r in self.conn.execute("SELECT * FROM history"):
            w = self.weight(dict(r))
            if w < 0:
                out[r["simkl_id"]] = w
        return out

    def taste_summary(self) -> dict[str, Any]:
        """What the history actually says, for `read_my_taste` and index_status."""
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM history")]
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        rated = []
        for r in rows:
            by_status[r.get("status") or "unknown"] = by_status.get(r.get("status") or "unknown", 0) + 1
            by_type[r.get("type") or "unknown"] = by_type.get(r.get("type") or "unknown", 0) + 1
            if r.get("rating"):
                rated.append(int(r["rating"]))
        return {
            "titles": len(rows),
            "by_status": by_status,
            "by_type": by_type,
            "rated": len(rated),
            "mean_rating": round(sum(rated) / len(rated), 2) if rated else None,
            "with_imdb": sum(1 for r in rows if r.get("imdb")),
        }

    # --- caches -------------------------------------------------------------

    def cache_title(self, simkl_id: int, type_: str, payload: dict[str, Any]) -> None:
        ids = payload.get("ids") or {}
        self.conn.execute(
            "INSERT INTO titles VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(simkl_id) DO UPDATE SET type=excluded.type, "
            "title=excluded.title, year=excluded.year, imdb=excluded.imdb, "
            "payload=excluded.payload, fetched_at=excluded.fetched_at",
            (
                int(simkl_id), type_, payload.get("title"), payload.get("year"),
                ids.get("imdb"), json.dumps(payload), time.time(),
            ),
        )
        self.conn.commit()

    def get_title(self, simkl_id: int, max_age: float | None = None) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload, fetched_at FROM titles WHERE simkl_id = ?", (int(simkl_id),)
        ).fetchone()
        if not row:
            return None
        if max_age is not None and time.time() - (row["fetched_at"] or 0) > max_age:
            return None
        return json.loads(row["payload"])

    def imdb_for(self, simkl_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT imdb FROM titles WHERE simkl_id = ?", (int(simkl_id),)
        ).fetchone()
        return row["imdb"] if row else None

    def cache_enrichment(self, imdb: str, record: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO enrichment VALUES (?,?,?,?) "
            "ON CONFLICT(imdb) DO UPDATE SET qid=excluded.qid, "
            "payload=excluded.payload, fetched_at=excluded.fetched_at",
            (imdb, record.get("qid"), json.dumps(record), time.time()),
        )
        self.conn.commit()

    def get_enrichment(self, imdb: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload FROM enrichment WHERE imdb = ?", (imdb,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    # --- sync bookkeeping ---------------------------------------------------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- feedback and the served log ---------------------------------------

    def record_feedback(self, simkl_id: int, reaction: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?,?,?)",
            (int(simkl_id), reaction, time.time()),
        )
        self.conn.commit()

    def feedback_for(self, simkl_id: int) -> list[str]:
        return [
            r["reaction"]
            for r in self.conn.execute(
                "SELECT reaction FROM feedback WHERE simkl_id = ? ORDER BY at", (int(simkl_id),)
            )
        ]

    def log_served(self, simkl_ids: Iterable[int], seeds: Iterable[int]) -> None:
        stamp = time.time()
        seed_json = json.dumps(sorted(int(s) for s in seeds))
        self.conn.executemany(
            "INSERT INTO served VALUES (?,?,?)",
            [(int(i), seed_json, stamp) for i in simkl_ids],
        )
        self.conn.commit()

    def times_served(self, simkl_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM served WHERE simkl_id = ?", (int(simkl_id),)
        ).fetchone()[0]

    # --- status -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """What `index_status` reports: size, freshness, and the known gaps."""
        taste = self.taste_summary()
        return {
            "history_titles": taste["titles"],
            "by_status": taste["by_status"],
            "by_type": taste["by_type"],
            "rated": taste["rated"],
            "history_with_imdb": taste["with_imdb"],
            "cached_titles": self.conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
            "cached_enrichment": self.conn.execute("SELECT COUNT(*) FROM enrichment").fetchone()[0],
            "last_sync": self.get_state("last_activity_all"),
            "feedback_entries": self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0],
            "known_gaps": [
                "The exclusion guarantee is only as complete as this history. "
                "Simkl cannot import Prime Video, Disney+, Max or Hulu, and "
                "nothing imports Crave -- anything watched there and never "
                "logged can still be recommended.",
            ],
        }
