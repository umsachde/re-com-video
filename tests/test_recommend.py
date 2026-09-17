"""End-to-end pipeline tests with a fake Simkl source and faked Wikidata.

No network. These cover the whole chain -- seed -> gather -> score -> filter ->
exclude -> rank -> report -- which is where the guarantee either holds or
doesn't.
"""

import pytest

import recommend
import store as store_mod
import wikidata as wd


class FakeSource:
    """Stands in for simkl-mcp. Records what was asked of it."""

    def __init__(self, titles=None, search_results=None, redirects=None):
        self.titles = titles or {}
        self.search_results = search_results or {}
        self.redirects = redirects or {}
        self.calls = []

    def search(self, query, type=None, limit=10):
        self.calls.append(("search", query, type))
        return self.search_results.get(query, [])

    def title(self, simkl_id, type):
        self.calls.append(("title", simkl_id))
        return self.titles.get(int(simkl_id), {})

    def resolve_imdb(self, imdb):
        self.calls.append(("resolve", imdb))
        return self.redirects.get(imdb, {})

    def lookup_watched(self, items):
        return []


def detail(simkl_id, title, *, imdb=None, neighbours=(), **extra):
    """A Simkl detail record. Neighbours carry only simkl+slug, as measured."""
    return {
        "title": title,
        "year": extra.pop("year", 2020),
        "ids": {"simkl": simkl_id, **({"imdb": imdb} if imdb else {})},
        "users_recommendations": [
            {"title": t, "year": y, "type": ty, "ids": {"simkl": nid, "slug": f"s{nid}"}}
            for nid, t, y, ty in neighbours
        ],
        **extra,
    }


@pytest.fixture
def st():
    s = store_mod.Store(":memory:")
    s.upsert_history([{"simkl_id": 900, "title": "Seen Already", "status": "completed",
                       "type": "movie", "imdb": "tt900"}])
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_wikidata(monkeypatch):
    """Maker signals off unless a test switches them on."""
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {})
    monkeypatch.setattr(wd, "maker_neighbours", lambda rec, **kw: [])


# --- seeding ----------------------------------------------------------------


def test_a_seed_resolves_to_one_title(st):
    src = FakeSource(search_results={"Panchayat": [
        {"title": "Panchayat", "year": 2020, "type": "tv", "ids": {"simkl": 1}}
    ]})
    assert recommend.resolve_seed(src, st, "Panchayat")["simkl_id"] == 1


def test_an_ambiguous_title_is_reported_not_guessed(st):
    """Measured 2026-09-16: "Panchayat" matches 4 Wikidata items, "Parasite" 6.
    Picking one silently is how you recommend from the wrong film."""
    src = FakeSource(search_results={"Parasite": [
        {"title": "Parasite", "year": 2019, "type": "movie", "ids": {"simkl": 1}},
        {"title": "Parasite", "year": 1982, "type": "movie", "ids": {"simkl": 2}},
    ]})
    with pytest.raises(recommend.SeedError, match="matches more than one"):
        recommend.resolve_seed(src, st, "Parasite")


def test_a_collision_across_types_is_caught(st):
    """The live failure this was found by: Simkl's /search takes ONE type at a
    time, so a movie-only search finds the 2017 Panchayat film and never learns
    the 2020 series exists. Every type has to be searched."""
    def search(query, type=None, limit=10):
        if type == "movie":
            return [{"title": "Panchayat", "year": 2017, "type": "movie", "ids": {"simkl": 916118}}]
        if type == "tv":
            return [{"title": "Panchayat", "year": 2020, "type": "tv", "ids": {"simkl": 1311258}}]
        return []
    src = FakeSource()
    src.search = search
    with pytest.raises(recommend.SeedError, match="matches more than one"):
        recommend.resolve_seed(src, st, "Panchayat")


def test_a_year_in_the_title_disambiguates(st):
    def search(query, type=None, limit=10):
        if type == "movie":
            return [{"title": "Panchayat", "year": 2017, "type": "movie", "ids": {"simkl": 916118}}]
        if type == "tv":
            return [{"title": "Panchayat", "year": 2020, "type": "tv", "ids": {"simkl": 1311258}}]
        return []
    src = FakeSource()
    src.search = search
    assert recommend.resolve_seed(src, st, "Panchayat 2020")["simkl_id"] == 1311258
    assert recommend.resolve_seed(src, st, "Panchayat (2017)")["simkl_id"] == 916118


def test_a_year_that_matches_nothing_says_what_does_exist(st):
    src = FakeSource()
    src.search = lambda q, type=None, limit=10: (
        [{"title": "X", "year": 2020, "type": "tv", "ids": {"simkl": 1}}] if type == "tv" else []
    )
    with pytest.raises(recommend.SeedError, match="No 'X' from 1999"):
        recommend.resolve_seed(src, st, "X 1999")


