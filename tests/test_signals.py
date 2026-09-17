"""Unit tests for signals.py — scoring, exclusion, explanation, honesty.

No network. The behaviours pinned here are the ones PLAN.md treats as
non-negotiable: exclusion runs last and always, a short result says why, and a
score of 1 is not dressed up as a ranking.
"""

import pytest

import signals


def cand(simkl_id, title, pairs, **extra):
    """A candidate as gather() produces it: (seed, signal) pairs as provenance."""
    return {
        simkl_id: {
            "title": title,
            "year": extra.pop("year", 2020),
            "type": extra.pop("type", "movie"),
            "imdb": extra.pop("imdb", f"tt{simkl_id}"),
            "sources": set(pairs),
            "seeds": {p[0] for p in pairs},
            **extra,
        }
    }


# --- scoring ----------------------------------------------------------------


def test_score_counts_distinct_seed_signal_pairs():
    merged = signals.merge_and_score(
        [
            cand(1, "X", [(10, "viewers")]),
            cand(1, "X", [(20, "director")]),
        ]
    )
    assert merged[1]["score"] == 2
    assert merged[1]["seeds"] == {10, 20}


def test_the_same_pair_twice_counts_once():
    """Two seeds reaching a title through the same person shouldn't double-count
    the same evidence."""
    merged = signals.merge_and_score(
        [cand(1, "X", [(10, "viewers")]), cand(1, "X", [(10, "viewers")])]
    )
    assert merged[1]["score"] == 1


def test_one_seed_through_two_signals_scores_two():
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers"), (10, "director")])])
    assert merged[1]["score"] == 2


def test_merge_fills_in_a_field_a_later_seed_knew():
    a = cand(1, "X", [(10, "viewers")])
    a[1]["imdb"] = None
    b = cand(1, "X", [(20, "director")], imdb="tt999")
    merged = signals.merge_and_score([a, b])
    assert merged[1]["imdb"] == "tt999"


def test_merge_of_nothing_is_empty():
    assert signals.merge_and_score([]) == {}


# --- exclusion --------------------------------------------------------------


def test_exclusion_removes_everything_already_seen():
    c = {**cand(1, "Seen", [(10, "viewers")]), **cand(2, "New", [(10, "viewers")])}
    kept, report = signals.exclude(c, seen={1})
    assert set(kept) == {2}
    assert report["excluded_already_seen"] == 1


def test_plan_to_watch_is_excluded_but_counted_separately():
    """"Already on your list" is a different sentence from "you've seen it"."""
    c = {**cand(1, "Planned", [(10, "viewers")]), **cand(2, "New", [(10, "viewers")])}
    kept, report = signals.exclude(c, seen={1, 2} - {2}, planned={1})
    assert set(kept) == {2}
    assert report["excluded_already_seen"] == 0
    assert [p["title"] for p in report["excluded_on_your_list"]] == ["Planned"]


def test_exclusion_reports_both_sides_of_the_count():
    c = {**cand(1, "A", [(10, "viewers")]), **cand(2, "B", [(10, "viewers")])}
    _, report = signals.exclude(c, seen={1})
    assert report["candidates_before_exclusion"] == 2
    assert report["candidates_after_exclusion"] == 1


def test_excluding_everything_is_allowed_and_reported():
    c = cand(1, "A", [(10, "viewers")])
    kept, report = signals.exclude(c, seen={1})
    assert kept == {}
    assert report["candidates_after_exclusion"] == 0


def test_exclusion_with_an_empty_history_keeps_everything():
    c = cand(1, "A", [(10, "viewers")])
    kept, _ = signals.exclude(c, seen=set())
    assert set(kept) == {1}


# --- taste ------------------------------------------------------------------


def test_a_disliked_seed_demotes_but_never_excludes():
    """PLAN 5.2: negatives demote. Turning one into an exclusion would shrink
    the catalogue in a way nothing reports."""
    merged = signals.merge_and_score([cand(1, "X", [(10, "director")])])
    out = signals.apply_taste(merged, dislikes={10: -2.0})
    assert 1 in out
    assert out[1]["adjusted_score"] < out[1]["score"]


def test_taste_leaves_untouched_candidates_alone():
    merged = signals.merge_and_score([cand(1, "X", [(10, "director")])])
    out = signals.apply_taste(merged, dislikes={99: -2.0})
    assert out[1]["adjusted_score"] == float(out[1]["score"])


def test_no_dislikes_is_a_no_op():
    merged = signals.merge_and_score([cand(1, "X", [(10, "director")])])
    assert signals.apply_taste(merged, {}) is merged


# --- explanation ------------------------------------------------------------


