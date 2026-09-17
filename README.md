# re-com-video

Movie and TV recommendations that never suggest something you've already seen — the video
sibling of [re-com](https://github.com/umsachde/re-com).

> **Status: Phase 0 measured, Phase 1 built.** The sibling server
> [`simkl-mcp`](https://github.com/umsachde/simkl-mcp) is written and tested, and the Phase 0
> probe has run in full against live Simkl and Wikidata. The recommender itself is next.
> Design, research and measurements are in [PLAN.md](PLAN.md).

## The idea

Tell it what you've watched and what you loved, and ask:

- **"More like *Panchayat*"** — or like several titles at once, or like one but as a movie, or
  in another language.
- **"What should I watch tonight?"** — under two hours, something Hindi, a short show to finish
  this weekend.

It recommends from the **whole catalogue**, not just one service. Each pick says why: which of
your titles it came from, and which signals agreed.

## How it works

| Piece | Job |
| --- | --- |
| `re-com-video` (this repo) | read-only MCP engine: taste model, candidates, agreement scoring, filters, exclusion |
| [`simkl-mcp`](https://github.com/umsachde/simkl-mcp) | the only place a login lives; syncs history, logs "I watched X" |
| [Simkl](https://simkl.com) | watch history, ratings, and "viewers of this also watched" |
| [Wikidata](https://www.wikidata.org) | directors, writers, music directors, cast, series, language |

Both data sources are free for a personal project. Why TMDb, Trakt and JustWatch aren't used is
in [PLAN.md §4.1](PLAN.md#41-ruled-out).

## What's here now

```
wikidata.py          maker-side signal: enrichment by IMDb ID, neighbour expansion
scripts/probe.py     Phase 0 — measures both signals before the engine is built on them
scripts/seeds.json   20 placeholder seeds. Replace with what you've actually watched.
tests/               24 unit tests, no network
```

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests -q

python scripts/probe.py --skip-simkl      # the Wikidata half needs no key at all
```

For the full probe, register a free app at
[simkl.com/settings/developer](https://simkl.com/settings/developer) and
`export SIMKL_CLIENT_ID=…` first.

## What the probe found (2026-09-16)

Measured over 20 seeds against live Simkl and Wikidata. Full numbers in
[PLAN.md §9.1a](PLAN.md).

- **Simkl's viewer signal is hard-capped at 12 per title.** Every seed in every catalogue
  returned 11 or 12 — it's a fixed-size list, not a measure of similarity. So a single-seed
  request has a ceiling of ~12 candidates from that source.
- **The geography worry was unfounded.** Indian and Western seeds both median 12 viewer
  neighbours (ratio 1.00), and Wikidata resolved 20/20 seeds by IMDb ID — with Indian seeds
  returning *more* maker-based neighbours (median 19) than Western (13.5).
- **v1 can recommend TV, on the viewer signal alone.** Wikidata's director and writer properties
  are film properties: *Kota Factory* and *The Bear* return zero maker neighbours. Both return a
  full 11–12 viewer neighbours, so no series is left without a signal — but a TV pick rests on
  one source, and says so.
- **The cross-source join costs a second hop.** A viewer neighbour carries only `ids.simkl` and
  `ids.slug`, never an IMDb ID. Resolving it through the cached detail endpoint works for
  100% of neighbours.
- **The two sources rarely agree**: 13 of 20 seeds share nothing, 7 share 1–5 titles (max
  Jaccard 0.235), and every TV seed shared zero. So the agreement score is driven by how many
  *seeds* surfaced a title, not how many sources — multi-seed requests are where the ranking
  means anything.
- **Music director carries Indian film**, more than expected — `composer` supplied 12 of 23
  candidates for *12th Fail* and 12 of 14 for *Laapataa Ladies*.

## Known limitation

The "never already seen" guarantee is only as complete as your history. Netflix history can be
imported; Crave, Prime Video, Disney+ and Max can't, so titles watched there need logging by
hand.

## License

MIT