def test_a_missing_title_says_so(st):
    with pytest.raises(recommend.SeedError, match="no title matching"):
        recommend.resolve_seed(FakeSource(), st, "Nonexistent Film")


def test_seed_search_covers_every_type(st):
    calls = []
    src = FakeSource()
    def search(query, type=None, limit=10):
        calls.append(type)
        return [{"title": "X", "year": 2020, "type": "tv", "ids": {"simkl": 1}}] if type == "tv" else []
    src.search = search
    assert recommend.resolve_seed(src, st, "X")["simkl_id"] == 1
    assert set(calls) == {"movie", "tv", "anime"}


def test_the_same_title_found_under_two_types_is_not_double_counted(st):
    src = FakeSource()
    src.search = lambda q, type=None, limit=10: [
        {"title": "X", "year": 2020, "type": "tv", "ids": {"simkl": 1}}
    ]
    assert recommend.resolve_seed(src, st, "X")["simkl_id"] == 1


# --- gathering --------------------------------------------------------------


def test_viewer_neighbours_become_candidates(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "tv")])})
    found = recommend.gather_seed(src, st, {"simkl_id": 1, "type": "tv", "title": "Seed"})
    assert set(found) == {2}
    assert found[2]["sources"] == {(1, "viewers")}


def test_a_neighbour_without_a_simkl_id_is_skipped(st):
    rec = detail(1, "Seed")
    rec["users_recommendations"] = [{"title": "No ID", "ids": {}}]
    src = FakeSource(titles={1: rec})
    assert recommend.gather_seed(src, st, {"simkl_id": 1, "type": "tv"}) == {}


def test_the_detail_record_is_cached_after_the_first_call(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "tv")])})
    seed = {"simkl_id": 1, "type": "tv"}
    recommend.gather_seed(src, st, seed)
    recommend.gather_seed(src, st, seed)
    assert sum(1 for c in src.calls if c[0] == "title") == 1


def test_maker_neighbours_cross_into_simkl_ids(st, monkeypatch):
    """Wikidata gives IMDb IDs; the engine keys on Simkl IDs. A candidate that
    can't cross can't be excluded reliably, so it isn't recommended."""
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {"tt1": {"imdb": "tt1", "qid": "Q1"}})
    monkeypatch.setattr(
        wd, "maker_neighbours",
        lambda rec, **kw: [{"imdb": "tt5", "title": "By Same Director", "year": 2018,
                            "signal": "director", "via": {"label": "D"}}],
    )
    src = FakeSource(
        titles={1: detail(1, "Seed", imdb="tt1")},
        redirects={"tt5": {"simkl_id": 5, "type": "movie"}},
    )
    found = recommend.gather_seed(src, st, {"simkl_id": 1, "type": "movie"})
    assert found[5]["sources"] == {(1, "director")}


def test_an_unresolvable_maker_candidate_is_dropped(st, monkeypatch):
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {"tt1": {"imdb": "tt1"}})
    monkeypatch.setattr(
        wd, "maker_neighbours",
        lambda rec, **kw: [{"imdb": "tt404", "title": "Unknown", "signal": "director"}],
    )
    src = FakeSource(titles={1: detail(1, "Seed", imdb="tt1")}, redirects={})
    assert recommend.gather_seed(src, st, {"simkl_id": 1, "type": "movie"}) == {}


def test_a_failed_redirect_is_cached_as_a_miss(st, monkeypatch):
    """Re-asking Simkl for an ID it already said it doesn't have is wasted
    budget against a 10 GET/s ceiling."""
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {"tt1": {"imdb": "tt1"}})
    monkeypatch.setattr(
        wd, "maker_neighbours",
        lambda rec, **kw: [{"imdb": "tt404", "title": "U", "signal": "director"}],
    )
    src = FakeSource(titles={1: detail(1, "S", imdb="tt1")}, redirects={})
    seed = {"simkl_id": 1, "type": "movie"}
    recommend.gather_seed(src, st, seed)
    recommend.gather_seed(src, st, seed)
    assert sum(1 for c in src.calls if c[0] == "resolve") == 1


# --- the full pipeline ------------------------------------------------------


def test_recommend_excludes_what_is_already_in_history(st):
    """The guarantee. 900 is in the fixture's history."""
    src = FakeSource(titles={
        1: detail(1, "Seed", neighbours=[(900, "Seen Already", 2019, "movie"),
                                         (2, "Fresh", 2021, "movie")]),
    })
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie", "title": "Seed"}])
    assert [r["simkl_id"] for r in out["results"]] == [2]
    assert out["exclusion"]["already_seen_removed"] == 1


