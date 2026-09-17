#!/usr/bin/env python3
"""Phase 0: measure the two signals before building a recommender on them.

PLAN.md 9.1. This answers, per seed title:

1. How many viewer-based neighbours does Simkl carry (`users_recommendations`)?
   The decisive number -- if it is near zero for Indian titles, the maker
   signals have to carry that catalogue and the plan has to say so.
2. What share of those neighbours carry an IMDb ID, the Wikidata join key?
3. Does Wikidata have the title, and does it have a director, composer,
   language?
4. How much do the two signals overlap (Jaccard)? Near zero means they are
   independent -- expect variety, not corroboration (PLAN.md 4.4).
5. For two seeds by one director, how much do their results share? This is the
   concentration risk that was re-com's longest-running defect (PLAN.md 6.1).

Usage:
    export SIMKL_CLIENT_ID=...            # simkl.com/settings/developer
    python scripts/probe.py --seeds scripts/seeds.json --out probe_results.json

The Simkl half needs only a client_id -- detail records are public reads, so no
login is required for this. The Wikidata half needs nothing at all, and
`--skip-simkl` runs it alone.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wikidata as wd  # noqa: E402

SIMKL_BASE = "https://api.simkl.com"
APP_NAME = "re-com-video-probe"
APP_VERSION = "0.1.0"


def _simkl_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """One public Simkl read. Deliberately not importing simkl-mcp's client:
    the probe must be runnable before that repo is even installed."""
    import requests

    client_id = os.environ.get("SIMKL_CLIENT_ID")
    if not client_id:
        raise SystemExit(
            "Set SIMKL_CLIENT_ID. Register a free app at "
            "https://simkl.com/settings/developer (the PIN flow needs no "
            "client_secret, so any redirect URI will do)."
        )
    query = dict(params or {})
    query.update({"client_id": client_id, "app-name": APP_NAME, "app-version": APP_VERSION})
    resp = requests.get(
        f"{SIMKL_BASE}{path}",
        params=query,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code == 412:
        raise SystemExit(f"Simkl rejected the client_id (412): {resp.text[:200]}")
    if resp.status_code != 200:
        return None
    # Detail endpoints are Cloudflare-cached, but everything here stays
    # sequential and spaced anyway -- 10 GET/s is the ceiling and a sustained
    # overage suspends the client_id.
    time.sleep(0.15)
    return resp.json()


def resolve_seed(seed: dict[str, Any]) -> dict[str, Any] | None:
    """IMDb ID -> Simkl ID, via /redirect's Location header (never followed)."""
    import requests

    client_id = os.environ.get("SIMKL_CLIENT_ID", "")
    resp = requests.get(
        f"{SIMKL_BASE}/redirect",
        params={
            "imdb": seed["imdb"],
            "client_id": client_id,
            "app-name": APP_NAME,
            "app-version": APP_VERSION,
        },
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
        allow_redirects=False,
        timeout=30,
    )
    location = resp.headers.get("Location", "")
    parts = [p for p in location.split("?")[0].split("/") if p]
    for i, part in enumerate(parts):
        if part in ("movies", "tv", "anime") and i + 1 < len(parts) and parts[i + 1].isdigit():
            return {
                "simkl_id": int(parts[i + 1]),
                "type": "movie" if part == "movies" else part,
            }
    return None


