# scripts

## `probe.py` — Phase 0

The measurement PLAN.md §9.1 requires before any of the engine is built.

```bash
export SIMKL_CLIENT_ID=...          # simkl.com/settings/developer
python scripts/probe.py --seeds scripts/seeds.json --out probe_results.json
```

`--skip-simkl` runs the Wikidata half alone, which needs no key of any kind.

## `seeds.json`

**Replace these with 20 titles you have actually watched.** The list shipped here
is a runnable placeholder, disambiguated by IMDb ID, not a claim about anyone's
taste — the probe measures how the signals behave *on your catalogue*, and a
stranger's seeds measure the wrong thing.

Keep the shape: `group` is one of `indian`, `western`, `other`, and the summary
compares medians across those groups (that comparison is decision gate 1). Every
seed needs an `imdb` ID — resolving by title is refused, because titles collide:
a live query on 2026-09-15 matched "Panchayat" to 4 Wikidata items, "Parasite"
to 6 and "Severance" to 5.