def test_the_seed_itself_is_never_a_result(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(1, "Seed", 2020, "movie"),
                                                             (2, "Other", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie", "title": "Seed"}])
    assert [r["simkl_id"] for r in out["results"]] == [2]


def test_several_seeds_agreeing_ranks_higher(st):
    src = FakeSource(titles={
        1: detail(1, "A", neighbours=[(3, "Shared", 2021, "movie"), (4, "OnlyA", 2021, "movie")]),
        2: detail(2, "B", neighbours=[(3, "Shared", 2021, "movie"), (5, "OnlyB", 2021, "movie")]),
    })
    out = recommend.recommend(
        src, st,
        [{"simkl_id": 1, "type": "movie"}, {"simkl_id": 2, "type": "movie"}],
    )
    assert out["results"][0]["simkl_id"] == 3
    assert out["results"][0]["score"] == 2


def test_every_result_carries_a_link_and_an_explanation(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    row = out["results"][0]
    assert row["simkl_url"].startswith("https://simkl.com/")
    assert row["why"] and row["confidence"]


def test_a_single_seed_result_is_labelled_as_a_list_not_a_ranking(st):
    """Measured: 13 of 20 seeds shared nothing between the two sources, so on
    one seed nearly everything scores 1."""
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    assert any("not a ranking" in c for c in out["caveats"])


def test_the_import_gap_is_always_stated(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    assert any("Crave" in c for c in out["caveats"])


def test_a_short_result_explains_itself(st):
    src = FakeSource(titles={
        1: detail(1, "Seed", neighbours=[(900, "Seen", 2019, "movie"), (2, "N", 2021, "movie")]),
    })
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}], limit=10)
    assert out["note"] and "already seen" in out["note"]


def test_no_seeds_is_an_error_not_an_empty_answer(st):
    with pytest.raises(recommend.SeedError):
        recommend.recommend(FakeSource(), st, [])


def test_results_are_logged_for_later_implicit_feedback(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "movie")])})
    recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    assert st.times_served(2) == 1


def test_a_disliked_seed_demotes_what_it_reaches(st):
    st.upsert_history([{"simkl_id": 1, "title": "Dropped Seed", "status": "dropped",
                        "type": "movie"}])
    src = FakeSource(titles={
        1: detail(1, "A", neighbours=[(3, "FromDisliked", 2021, "movie")]),
        2: detail(2, "B", neighbours=[(4, "FromLiked", 2021, "movie")]),
    })
    out = recommend.recommend(
        src, st, [{"simkl_id": 1, "type": "movie"}, {"simkl_id": 2, "type": "movie"}]
    )
    ids = [r["simkl_id"] for r in out["results"]]
    assert ids.index(4) < ids.index(3)


def test_filters_run_before_exclusion_and_both_are_reported(st):
    src = FakeSource(titles={
        1: detail(1, "Seed", neighbours=[(2, "Movie", 2021, "movie"), (3, "Show", 2021, "tv")]),
    })
    st.cache_title(2, "movie", {"title": "Movie", "runtime": 95, "ids": {}})
    st.cache_title(3, "tv", {"title": "Show", "runtime": 45, "ids": {}})
    out = recommend.recommend(
        src, st, [{"simkl_id": 1, "type": "movie"}], type="movie"
    )
    assert [r["simkl_id"] for r in out["results"]] == [2]
    assert any(f["filter"] == "type" for f in out["filters"])


def test_tonight_seeds_come_from_the_taste_model(st):
    st.upsert_history([
        {"simkl_id": 10, "title": "Loved", "status": "completed", "rating": 9, "type": "movie"},
        {"simkl_id": 11, "title": "Meh", "status": "completed", "rating": 5, "type": "movie"},
    ])
    assert [s["simkl_id"] for s in recommend.tonight_seeds(st)] == [10]


# --- degrading instead of failing -------------------------------------------