def test_explanation_groups_signals_under_the_seed_that_used_them():
    merged = signals.merge_and_score(
        [cand(1, "X", [(10, "viewers"), (10, "director"), (20, "composer")])]
    )
    why = signals.explain(merged[1])
    assert why == [
        {"seed_simkl_id": 10, "signals": ["director", "viewers"]},
        {"seed_simkl_id": 20, "signals": ["composer"]},
    ]


def test_explanation_is_built_from_the_same_pairs_the_score_counts():
    """If these could drift apart, the explanation would be a separate claim
    that might not match the ranking."""
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers"), (20, "director")])])
    total = sum(len(g["signals"]) for g in signals.explain(merged[1]))
    assert total == merged[1]["score"]


# --- honesty about what a score means ---------------------------------------


def test_a_single_viewer_signal_says_so_plainly():
    """Phase 0 measured that cross-source agreement is rare, so a score of 1 is
    the norm. Calling it "one signal" beats implying a ranking."""
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers")])])
    assert signals.confidence(merged[1], seed_count=1) == (
        "one signal: viewers of your title also watched it"
    )


def test_two_sources_on_one_seed_is_reported_as_agreement():
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers"), (10, "director")])])
    assert "two independent signals" in signals.confidence(merged[1], 1)


def test_several_seeds_agreeing_is_the_strongest_wording():
    merged = signals.merge_and_score(
        [cand(1, "X", [(10, "viewers")]), cand(1, "X", [(20, "viewers")])]
    )
    assert "across several of your titles" in signals.confidence(merged[1], 2)


# --- ranking ----------------------------------------------------------------


def test_rank_orders_by_score():
    c = {
        **cand(1, "Low", [(10, "viewers")]),
        **cand(2, "High", [(10, "viewers"), (20, "director")]),
    }
    merged = signals.merge_and_score([c])
    out = signals.rank(merged, seed_count=2, limit=5)
    assert [r["title"] for r in out] == ["High", "Low"]


def test_rank_honours_the_limit():
    c = {}
    for i in range(1, 6):
        c.update(cand(i, f"T{i}", [(10, "viewers")]))
    out = signals.rank(signals.merge_and_score([c]), seed_count=1, limit=3)
    assert len(out) == 3


def test_rank_is_reproducible_for_tied_candidates():
    c = {**cand(1, "A", [(10, "viewers")], year=2020), **cand(2, "B", [(10, "viewers")], year=2021)}
    merged = signals.merge_and_score([c])
    first = [r["simkl_id"] for r in signals.rank(merged, 1, 5)]
    second = [r["simkl_id"] for r in signals.rank(merged, 1, 5)]
    assert first == second


def test_every_result_carries_a_simkl_link():
    """Simkl's API rules require linking back to the page for any title shown."""
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers")], type="movie")])
    assert signals.rank(merged, 1, 5)[0]["simkl_url"] == "https://simkl.com/movies/1"


def test_rank_carries_the_explanation_and_the_confidence():
    merged = signals.merge_and_score([cand(1, "X", [(10, "viewers")])])
    row = signals.rank(merged, 1, 5)[0]
    assert row["why"] and row["confidence"]


@pytest.mark.parametrize(
    "type_,expected", [("movie", "movies"), ("tv", "tv"), ("anime", "anime")]
)
def test_simkl_url_by_type(type_, expected):
    assert signals.simkl_url(7, type_) == f"https://simkl.com/{expected}/7"


# --- shortfall reporting ----------------------------------------------------


def test_a_full_result_needs_no_note():
    assert signals.shortfall_note(10, 10, 1, {}) is None


def test_a_short_result_names_exclusion_as_the_reason():
    note = signals.shortfall_note(4, 10, 1, {"excluded_already_seen": 6})
    assert "already seen them" in note
    assert "4 of the 10" in note


def test_a_short_result_names_the_twelve_neighbour_ceiling():
    """A user who asked for 20 from one seed should learn that 12 was the cap
    before filtering even started -- it's a property of the data, not the ask."""
    note = signals.shortfall_note(12, 20, 1, {})
    assert "at most 12 viewer-based neighbours" in note


def test_a_short_result_names_a_filter_that_caused_it():
    note = signals.shortfall_note(
        2, 10, 1, {"filters": [{"filter": "language", "removed": 8}]}
    )
    assert "language filter removed 8" in note


def test_a_short_result_always_gives_some_reason():
    """Never a silently short list."""
    note = signals.shortfall_note(2, 5, 1, {})
    assert note and "did not surface more" in note


def test_plan_to_watch_is_named_in_the_shortfall():
    note = signals.shortfall_note(
        1, 5, 1, {"excluded_on_your_list": [{"simkl_id": 1}, {"simkl_id": 2}]}
    )
    assert "Plan to Watch" in note
