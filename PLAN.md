# re-com-video — Design, Research & Roadmap

The movie and TV sibling of [re-com](https://github.com/umsachde/re-com). **Status: Phase 0 half
measured, Phase 1 built.** This document carries the design, the research, what was ruled out and
why, and what has been measured so far.

**What exists as of 2026-09-15**

| | |
| --- | --- |
| `simkl-mcp` | Built (§7.1) — client, PIN auth, sync, detail reads, ID resolution, writes; 84 unit tests, no network. Not yet run against a live account: needs a `client_id`. |
| `wikidata.py` | Built and **verified live** — enrichment and maker-neighbour expansion; 24 unit tests. |
| `scripts/probe.py` | Built. Wikidata half **run** (§9.1a); Simkl half blocked on a `client_id`. |
| The engine (§7.2) | Not started. Phase 2. |

**The one thing blocking the rest:** a free Simkl app registered at
[simkl.com/settings/developer](https://simkl.com/settings/developer), which yields the `client_id`
that every Simkl call needs. Without it the decisive measurement (§9.1 question 1) cannot run.

It follows re-com's convention: one document, decisions with the alternative they beat, and
facts labelled by how well they are known. Every external claim carries the date it was
checked, because this project's first three assumptions about data sources were all false by
the time they were checked (§4.1).

**How facts are marked**

| Mark | Meaning |
| --- | --- |
| **Verified** | Read from the primary source (official docs, terms, or a live query), date given |
| **Reported** | From a secondary source (a news post, a third-party guide, a GitHub issue) |
| **Unverified** | Believed, not checked. Anything built on it must check it first |
| **Measured** | A number from a live probe run for this document |

Constraints this plan is held to: **a hobby project, zero budget** (no paid tier of anything),
the user is **in Canada**, and **Netflix** is the main streaming service — as a *source of
history*, not as a limit on what gets recommended (§1).

---

## 1. What this is

An MCP recommendation engine for movies and TV that returns titles **you have not already
seen**, ranked by how many independent signals agree, and explains every pick.

It recommends from **the whole catalogue**, not from what one service carries. Where something
streams is a separate question with no free, accurate, lawful answer today (§4.6), so it is not
allowed to gate recommendations. Netflix matters only because it is where most of the viewing
history comes from.

### The guarantee

> No result is ever a title already in your history — watched, in progress, on hold or dropped.

Same shape as re-com's, and the same consequences: exclusion is applied last and always, the
exclusion set has one ID namespace, and a gap in the history is stated rather than hidden.

**Stated limitation, from day one:** the guarantee is only as complete as the history. Simkl
cannot import Prime Video, Disney+, Max or Hulu history, and nothing imports Crave (§4.5).
Anything watched there and never logged can come back. Tool responses must say this, not imply
a Netflix-grade guarantee everywhere.

### Hard requirements

1. Never recommend a title already in the history (above).
2. **re-com-video never writes to your account.** Logging "I watched X" is done by the sibling
   `simkl-mcp` server, exactly as re-com leaves playlist writes to `ytmusic-mcp` (§7.1).
3. Degrade with a stated reason, never silently — a thin signal, an unresolvable title, a
   shortfall against `limit`, an ambiguous title match.
4. Hold no credentials. Simkl auth lives entirely in `simkl-mcp`.
5. Use only sources whose terms allow this use, for free (§4).

---

## 2. How you would use it

Everything someone might plausibly ask. v1 is the first two groups; the rest is roadmap (§10),
listed now so the data model does not paint them out.

### 2.1 v1 — "more like this"

| Ask | What it needs |
| --- | --- |
| "More like *Panchayat*" | one seed → neighbours, exclusion |
| "Something like *Kota Factory* and *Gullak* together" | several seeds → agreement across seeds |
| "Like *Past Lives*, but a show" | seed + a `type` filter that crosses movie ↔ TV |
| "Like *12th Fail*, but in English" / "Korean shows like *Panchayat*" | seed + a language filter |

### 2.2 v1 — "something for tonight"

No seed given; seeds come from your own highly rated and recently finished titles.

| Ask | What it needs |
| --- | --- |
| "What should I watch tonight?" | taste profile → candidates → exclusion |
| "A movie under two hours" | runtime filter (Simkl `runtime`, minutes) |
| "Something Hindi" / "not subtitled" | original-language filter (§4.3, §4.4) |
| "A short show I can finish this weekend" | type = TV, series ended, low episode count |
| "Something light" | genre in v1, stated honestly as genre; tone is a later layer (§10) |

### 2.3 Remembering what you watch (v1 plumbing, via `simkl-mcp`)

| Ask | Effect |
| --- | --- |
| "I just watched *Laapataa Ladies*, loved it" | mark watched + rating 9 |
| "I dropped *Heeramandi* after two episodes" | status → dropped (a negative signal) |
| "Put *Scam 1992* on my list" | status → plan to watch |
| "Import my Netflix history" | Simkl's Netflix import, or the CSV path (§4.5) |
| "Why did you recommend this?" | `explain_recommendation` — which signals, from which seeds |

### 2.4 Later — the rest of the ways

- **Watchlist triage** — "which of my Plan to Watch should I start?" Ranks a list you already
  have instead of discovering.
- **People** — "more from this director I haven't seen", "the writer of *Scam 1992*". Indian
  films specifically: *music director* (Wikidata composer) is a real taste axis.
- **Franchise and series** — "what comes next in this series?" (Wikidata follows / followed-by /
  part-of-series; Simkl anime `relations`).
- **Resume or abandon** — "should I go back to anything on hold?"
- **New and upcoming** — releases matching your taste (Simkl's free CDN calendar and premieres).
- **Surprise me** — deliberately outside the usual neighbourhood, with the distance reported.
- **Watching with someone** — blend two people's seeds; exclude what either has seen.
- **Read my taste** — "what do I actually like?", the analogue of re-com's `read_my_mood`.
- **Tone / mood** — "something tense", "comforting", "a slow burn", beyond genre (§10, phase 5).
- **Episode helper** — "where was I in *Mirzapur*?" (Simkl `next_to_watch`). Not a
  recommendation, but it falls out of the same data.

---

## 3. Architecture

Same layering as re-com, and the same split of responsibilities across sibling repos:

```
Projects/
  re-com/          music engine            (unchanged by this project)
  ytmusic-mcp/     YouTube Music auth + reads/writes
  spotify-mcp/     Spotify auth + reads/writes
  re-com-video/    video engine            (this repo)
  simkl-mcp/       Simkl auth + reads/writes, the only place a Simkl token lives
```

```
  tools        the MCP surface: validation, stated reasons, error translation
    |
  engine       seeding, candidate gathering, agreement scoring, filters, exclusion
    |
  knowledge    local store: history mirror, taste signals, feedback, served log
               title cache: Simkl detail records + Wikidata enrichment, keyed by IDs
    |
  sources      simkl-mcp (history + viewer-based neighbours + metadata)
               Wikidata (people, series, language, based-on) — public, no auth
```

**Why a separate repo, not a folder in re-com.** Verified by reading re-com: of its ~10,350
lines, the mood, tempo, lyrics, language-inference and music-graph code is music-specific. What
transfers is a ~30-line scoring rule (`signals._merge_and_score`: count distinct
(seed, source) pairs), the exclusion-last structure, and the verification approach. Copying
those ideas is cheaper than sharing code between two projects that share no logic.

**Build `simkl-mcp`, don't adopt the existing one.** An MCP server for Simkl already exists
([srevinsaju/simkl-mcp](https://github.com/srevinsaju/simkl-mcp), reported: TypeScript on
Cloudflare Workers, 23 tools, MIT, 7 commits). It is a general wrapper; this project needs a
small local Python server shaped like `ytmusic-mcp`, whose payload shapes it controls — the
lesson of re-com §6.6 was that a wrapper's shape assumptions are where silent exclusion bugs
live. Worth reading as a reference, not as a dependency.

---

## 4. Research — sources, what was ruled out, and why

All checked 2026-09-13 unless stated.

### 4.1 Ruled out

| Source | Why not | Status |
| --- | --- | --- |
| **TMDb** | API terms §1.C ("Restrictions") say you must not use the APIs or content "in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application." §2.A also lists LLM chatbots as *commercial* use, which needs a paid written agreement. An MCP server called by Claude is squarely this. Terms last updated 2023-10-20. | Verified |
| **Trakt** | Creating an API app now requires an active VIP membership; apps owned by free accounts were deleted around 2026-07-31. Confirmed by a Trakt maintainer on 2026-08-07 in trakt/trakt-api#902. VIP is ~$60/yr (reported). Its streaming-service auto-sync is also VIP-only (reported). Otherwise the best-fitting product. | Verified (VIP gate), Reported (price) |
| **JustWatch** | Partner API only; no free developer access. | Reported |
| **MovieLens** | Research-use licence ("may not use this information for any commercial or revenue-bearing purposes without first obtaining permission"); newest stable set (ml-32m) stops at 2023-10-12. Possible offline signal later, nothing current. | Verified |
| **IMDb datasets** | Personal and non-commercial use; basics, ratings, crew and principals, **no similarity data**. | Verified |
| **TVmaze** | CC BY-SA, free, but TV only and no similarity endpoint. | Verified |
| **Scraping Netflix / Crave** | Against their terms, needs a stored password, breaks whenever the site changes. | Decision |

Two corrections to this project's own first draft, kept as a record: it assumed TMDb's
`/similar` was collaborative filtering (it is genres + keywords only; TMDb staff: "Similar does
not tend to yield very good results", 2018), and it assumed Trakt OAuth was a free phase-2 step.

### 4.2 Simkl — the primary source

**Terms (verified, api.simkl.org/api-rules):** free for "non-commercial apps and personal
projects"; commercial use free under $150/month revenue. No AI or ML clause in the API rules.
Required: link back to the specific Simkl page for any title shown; send `client_id`,
`app-name`, `app-version` on every request plus a `User-Agent`. Simkl asks apps not to use it
as a generic metadata CDN (rule 3) — this project uses it for tracking and discovery, which is
what it is for. **Unverified:** simkl.com's full site terms (Cloudflare blocked every fetch);
read them once logged in.

**Auth (verified):** PIN flow built for CLIs — no `client_secret`, no redirect URI. The user
types a 5-character code at simkl.com/pin; poll every 5 s; code expires in 15 min. Tokens
advertise `expires_in: 157680000` (~5 years) and there is **no refresh grant**; a 401 in
practice means the app was revoked.

**Limits (verified):** 10 GET/s and 1 POST/s, per `client_id` and per user token. Sustained
overage gets a `client_id` suspended. Detail endpoints (`/movies/{id}`, `/tv/{id}`,
`/anime/{id}`) are Cloudflare-cached and parallel calls to them are explicitly allowed;
everything else should be sequential. Sync writes are serialised per user with a 20-second lock.

**History (verified):** a two-phase sync. Once: `GET /sync/all-items/{shows|movies|anime}` for
the full library. After that: check `GET /sync/activities`; only if its `all` timestamp moved,
fetch `GET /sync/all-items?date_from=<that timestamp>`. `date_from` has no maximum age.
**Deletions are not in the delta** — when `removed_from_list` moves, refetch with
`extended=simkl_ids_only` and diff. Removing an item also wipes its rating.

**Statuses (verified):**

| Status | Movies | TV | Anime |
| --- | :-: | :-: | :-: |
| `watching` | — | ✓ | ✓ |
| `plantowatch` | ✓ | ✓ | ✓ |
| `hold` | — | ✓ | ✓ |
| `dropped` | ✓ | ✓ | ✓ |
| `completed` | ✓ | ✓ | ✓ |

Status says *where* an item is; `watched_episodes_count` / `total_episodes_count` say *how far*.
Writing `completed` for a still-airing show silently becomes `watching`. Rating an item not on
any list auto-files it (released movie → completed, other shows → watching).

**Identity (verified):** `ids.simkl` is an integer, globally unique and permanent — the primary
key. Every other ID is a string. `slug` is **not unique** (three different *Superman* movies
share one). **TMDB IDs are not unique across movie and TV.** External IDs in responses are
echo-only; resolve an external ID to a Simkl ID with `GET /redirect` and read the `Location`
header **without following the 301**. Requests also accept a `netflix` ID. Items can be
reclassified between movie, TV and anime; follow Simkl's classification.

**Neighbours (verified schema, unmeasured size):** every movie, TV and anime detail record
carries `users_recommendations` — "mini media objects suggested by Simkl based on this title's
viewers" (title, year, poster, type, simkl id). This is the viewer-based signal, the analogue
of re-com's native radio. **How many per title, and how many for Indian titles, is unknown**
— the first thing Phase 0 measures (§9.1). Anime records also carry `relations` (prequel,
sequel, side story).

**Metadata (verified schema):** movie records include `runtime` (minutes), `director`,
`genres`, `country` (ISO 3166-1), `language` (ISO 639-1, uppercase), certification, ratings and
alternate titles. TV records include `runtime`, `country`, `network`, `genres`, `status` and
episode counts; a TV `language` field was not seen in the schema (unverified either way).

**Silent fallbacks to design around (verified, genre browse):** an unknown `country` or
`network` segment is silently treated as `all`; an unknown `year` as `all`; an unknown genre
returns top-level `null`, not `[]`. No 400s. This is exactly the class of silent failure re-com
§6.6 paid for — any use must assert the filter took effect.

**Nulls (verified):** Simkl documents five distinct meanings of `null` (never happened, doesn't
apply, empty result, unknown, end state). `watched_at` near `1970-01-01T00:00:01Z` means "a
long time ago", not a corrupt date.

### 4.3 Wikidata — people, series and language

**Licence (verified):** all structured data is CC0, public domain. No terms problem of any kind.

**Query service (verified):** `https://query.wikidata.org/sparql`. 60-second hard timeout;
60 s of processing time per 60 s per client (User-Agent + IP); 30 error queries per minute;
5 parallel queries per IP; a descriptive `User-Agent` is required or the client may be blocked.
**Measured:** one country-wide aggregate over US films timed out at 60 s. At runtime, look up
titles individually (or in small batches by ID), never as big aggregate queries.

**Properties (verified live):**

| Use | Property |
| --- | --- |
| Join key | IMDb ID `P345` |
| People | director `P57`, cast member `P161`, screenwriter `P58`, composer `P86` |
| Series | part of the series `P179`, follows `P155`, followed by `P156` |
| Source material | based on `P144` |
| Language / origin | original language `P364`, country of origin `P495`, genre `P136` |
| Cross-refs | TMDB movie `P4947`, TMDB TV `P4983`, Letterboxd film `P6127`, Trakt.tv `P8013` |
| Streaming IDs | Netflix `P1874`, JioHotstar `P11049`, Prime Video `P8055`, Disney+ `P7595`/`P7596`, Apple TV `P9586`/`P9751`, HBO Max `P8298`, Tubi `P7760`/`P7761` — **no Crave property exists** |

**Coverage (measured, films with an IMDb ID released 2015 or later):**

| Country | Films | Director | Cast | Genre | Writer | Language |
| --- | --: | --: | --: | --: | --: | --: |
| India | 5,377 | 62% | 69% | 62% | 22% | 92% |
| Canada | 1,947 | 89% | 45% | 80% | 39% | 74% |

**Spot check (measured):** 12th Fail, Laapataa Ladies, Jawan, Pathaan, RRR, Kantara, Stree 2,
Heeramandi, Sacred Games, Gangs of Wasseypur, Panchayat, Kota Factory and The Family Man (2019)
all resolve to an item with an IMDb ID, a director or cast, an original language and India as
country. Wikidata is **not** weak on this catalogue, which matters because re-com's hardest
problem was exactly that catalogue.

**Ambiguity (measured):** titles collide constantly. "Panchayat" matched four items (Hindi,
Punjabi and Nepali works); "The Family Man" six; "The Bear" seven; "Dangal" three. **Never join
on title.** Join Simkl → Wikidata on IMDb ID; fall back to title + year + type only with a
stated low-confidence note.

### 4.4 How the two sources fit together

- Simkl answers **whose taste this is** (history, ratings, statuses) and **what the same viewers
  watched** (`users_recommendations`).
- Wikidata answers **what connects titles by their makers and material** — same director,
  writer, music director, cast, series, source novel.
- They are different kinds of evidence. re-com §7.10 learned that *independent* sources give
  variety while *agreeing* sources give confidence, and that expecting one to deliver the other
  was the mistake. Phase 0 measures overlap between them before either is trusted (§9.1).

### 4.5 Getting your history in

| Source | How | Ongoing? | Status |
| --- | --- | --- | --- |
| **Netflix → Simkl** | Simkl's importer, plus a Simkl browser extension that tracks Netflix going forward | Extension: yes | Reported (Simkl docs; the import page itself returned 403) |
| **Netflix "Download all"** | Account → profile → Viewing activity → *Download all*; CSV, **per profile** | One-off | Verified (Netflix Help Center) |
| **Netflix personal data export** | Account → Security & privacy → *Download your personal information*; a zip within ~30 days containing `CONTENT_INTERACTION/ViewingActivity.csv` | One-off | Reported |
| **Crave** | In-app History under My Cravings, per profile. **No export found.** | — | Reported (no export); log manually |
| Prime Video, Disney+, Max, Hulu | Simkl says it cannot import these: no history page and no API | — | Verified (Simkl docs) |
| IMDb, Letterboxd, Trakt, TV Time, MyAnimeList, CSV | Simkl one-time importers | One-off | Verified (Simkl docs) |

`ViewingActivity.csv` columns: **Profile Name, Start Time, Duration, Title** are confirmed by
two independent sources; *Attributes, Supplemental Video Type, Device Type, Bookmark, Latest
Bookmark, Country* are reported by one. Titles are strings such as "Show: Season 1: Episode"
that must be parsed and matched. **If `Duration` holds, it separates "watched" from "sampled
for four minutes" — an implicit signal nothing else gives.** Confirm on a real export before
designing around it.

**Action worth taking now:** request the Netflix personal-data export today. It takes up to 30
days, and Phase 0 wants it.

**Household profiles:** both Netflix exports are per profile. Import only your own, or someone
else's taste becomes yours.

### 4.6 "Where can I watch it?" — deliberately out of v1

| Candidate | Why it doesn't answer the question |
| --- | --- |
| TMDb watch providers (JustWatch data) | TMDb's AI restriction (§4.1) |
| JustWatch API | Partner-only |
| Simkl `search/random?service=netflix` | Means "has a Netflix link", not "available in Canada"; its `country` filter means "released in that country" (verified) |
| Wikidata streaming IDs | Not region-aware, often stale, and sparse — **measured:** of 5,377 Indian films since 2015, 347 (6.5%) have a Netflix ID, 75 (1.4%) a JioHotstar ID, 8 (0.15%) a Prime Video ID. No Crave property at all. |

So v1 recommends from the whole catalogue and, at most, says "has been on Netflix" when Wikidata
or Simkl carries a link, worded as a hint. Revisit only if a free, lawful, region-aware source
appears.

---

## 5. The taste model

re-com only knew "in your library or not". Video carries more, and using it is most of the
quality.

### 5.1 What each piece of history means

| State | Excluded from results? | Taste signal |
| --- | --- | --- |
| Rated 8–10 | yes | **strong positive** — a seed for "tonight" |
| Rated 5–7 | yes | weak positive |
| Rated 1–4 | yes | **negative** — demotes neighbours |
| Movie `completed`, unrated | yes | weak positive |
| Show `completed`, unrated | yes | positive (finishing a series is a choice) |
| Show `watching` | yes | positive |
| Show `hold` | yes | neutral |
| `dropped` | yes | **negative** |
| `plantowatch` | yes, from discovery results — reported as "already on your list" | mild positive intent |
| Example titles you give ("I like these") | only if actually watched | **strongest positive** — explicit seeds |
| Netflix row with a short `Duration` (if confirmed, §4.5) | no — never started properly | weak negative |

Explicit examples beat raw history: ten titles you love say more than five hundred you
half-watched. v1 should make giving them easy.

### 5.2 Learning without being asked

re-com §4.13 carries over directly: diff *what was recommended* against *what later appears in
history*. Played → strong. Not played after enough history syncs → weak, **demotes, never
excludes**. Learned preferences apply to the direction (director, genre, language), bounded,
and are always reported.

### 5.3 TV is not a movie

A show has partial states music never had. Decisions:

- One episode watched counts as **seen** for exclusion. Recommending a show you started is
  never "new".
- `dropped` is the clearest negative signal in the whole model; use it.
- A long-running show you watched 2 of 60 episodes of is not evidence you liked it; weight by
  `watched_episodes_count / total_episodes_count` where both exist.

---

## 6. Candidates and ranking

### 6.1 Signals

| Signal | Source | Kind |
| --- | --- | --- |
| `viewers` | Simkl `users_recommendations` of the seed | viewer-based |
| `director` | other titles by the seed's director (Wikidata `P57`) | maker |
| `writer` | same screenwriter (`P58`) | maker |
| `music` | same composer / music director (`P86`) | maker |
| `cast` | shared principal cast (`P161`) — capped, see below | maker |
| `series` | same series, prequel or sequel (`P179`, `P155`, `P156`; Simkl anime `relations`) | franchise |
| `source` | same source material (`P144`) | franchise |

**Score = number of distinct (seed, signal) pairs that surfaced a title** — re-com's rule
unchanged, because it gives a ranking and an explanation at once and degrades gracefully when a
signal is empty.

**Known risk, stated before building:** maker signals are the video equivalent of re-com's
artist-centric graph. re-com's longest-running defect (§7.10–§7.14) was that seeds sharing an
artist returned 90% the same results. Here: two seeds by the same director will agree on that
director's whole filmography. Mitigations to test, not assume — cap how many results one person
contributes, don't let `series` + `director` + `cast` from the same franchise count as three
independent votes, and measure concentration (§9).

**Cast lists are long.** A blockbuster lists dozens of cast members, so "shares a cast member"
is noise unless restricted to billed principals. Whether Wikidata's cast ordering is reliable
enough for that is **unverified**.

### 6.2 Identity and exclusion

- Every candidate is a Simkl ID. Wikidata candidates cross into Simkl via IMDb ID → `/redirect`.
  **One ID namespace**, so re-com's second-stage title/artist text exclusion index is not needed
  — unless a candidate can only be matched by title, in which case it carries a low-confidence
  flag and is excluded by text as well.
- Exclusion runs **last**, after ranking and every filter, against the synced history.
- TMDB IDs never serve as keys (not unique across movie and TV).

### 6.3 Filters

type (movie / TV / anime), runtime, original language, genre, series status (ended),
episode count, minimum rating. Every filter reports what it removed. A filter that silently
falls back to "all" (§4.2) is a bug.

---

## 7. Tool surface (v1, design only)

### 7.1 `simkl-mcp` — auth, reads, writes

| Tool | Simkl endpoint |
| --- | --- |
| `login()` | PIN flow; stores the token locally |
| `get_activities()` | `GET /sync/activities` |
| `get_library(type, status, date_from)` | `GET /sync/all-items/...` |
| `get_title(simkl_id, type)` | `GET /movies/{id}`, `/tv/{id}`, `/anime/{id}` |
| `resolve_id(imdb / tmdb+type / netflix)` | `GET /redirect`, read `Location`, don't follow |
| `search(query, type)` | `GET /search/{type}` |
| `mark_watched(items)` | `POST /sync/history` (batched) |
| `set_status(items, status)` | `POST /sync/add-to-list` |
| `rate(items, rating)` | `POST /sync/ratings` |
| `remove(items)` | `POST /sync/history/remove` |
| `logout()` | delete the local token |

### 7.2 `re-com-video` — read-only engine

| Tool | Does |
| --- | --- |
| `recommend_from_titles(titles, type?, language?, runtime_max?, limit?)` | §2.1 |
| `recommend_for_tonight(type?, language?, runtime_max?, genre?, finishable?, limit?)` | §2.2 |
| `explain_recommendation(title)` | which signals, from which seeds |
| `read_my_taste()` | what the history says you like |
| `refresh_library(ids?)` | resync; `ids` adds just-logged titles instantly (re-com §7.5's lesson, built in from the start) |
| `record_feedback(title, reaction)` | local, not written to Simkl |
| `index_status()` | history size, last sync, enrichment coverage, known gaps |

Every result links to its Simkl page (Simkl rule 1) and lists `sources`.

---

## 8. Decisions, and what they beat

**8.1 Simkl over Trakt.** *Rejected: Trakt.* Better product (personal recommendations, broader
streaming auto-sync), but API access now costs ~$60/yr and the budget is zero.

**8.2 No TMDb, even for metadata.** *Rejected: use it anyway — nobody enforces against a hobby
project.* Probably true, and not a foundation to build on. Simkl and Wikidata cover what's
needed.

**8.3 Whole catalogue, not "on my services".** *Rejected: filter to Netflix.* The user asked for
this explicitly, and there's no free region-aware availability source anyway (§4.6).

**8.4 Read-only engine, separate write server.** *Rejected: one server that recommends and logs.*
re-com §4.9: a recommender that also mutates the history can't be trusted to have excluded what
it just added. Unlike re-com, the resync step is designed in from day one (`refresh_library(ids)`).

**8.5 Join on IDs, never titles.** Measured: "The Bear" is seven Wikidata items (§4.3).

**8.6 Probe before building.** *Rejected: build v1 and see.* re-com spent months (§7.10–§7.14)
discovering regional coverage gaps after building. Here the probe costs nothing: a free Simkl
key and public Wikidata.

---

## 9. Verification

### 9.1 Phase 0 — the probe (research, before any product code)

**Setup:** a free Simkl developer app, PIN login, a Wikidata `User-Agent`.

**Seeds:** 20 titles you have actually watched — about 8 Indian (Hindi and at least one other
language), 8 Western, 4 other (e.g. Korean); a mix of movies and shows.

**Measure, per seed:**

1. `users_recommendations` count. The decisive number.
2. Share of those neighbours that carry an IMDb ID (the Wikidata join key).
3. Wikidata item found via IMDb ID? Director, cast, composer, language present?
4. Overlap between Simkl's viewer neighbours and Wikidata's maker neighbours (Jaccard) —
   independence vs agreement (§4.4).
5. Same-director concentration: for two seeds by one director, share of results in common.

**History fidelity:**

6. Netflix "Download all" row count vs Simkl library items after import; list what didn't match.
7. When the personal-data export arrives: confirm the `Duration` column (§4.5).

**Decision gates:**

- Indian seeds' median neighbour count far below Western seeds → maker signals must carry that
  catalogue, and the plan says so.
- Viewer ∩ maker overlap near zero → they're independent: expect variety, not corroboration.
- Import drops many titles → exclusion needs an explicit "unmatched history" report.

### 9.1a Phase 0 — first results (Wikidata half)

**Measured 2026-09-15** by `scripts/probe.py --skip-simkl` over 20 placeholder seeds (9 Indian,
8 Western, 3 other; a mix of films and series). The Simkl half has not run — it needs a
`client_id` — so questions 1, 2 and 4 are **undecided, not passed**.

| Group | Seeds | Wikidata found | Has director | Median maker neighbours |
| --- | --: | --: | --: | --: |
| Indian | 9 | 9/9 | 8/9 | **19** |
| Western | 8 | 8/8 | 6/8 | **13.5** |
| Other (KR, JP) | 3 | 3/3 | 3/3 | **25** |

**1. The geography worry was the wrong worry.** Decision gate 1 expected Indian seeds to be the
thin ones. On the maker signal they are the *thickest* — 20/20 seeds resolved by IMDb ID, all 20
carried an original language, and the Indian median beats the Western one. Wikidata is not weak
on this catalogue.

**2. The real gap is format, not region — and it is new.** Every seed that returned **zero**
maker neighbours is a series: *Kota Factory* (0), *The Bear* (0), *Panchayat* (1). *Succession*
has no director in Wikidata at all and survives only on its composer. P57/P58 are film
properties; series credit episode directors, who are not modelled the same way.

> **The maker signal is a movie signal.** For TV it degrades to composer-only, and sometimes to
> nothing. That makes Simkl's `users_recommendations` not merely the better signal for TV but
> very likely the *only* one — which raises, rather than lowers, the stakes on the measurement
> still blocked. If TV neighbour counts also come back thin, v1 has no TV recommender and the
> plan has to say so plainly rather than shipping a thin one.

**3. Music director is the load-bearing Indian signal, as predicted — more so than predicted.**
`composer` (P86) fires on 6 of 9 Indian seeds and is usually the largest contributor: 12 of 23
for *12th Fail*, 12 of 14 for *Laapataa Ladies*. §2.4 listed music director as "a real taste
axis" for Indian film; measured, it carries that catalogue.

**4. `writer` (P58) is sparse**, returning nothing for 5 of 9 Indian seeds — consistent with the
22% writer coverage measured in §4.3. It earns a place in the signal list but not weight.

**5. Same-director concentration is real but tolerable so far.** *Gangs of Wasseypur* ↔ *Sacred
Games* (both Anurag Kashyap) share 10 candidate titles, Jaccard **0.312**, at a cap of 12 results
per person. Below the 0.5 line, and worth re-measuring with the viewer signal mixed in — this is
the defect that cost re-com §7.10–§7.14 the most time.

**6. Wikidata's query service returns transient `502`s.** Observed on the first live run, on a
query that succeeded unchanged moments later. Retried (bounded, 3 attempts) in `wikidata.py`; a
`429` and a query timeout deliberately are not, because retrying either makes it worse.

**7. Titles collide exactly as §8.5 claimed.** A live label query on 2026-09-15 matched
"Panchayat" to 4 Wikidata items, "Parasite" to 6, "Severance" to 5 and "The Family Man" to 6.
Both the probe and `wikidata.py` refuse to join on anything but an IMDb ID.

### 9.1b Two endpoints §7.1 missed

Found while building `simkl-mcp`, verified in the Simkl docs on 2026-09-15, and now in its tool
surface:

- **`POST /sync/watched`** — posts a batch of items and returns, per item, whether it is in the
  user's library, its status and when it was last watched. This is a *server-side* answer to the
  question the guarantee rests on. The engine excludes against its local mirror, which is only as
  fresh as the last sync; this makes the guarantee checkable rather than merely believed, and
  catches anything watched since. It belongs in §9.2's live smoke harness as the assertion behind
  "excludes history".
- **`GET /sync/ratings/{type}/{rating}`** — reads ratings back directly instead of inferring them
  from a full library pull. Ratings 8–10 are §5.1's strongest seeds, so fetching them should not
  require transferring the whole library.

One correction to §4.2's PIN notes: the `device_code` in the PIN response is the **literal string
`"DEVICE_CODE"`**, a placeholder kept for RFC 8628 shape compatibility. Polling uses `user_code`.
Polling must also stop at the first token — Simkl deletes an approved code, and polling an unknown
code falls through to the *issue-a-new-code* branch, so a client that kept going would be handed a
fresh code and wait forever.

### 9.2 Once there is code (mirrors re-com §5)

| Layer | Covers |
| --- | --- |
| Unit tests with fakes, no network (enforced) | scoring, exclusion, filters, status semantics, null handling, disambiguation |
| Live smoke harness | every tool against the real account: **returns or says why**, **excludes history**, **within** a latency ceiling |
| Quality check | signal agreement vs its ceiling, per-person and per-franchise concentration, cross-seed overlap, a measured noise floor |

re-com §6.5 applies from day one: the harness's first live run is part of writing it, and the
harness gets its own tests.

---

## 10. Roadmap

| Phase | Scope | State |
| --- | --- | --- |
| **0 — Probe** | §9.1. Write results back into this document. | **Wikidata half done (§9.1a).** Simkl half blocked on a `client_id`. Netflix data export still to request — it takes up to 30 days. |
| **1 — `simkl-mcp`** | PIN auth, library sync, title details, ID resolution, writes (§7.1) | **Built**, 84 unit tests. Unrun against a live account. |
| **2 — v1 engine** | `recommend_from_titles`, `recommend_for_tonight`, explain, refresh, taste, exclusion, Wikidata enrichment cache | `wikidata.py` (the maker half) built and live-verified. The Simkl half, scoring, exclusion and the tool surface are next. |
| **3 — History depth** | Direct Netflix CSV importer (durations), implicit feedback (§5.2), unmatched-history report | not started |
| **4 — More ways to ask** | watchlist triage, people, franchise-next, new releases, surprise me, watching together (§2.4) | not started |
| **5 — Tone** | a mood/tone layer beyond genre: Claude reading overviews, best-source-wins like re-com §4.5 | not started |
| **6 — Availability** | only if a free, lawful, region-aware source appears (§4.6) | — |

**Next, in order.** (1) Register the Simkl app and run `scripts/probe.py` in full — §9.1a's
question 2 about TV is now the one that decides whether v1 recommends TV at all. (2) Request the
Netflix personal-data export, which takes up to 30 days and is wanted before phase 3. (3) Build
the phase 2 engine on whatever the probe says, not on what this document assumed.

---

## 11. Open questions

1. **Crave** — confirmed in use? If so, logging there is manual (§4.5).
2. **Other services** — Prime Video, Disney+, Apple TV+, a JioHotstar or other Indian service?
   Each one Simkl can't import widens the stated exclusion gap.
3. **Netflix profiles** — is the Netflix account shared? Import only your profile.
4. **Anime** — in scope? Simkl handles it well and it costs little to include.
5. **Languages you watch** — decides which language filters matter first.
6. **Rewatches** — should a favourite you'd happily rewatch ever come back? (Simkl's rewatch
   tracking is Pro/VIP-only, verified, so it would be local.)
7. **simkl.com site terms** — read them once logged in (§4.2).

---

## 12. Sources

Checked 2026-09-13.

- TMDb API terms of use — https://www.themoviedb.org/api-terms-of-use
- TMDb similar-movies reference — https://developer.themoviedb.org/reference/movie-similar
- TMDb staff on similar vs recommendations — https://www.themoviedb.org/talk/5b70fd3fc3a368189916a57f
- Trakt: VIP required for API apps — https://github.com/trakt/trakt-api/issues/902 · https://github.com/euzu/tuliprox/issues/853
- Trakt docs (create an app, rate limits, VIP methods) — https://docs.trakt.tv/docs/create-an-app · https://docs.trakt.tv/docs/rate-limiting
- Trakt 2026 account limits — https://forums.trakt.tv/t/updating-trakt-limits-for-2026/101592
- Trakt streaming sync — https://forums.trakt.tv/t/automatically-sync-your-streaming-services/34947 · https://www.pcworld.com/article/2607062/trakt-helps-you-keep-track-of-your-streaming-shows.html
- Trakt VIP price (third party) — https://mindbalance.blog/trakt-cost-vip-pricing-guide
- Simkl API rules — https://api.simkl.org/api-rules
- Simkl rate limits, sync, statuses, IDs, PIN, redirect, nulls, errors — https://api.simkl.org/resources/rate-limits · https://api.simkl.org/guides/sync · https://api.simkl.org/conventions/list-statuses · https://api.simkl.org/conventions/standard-media-objects · https://api.simkl.org/api-reference/pin · https://api.simkl.org/api-reference/redirect · https://api.simkl.org/conventions/null-values · https://api.simkl.org/conventions/errors
- Simkl detail endpoints — https://api.simkl.org/api-reference/simkl/get-movie · https://api.simkl.org/api-reference/simkl/get-tv-show · https://api.simkl.org/api-reference/simkl/get-anime
- Simkl import docs — https://docs.simkl.org/how-to-use-simkl/advanced-usage/import-export-data/importing-to-simkl · https://docs.simkl.org/how-to-use-simkl/faq/frequently-asked-questions/can-simkl-auto-sync-from-other-streaming-apps-like-primevideo-disney+-hulu-and-import-watch-history
- Existing Simkl MCP — https://github.com/srevinsaju/simkl-mcp
- Wikidata licensing — https://www.wikidata.org/wiki/Wikidata:Licensing
- Wikidata Query Service manual — https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual
- Netflix viewing history help — https://help.netflix.com/en/node/101917
- Netflix export notes (third party) — https://blog.deariary.com/posts/2026-06-10-netflix-watch-history-locked · https://jakelee.co.uk/analysing-netflix-viewing-history/
- Crave History (My Cravings) — https://www.crave.ca/en/support/what-is-my-cravings-35557281
- MovieLens datasets — https://grouplens.org/datasets/movielens/
- IMDb non-commercial datasets — https://data.imdb.com/non-commercial-datasets/
- TVmaze API — https://www.tvmaze.com/api
- JustWatch API — https://apis.justwatch.com/docs/api/
