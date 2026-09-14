# re-com-video

Movie and TV recommendations that never suggest something you've already seen — the video
sibling of [re-com](https://github.com/umsachde/re-com).

> **Status: planning.** No code yet. The design, the research behind it and the roadmap are in
> [PLAN.md](PLAN.md).

## The idea

Tell it what you've watched and what you loved, and ask:

- **"More like *Panchayat*"** — or like several titles at once, or like one but as a movie, or
  in another language.
- **"What should I watch tonight?"** — under two hours, something Hindi, a short show to finish
  this weekend.

It recommends from the **whole catalogue**, not just one service. Each pick says why: which of
your titles it came from, and which signals agreed.

## How it will work

| Piece | Job |
| --- | --- |
| `re-com-video` (this repo) | read-only MCP engine: taste model, candidates, agreement scoring, filters, exclusion |
| `simkl-mcp` (planned sibling repo) | the only place a login lives; syncs history, logs "I watched X" |
| [Simkl](https://simkl.com) | watch history, ratings, and "viewers of this also watched" |
| [Wikidata](https://www.wikidata.org) | directors, writers, music directors, cast, series, language |

Both data sources are free for a personal project. Why TMDb, Trakt and JustWatch aren't used is
in [PLAN.md §4.1](PLAN.md#41-ruled-out).

## Known limitation

The "never already seen" guarantee is only as complete as your history. Netflix history can be
imported; Crave, Prime Video, Disney+ and Max can't, so titles watched there need logging by
hand.

## License

MIT