def probe_seed(seed: dict[str, Any], *, skip_simkl: bool = False) -> dict[str, Any]:
    """Everything PLAN.md 9.1 asks about one title."""
    out: dict[str, Any] = {
        "title": seed.get("title"),
        "imdb": seed.get("imdb"),
        "group": seed.get("group"),
        "errors": [],
    }

    # --- Simkl: the viewer signal
    if not skip_simkl:
        try:
            resolved = resolve_seed(seed)
            if not resolved:
                out["errors"].append("Simkl did not resolve this IMDb ID")
            else:
                out.update(resolved)
                kind = {"movie": "/movies", "tv": "/tv", "anime": "/anime"}[resolved["type"]]
                detail = _simkl_get(f"{kind}/{resolved['simkl_id']}", {"extended": "full"})
                neighbours = (detail or {}).get("users_recommendations") or []
                out["viewer_neighbours"] = len(neighbours)
                # Measured 2026-09-16: a users_recommendations entry carries only
                # ids.simkl and ids.slug -- never an imdb id. So the join key has
                # to be fetched with a second hop per neighbour. Those detail
                # endpoints are the Cloudflare-cached ones Simkl explicitly allows
                # hammering, which is what makes this affordable.
                out["viewer_ids_inline"] = sorted(
                    {k for n in neighbours for k in (n.get("ids") or {})}
                )
                out["viewer_strength_present"] = sum(
                    1 for n in neighbours if n.get("users_percent") or n.get("users_count")
                )
                imdb_ids = []
                for n in neighbours:
                    nid = (n.get("ids") or {}).get("simkl")
                    ntype = n.get("type") or "tv"
                    if nid is None:
                        continue
                    seg = {"movie": "/movies", "tv": "/tv", "anime": "/anime"}.get(ntype, "/tv")
                    nd = _simkl_get(f"{seg}/{int(nid)}", {"extended": "full"})
                    got = ((nd or {}).get("ids") or {}).get("imdb")
                    if got:
                        imdb_ids.append(got)
                out["viewer_neighbours_with_imdb"] = len(imdb_ids)
                out["viewer_imdb_share"] = (
                    round(len(imdb_ids) / len(neighbours), 3) if neighbours else None
                )
                out["_viewer_imdb_set"] = sorted(set(imdb_ids))
                out["simkl_runtime"] = (detail or {}).get("runtime")
                out["simkl_language"] = (detail or {}).get("language")
                out["simkl_country"] = (detail or {}).get("country")
        except Exception as e:  # noqa: BLE001 - one dead seed must not sink the probe
            out["errors"].append(f"Simkl: {e}")

    # --- Wikidata: the maker signal
    try:
        rec = wd.lookup_by_imdb([seed["imdb"]]).get(seed["imdb"])
        out["wikidata_found"] = rec is not None
        if rec:
            out["wikidata_qid"] = rec.get("qid")
            out["wikidata_coverage"] = wd.coverage(rec)
            out["wikidata_language"] = [v["label"] for v in rec.get("language") or []]
            out["wikidata_director"] = [v["label"] for v in rec.get("director") or []]
            out["wikidata_director_qids"] = [v["qid"] for v in rec.get("director") or []]
            neighbours = wd.maker_neighbours(rec, per_person=12)
            out["maker_neighbours"] = len(neighbours)
            out["maker_by_signal"] = {
                s: sum(1 for n in neighbours if n["signal"] == s) for s in wd.MAKER_SIGNALS
            }
            out["_maker_imdb_set"] = sorted({n["imdb"] for n in neighbours if n["imdb"]})
        else:
            out["maker_neighbours"] = 0
            out["_maker_imdb_set"] = []
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"Wikidata: {e}")
        out.setdefault("_maker_imdb_set", [])

    # --- Question 4: are the two signals independent or corroborating?
    viewer = set(out.get("_viewer_imdb_set") or [])
    maker = set(out.get("_maker_imdb_set") or [])
    union = viewer | maker
    out["viewer_maker_overlap"] = len(viewer & maker)
    out["viewer_maker_jaccard"] = round(len(viewer & maker) / len(union), 3) if union else None
    return out