def test_a_wikidata_outage_does_not_sink_the_request(st, monkeypatch):
    """Found on the first run against a real library: one Wikidata timeout
    raised straight through and killed the whole recommendation. The viewer
    signal alone is still a usable answer."""
    def boom(*a, **kw):
        raise wd.WikidataError("Read timed out")
    monkeypatch.setattr(wd, "lookup_by_imdb", boom)
    src = FakeSource(titles={1: detail(1, "Seed", imdb="tt1",
                                       neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie", "title": "Seed"}])
    assert [r["simkl_id"] for r in out["results"]] == [2]


def test_a_degraded_signal_is_reported_not_swallowed(st, monkeypatch):
    monkeypatch.setattr(
        wd, "lookup_by_imdb", lambda *a, **kw: (_ for _ in ()).throw(wd.WikidataError("down"))
    )
    src = FakeSource(titles={1: detail(1, "Seed", imdb="tt1",
                                       neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie", "title": "Seed"}])
    assert out["degraded"] and "maker signals unavailable" in out["degraded"][0]
    assert any("Wikidata was unreachable" in c for c in out["caveats"])


def test_no_degradation_reports_none(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[(2, "N", 2021, "movie")])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    assert out["degraded"] is None


def test_a_candidate_found_by_both_signals_keeps_its_provenance(st, monkeypatch):
    """The viewer signal carries no `via_by`; the maker signal writing to that
    key blew up when the same title arrived from both."""
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {"tt1": {"imdb": "tt1"}})
    monkeypatch.setattr(
        wd, "maker_neighbours",
        lambda rec, **kw: [{"imdb": "tt5", "title": "Both", "signal": "director",
                            "via": {"qid": "Q-d", "label": "D"}}],
    )
    src = FakeSource(
        titles={1: detail(1, "Seed", imdb="tt1", neighbours=[(5, "Both", 2021, "movie")])},
        redirects={"tt5": {"simkl_id": 5, "type": "movie"}},
    )
    found = recommend.gather_seed(src, st, {"simkl_id": 1, "type": "movie"})
    assert found[5]["sources"] == {(1, "viewers"), (1, "director")}
    assert found[5]["via_by"] == {"director": "Q-d"}


# --- a history that has swallowed its own neighbourhood ---------------------


def test_saturation_is_detected_and_explained(st):
    """Measured on a real 70-film Marvel library: 72 viewer candidates, one
    survived exclusion. Padding that with a director's back catalogue and
    letting it read like a recommendation is the dishonest option."""
    neighbours = [(n, f"Seen{n}", 2015, "movie") for n in range(100, 112)]
    st.upsert_history([
        {"simkl_id": n, "title": f"Seen{n}", "status": "completed", "type": "movie"}
        for n, *_ in neighbours
    ])
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=neighbours)})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie", "title": "Seed"}])
    assert out["signal_health"]["saturated"] is True
    assert out["signal_health"]["viewer_surviving_exclusion"] == 0
    assert any("already in your history" in c for c in out["caveats"])


def test_a_healthy_signal_is_not_flagged_as_saturated(st):
    src = FakeSource(titles={1: detail(1, "Seed", neighbours=[
        (n, f"New{n}", 2015, "movie") for n in range(200, 212)
    ])})
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    assert out["signal_health"]["saturated"] is False
    assert not any("already in your history" in c for c in out["caveats"])


def test_viewer_backing_breaks_a_tie_against_a_maker_only_candidate(st, monkeypatch):
    """Phase 0 measured the maker signal is film-only and noisy; at equal
    evidence the viewer signal is the better bet."""
    monkeypatch.setattr(wd, "lookup_by_imdb", lambda ids, **kw: {"tt1": {"imdb": "tt1"}})
    monkeypatch.setattr(
        wd, "maker_neighbours",
        lambda rec, **kw: [{"imdb": "tt9", "title": "Old Back Catalogue", "year": 1966,
                            "signal": "composer", "via": {"qid": "Q-c"}}],
    )
    src = FakeSource(
        titles={1: detail(1, "Seed", imdb="tt1", neighbours=[(2, "Viewer Pick", 2021, "movie")])},
        redirects={"tt9": {"simkl_id": 9, "type": "movie"}},
    )
    out = recommend.recommend(src, st, [{"simkl_id": 1, "type": "movie"}])
    ids = [r["simkl_id"] for r in out["results"]]
    assert ids.index(2) < ids.index(9)


def test_seeds_are_spread_across_the_taste_profile(st):
    """70 films all rated 8 produced six seeds that were all Spider-Man or
    early MCU -- one corner of the profile, all with the same neighbours."""
    st.upsert_history([
        {"simkl_id": i, "title": f"T{i}", "status": "completed", "rating": 8, "type": "movie"}
        for i in range(1, 31)
    ])
    picked = [s["simkl_id"] for s in st.seeds(limit=5)]
    assert len(set(picked)) == 5
    assert max(picked) - min(picked) > 10, "seeds clustered instead of spreading"


def test_seed_spreading_is_deterministic(st):
    st.upsert_history([
        {"simkl_id": i, "title": f"T{i}", "status": "completed", "rating": 8, "type": "movie"}
        for i in range(1, 31)
    ])
    assert [s["simkl_id"] for s in st.seeds(limit=5)] == [s["simkl_id"] for s in st.seeds(limit=5)]
