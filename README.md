# re-com-video

Movie and TV recommendations that never suggest something you've already seen — the video
sibling of [re-com](https://github.com/umsachde/re-com).

> **Status: Phase 0 half measured, Phase 1 built.** The sibling server
> [`simkl-mcp`](https://github.com/umsachde/simkl-mcp) is written and tested; the Wikidata half of
> this engine is written and verified against the live query service. The recommender itself is
> next. Design, research and measurements are in [PLAN.md](PLAN.md).

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

## What the probe found (2026-09-15)

Measured over 20 seeds, Wikidata half only — the Simkl half is still blocked on a `client_id`.

- **Wikidata resolved 20/20 seeds by IMDb ID**, all with an original language. The worry that it
  would be thin on the Indian catalogue was the wrong worry: Indian seeds returned a *higher*
  median of maker-based neighbours (19) than Western ones (13.5).
- **The real gap is format, not region.** Every seed that returned zero maker neighbours is a
  series — *Kota Factory*, *The Bear*; *Panchayat* returned one. Wikidata's director and writer
  properties are film properties. **The maker signal is a movie signal**, which makes Simkl's
  viewer-based neighbours very likely the *only* signal for TV.
- **Music director carries Indian film**, as [PLAN.md §2.4](PLAN.md) guessed and more so —
  `composer` supplied 12 of 23 candidates for *12th Fail* and 12 of 14 for *Laapataa Ladies*.
- **Titles collide exactly as expected**: a live query matched "Panchayat" to 4 Wikidata items,
  "Parasite" to 6, "Severance" to 5. Nothing here joins on anything but an IMDb ID.

Full numbers and what they change: [PLAN.md §9.1a](PLAN.md#91a-phase-0--first-results-wikidata-half).

## Known limitation

The "never already seen" guarantee is only as complete as your history. Netflix history can be
imported; Crave, Prime Video, Disney+ and Max can't, so titles watched there need logging by
hand.

## License

MIT