def concentration(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Question 5: do two seeds by the same director return the same titles?

    This is the video form of re-com's longest-running defect, where two seeds
    sharing an artist returned ~90% the same results. Measured here rather than
    assumed, because the mitigation (a per-person cap) needs a number.
    """
    by_director: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        for qid in r.get("wikidata_director_qids") or []:
            by_director.setdefault(qid, []).append(r)

    pairs = []
    for qid, seeds in by_director.items():
        if len(seeds) < 2:
            continue
        for i in range(len(seeds)):
            for j in range(i + 1, len(seeds)):
                a = set(seeds[i].get("_maker_imdb_set") or [])
                b = set(seeds[j].get("_maker_imdb_set") or [])
                union = a | b
                pairs.append(
                    {
                        "director_qid": qid,
                        "seeds": [seeds[i]["title"], seeds[j]["title"]],
                        "shared": len(a & b),
                        "jaccard": round(len(a & b) / len(union), 3) if union else None,
                    }
                )
    return pairs


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Group the numbers the way the decision gates in PLAN.md 9.1 read them."""

    def med(values: list[Any]) -> float | None:
        nums = [v for v in values if isinstance(v, (int, float))]
        return round(statistics.median(nums), 2) if nums else None

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        groups.setdefault(r.get("group") or "ungrouped", []).append(r)

    per_group = {
        name: {
            "seeds": len(rows),
            "median_viewer_neighbours": med([r.get("viewer_neighbours") for r in rows]),
            "median_maker_neighbours": med([r.get("maker_neighbours") for r in rows]),
            "median_viewer_imdb_share": med([r.get("viewer_imdb_share") for r in rows]),
            "wikidata_found": sum(1 for r in rows if r.get("wikidata_found")),
            "with_director": sum(
                1 for r in rows if (r.get("wikidata_coverage") or {}).get("director")
            ),
            "median_viewer_maker_jaccard": med([r.get("viewer_maker_jaccard") for r in rows]),
        }
        for name, rows in groups.items()
    }

    gates = []
    indian = per_group.get("indian", {})
    western = per_group.get("western", {})
    iv, wv = indian.get("median_viewer_neighbours"), western.get("median_viewer_neighbours")
    if iv is not None and wv:
        ratio = iv / wv if wv else None
        gates.append(
            {
                "gate": "Indian seeds' viewer neighbours vs Western",
                "value": f"{iv} vs {wv} (ratio {ratio:.2f})" if ratio else f"{iv} vs {wv}",
                "reading": (
                    "Maker signals must carry the Indian catalogue; say so in the plan."
                    if ratio is not None and ratio < 0.5
                    else "Viewer signal is comparable across catalogues."
                ),
            }
        )
    # Only meaningful when the viewer side actually ran. With --skip-simkl every
    # viewer set is empty, which makes the Jaccard a vacuous 0.0 -- reporting
    # that as "the sources are independent" would be exactly the silent
    # degradation this project's third hard requirement forbids.
    measured_viewer = [r for r in results if r.get("viewer_neighbours") is not None]
    if not measured_viewer:
        gates.append(
            {
                "gate": "Viewer vs maker overlap (Jaccard)",
                "value": "not measured",
                "reading": (
                    "The Simkl half did not run, so there is no viewer set to "
                    "compare against. This gate is undecided, not passed."
                ),
            }
        )
    else:
        jac = med([r.get("viewer_maker_jaccard") for r in measured_viewer])
        # A median alone hides the shape: most seeds can share nothing while a
        # few share a lot. Both numbers are reported, because "the sources are
        # independent" and "they are independent for most titles" have
        # different consequences for the scoring rule.
        any_overlap = sum(1 for r in measured_viewer if r.get("viewer_maker_overlap"))
        share = any_overlap / len(measured_viewer)
        worst = max((r.get("viewer_maker_jaccard") or 0) for r in measured_viewer)
        gates.append(
            {
                "gate": "Viewer vs maker overlap (Jaccard)",
                "value": (
                    f"median {jac}, max {round(worst, 3)}, "
                    f"{any_overlap}/{len(measured_viewer)} seeds share anything"
                ),
                "reading": (
                    "Largely independent: most seeds share nothing between the "
                    "two sources, so expect variety rather than corroboration, "
                    "and expect the agreement score to be driven by how many "
                    "SEEDS surfaced a title rather than how many sources did."
                    if share < 0.5
                    else "They overlap often: agreement between them is meaningful."
                ),
            }
        )
        if any_overlap and share < 0.5:
            gates[-1]["reading"] += (
                f" It is not zero though -- {any_overlap} seeds do overlap "
                "(up to Jaccard %.3f), so a cross-source agreement of 2 is rare "
                "but real and should not be treated as impossible." % worst
            )
    conc = concentration(results)
    if conc:
        worst = max(conc, key=lambda p: p["jaccard"] or 0)
        gates.append(
            {
                "gate": "Same-director concentration (worst pair)",
                "value": f"{worst['seeds']} share {worst['shared']} titles (Jaccard {worst['jaccard']})",
                "reading": (
                    "High: cap what one person contributes before shipping."
                    if (worst["jaccard"] or 0) > 0.5
                    else "Acceptable at the current per-person cap."
                ),
            }
        )

    return {
        "seeds": len(results),
        "errors": sum(len(r.get("errors") or []) for r in results),
        "per_group": per_group,
        "same_director_pairs": conc,
        "decision_gates": gates,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", default="scripts/seeds.json", help="JSON list of seed titles")
    ap.add_argument("--out", default="probe_results.json")
    ap.add_argument(
        "--skip-simkl",
        action="store_true",
        help="Run the Wikidata half only -- needs no key at all.",
    )
    args = ap.parse_args()

    seeds = json.loads(Path(args.seeds).read_text())
    missing = [s.get("title") for s in seeds if not s.get("imdb")]
    if missing:
        raise SystemExit(
            f"Every seed needs an imdb id (missing for: {', '.join(map(str, missing))}). "
            "Titles are not a join key -- 'The Bear' is seven distinct Wikidata items."
        )

    results = []
    for i, seed in enumerate(seeds, 1):
        print(f"[{i}/{len(seeds)}] {seed['title']}", flush=True)
        r = probe_seed(seed, skip_simkl=args.skip_simkl)
        for err in r["errors"]:
            print(f"        ! {err}", flush=True)
        print(
            f"        viewer={r.get('viewer_neighbours', '-')} "
            f"maker={r.get('maker_neighbours', '-')} "
            f"jaccard={r.get('viewer_maker_jaccard', '-')}",
            flush=True,
        )
        results.append(r)

    summary = summarize(results)
    Path(args.out).write_text(json.dumps({"summary": summary, "seeds": results}, indent=2))

    print("\n--- Summary ---")
    for name, g in summary["per_group"].items():
        print(
            f"{name:10s} n={g['seeds']:2d}  viewer(med)={g['median_viewer_neighbours']}  "
            f"maker(med)={g['median_maker_neighbours']}  "
            f"wikidata={g['wikidata_found']}/{g['seeds']}  "
            f"director={g['with_director']}/{g['seeds']}"
        )
    print("\n--- Decision gates (PLAN.md 9.1) ---")
    for gate in summary["decision_gates"]:
        print(f"* {gate['gate']}: {gate['value']}\n    -> {gate['reading']}")
    print(f"\nWritten to {args.out}. Fold the numbers back into PLAN.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
