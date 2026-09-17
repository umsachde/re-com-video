"""Unit tests for store.py — the exclusion set, the taste weights, the caches."""

import pytest

import store as store_mod


@pytest.fixture
def s():
    st = store_mod.Store(":memory:")
    yield st
    st.close()


def row(simkl_id, **kw):
    base = {
        "simkl_id": simkl_id, "type": "movie", "title": f"T{simkl_id}",
        "year": 2020, "status": "completed", "rating": None,
        "watched_episodes": None, "total_episodes": None, "imdb": f"tt{simkl_id}",
    }
    base.update(kw)
    return base


# --- the exclusion set ------------------------------------------------------


def test_every_status_is_excluded_not_just_completed():
    """One episode watched counts as seen; a dropped show is still seen."""
    st = store_mod.Store(":memory:")
    st.upsert_history([
        row(1, status="completed"), row(2, status="watching"),
        row(3, status="hold"), row(4, status="dropped"), row(5, status="plantowatch"),
    ])
    assert st.exclusion_ids() == {1, 2, 3, 4, 5}


def test_plan_to_watch_is_separately_identifiable(s):
    s.upsert_history([row(1, status="completed"), row(2, status="plantowatch")])
    assert s.planned_ids() == {2}


def test_upsert_merges_rather_than_replacing(s):
    """A Simkl delta carries only what changed. Replacing the table with one
    would silently empty the exclusion set."""
    s.upsert_history([row(1), row(2)])
    s.upsert_history([row(3)])
    assert s.exclusion_ids() == {1, 2, 3}


def test_upsert_updates_an_existing_row(s):
    s.upsert_history([row(1, status="watching", rating=None)])
    s.upsert_history([row(1, status="completed", rating=9)])
    got = s.get_history(1)
    assert (got["status"], got["rating"]) == ("completed", 9)
    assert s.history_size() == 1


def test_a_row_without_a_simkl_id_is_not_written(s):
    s.upsert_history([{"title": "nameless"}, row(1)])
    assert s.exclusion_ids() == {1}


def test_removal_makes_a_title_recommendable_again(s):
    s.upsert_history([row(1), row(2)])
    s.remove_history([1])
    assert s.exclusion_ids() == {2}


# --- taste weights (PLAN 5.1) -----------------------------------------------


@pytest.mark.parametrize(
    "rating,positive", [(10, True), (9, True), (8, True), (5, True), (4, False), (1, False)]
)
def test_rating_sign_follows_the_plan(rating, positive):
    w = store_mod.Store.weight(row(1, rating=rating))
    assert (w > 0) is positive


def test_a_rating_overrides_the_status():
    """A status is mostly a side effect of what got clicked; a rating is the
    user speaking."""
    w = store_mod.Store.weight(row(1, status="completed", rating=2))
    assert w < 0


def test_dropped_is_the_clearest_negative():
    assert store_mod.Store.weight(row(1, status="dropped")) < 0


def test_hold_is_neutral():
    assert store_mod.Store.weight(row(1, status="hold")) == 0.0


def test_a_barely_watched_series_is_weak_evidence():
    """2 of 60 episodes is not evidence you liked it (PLAN 5.3)."""
    barely = store_mod.Store.weight(
        row(1, type="tv", status="completed", watched_episodes=2, total_episodes=60)
    )
    fully = store_mod.Store.weight(
        row(2, type="tv", status="completed", watched_episodes=60, total_episodes=60)
    )
    assert 0 < barely < fully


def test_partial_watching_never_softens_a_negative():
    """A show dropped after 2 of 60 is a stronger signal, not a weaker one."""
    w = store_mod.Store.weight(
        row(1, type="tv", status="dropped", watched_episodes=2, total_episodes=60)
    )
    assert w == store_mod.Store.weight(row(1, type="tv", status="dropped"))


# --- seeds ------------------------------------------------------------------


def test_seeds_prefer_highly_rated_titles(s):
    s.upsert_history([row(1, rating=9), row(2, rating=5), row(3, status="dropped")])
    assert [r["simkl_id"] for r in s.seeds()] == [1]


def test_seeds_exclude_plan_to_watch(s):
    """You can't seed from something you haven't watched."""
    s.upsert_history([row(1, rating=10, status="plantowatch"), row(2, rating=9)])
    assert [r["simkl_id"] for r in s.seeds()] == [2]


def test_seeds_honour_the_limit(s):
    s.upsert_history([row(i, rating=9) for i in range(1, 10)])
    assert len(s.seeds(limit=3)) == 3


def test_seeds_carry_their_weight(s):
    s.upsert_history([row(1, rating=9)])
    assert s.seeds()[0]["taste_weight"] > 0


def test_no_strong_seeds_returns_empty_rather_than_guessing(s):
    s.upsert_history([row(1, status="hold")])
    assert s.seeds() == []


def test_dislikes_collects_the_negatives(s):
    s.upsert_history([row(1, rating=9), row(2, status="dropped"), row(3, rating=2)])
    assert set(s.dislikes()) == {2, 3}


# --- caches -----------------------------------------------------------------


def test_title_cache_round_trips(s):
    s.cache_title(7, "movie", {"title": "X", "year": 2020, "ids": {"imdb": "tt7"}})
    assert s.get_title(7)["title"] == "X"
    assert s.imdb_for(7) == "tt7"


def test_title_cache_miss_is_none(s):
    assert s.get_title(999) is None


def test_title_cache_honours_max_age(s):
    s.cache_title(7, "movie", {"title": "X"})
    assert s.get_title(7, max_age=0) is None


def test_enrichment_cache_round_trips(s):
    s.cache_enrichment("tt1", {"qid": "Q1", "director": [{"qid": "Q2"}]})
    assert s.get_enrichment("tt1")["qid"] == "Q1"


def test_an_empty_enrichment_is_still_cached(s):
    """"Wikidata doesn't have this" is an answer worth remembering -- re-asking
    costs a query against a 60-second-per-minute budget."""
    s.cache_enrichment("tt1", {})
    assert s.get_enrichment("tt1") == {}


# --- state, feedback, served log --------------------------------------------


def test_sync_state_round_trips(s):
    s.set_state("last_activity_all", "2026-09-16T00:00:00Z")
    assert s.get_state("last_activity_all") == "2026-09-16T00:00:00Z"
    s.set_state("last_activity_all", "later")
    assert s.get_state("last_activity_all") == "later"


def test_feedback_is_recorded(s):
    s.record_feedback(1, "loved it")
    assert s.feedback_for(1) == ["loved it"]


def test_served_log_counts_appearances(s):
    s.log_served([1, 2], seeds=[10])
    s.log_served([1], seeds=[11])
    assert s.times_served(1) == 2
    assert s.times_served(2) == 1


def test_status_names_the_import_gap(s):
    """The guarantee's limit has to reach the user, not sit in a docstring."""
    gaps = " ".join(s.status()["known_gaps"])
    assert "Crave" in gaps and "Prime Video" in gaps


def test_status_reports_sizes(s):
    s.upsert_history([row(1, rating=9)])
    st = s.status()
    assert st["history_titles"] == 1
    assert st["rated"] == 1
