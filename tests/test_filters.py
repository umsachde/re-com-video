"""Unit tests for filters.py.

The theme throughout: a filter must never quietly stop filtering. Simkl's own
browse endpoints do exactly that -- an unknown segment is treated as "all" and
still returns 200 -- which is the failure this whole project is shaped around.
"""

import pytest

import filters


def c(entries):
    """{simkl_id: metadata} in the shape hydrate() produces."""
    return {i: {"simkl_id": i, **meta} for i, meta in entries.items()}


# --- unknown values are refused, not widened --------------------------------


def test_an_unknown_type_is_refused():
    with pytest.raises(filters.FilterError, match="type must be one of"):
        filters.by_type(c({1: {"type": "movie"}}), "documentary-ish")


def test_a_nonsense_runtime_is_refused():
    with pytest.raises(filters.FilterError, match="positive number of minutes"):
        filters.by_runtime(c({1: {"runtime": 90}}), -5)


def test_no_filter_value_is_a_no_op():
    cands = c({1: {"type": "movie"}})
    out, report = filters.by_type(cands, None)
    assert out is cands and report is None


@pytest.mark.parametrize("given,expected", [("show", "tv"), ("series", "tv"), ("movies", "movie")])
def test_common_synonyms_are_accepted(given, expected):
    out, _ = filters.by_type(c({1: {"type": expected}}), given)
    assert set(out) == {1}


# --- each filter reports what it did ----------------------------------------


def test_type_filter_reports_its_removals():
    out, report = filters.by_type(c({1: {"type": "movie"}, 2: {"type": "tv"}}), "tv")
    assert set(out) == {2}
    assert report == {"filter": "type", "removed": 1, "kept": 1, "unknown_metadata": 0}


def test_runtime_filter_keeps_only_what_fits():
    out, report = filters.by_runtime(
        c({1: {"runtime": 95}, 2: {"runtime": 180}}), 120
    )
    assert set(out) == {1}
    assert report["removed"] == 1


def test_language_matches_a_name_or_a_code():
    cands = c({1: {"languages": ["Hindi"]}, 2: {"languages": ["English"]}})
    out, _ = filters.by_language(cands, "hindi")
    assert set(out) == {1}


def test_genre_matches_case_insensitively():
    out, _ = filters.by_genre(c({1: {"genres": ["Comedy"]}, 2: {"genres": ["Horror"]}}), "comedy")
    assert set(out) == {1}


def test_min_rating_filters_on_the_community_score():
    out, _ = filters.by_min_rating(
        c({1: {"community_rating": 8.2}, 2: {"community_rating": 5.0}}), 7
    )
    assert set(out) == {1}


# --- missing metadata is counted, never silently decided ---------------------


def test_a_candidate_with_no_metadata_is_counted_as_unknown():
    """Dropping it silently looks like a thin signal; keeping it silently
    breaks the filter's promise. So it's counted and reported."""
    out, report = filters.by_runtime(c({1: {"runtime": 90}, 2: {}}), 120)
    assert set(out) == {1}
    assert report["unknown_metadata"] == 1
    assert "cannot vouch" in report["note"]


def test_unknown_metadata_does_not_sneak_through_a_filter():
    out, _ = filters.by_language(c({1: {}}), "Hindi")
    assert out == {}


def test_a_report_without_unknowns_carries_no_note():
    _, report = filters.by_type(c({1: {"type": "tv"}}), "tv")
    assert "note" not in report


# --- finishable -------------------------------------------------------------

def test_finishable_wants_an_ended_and_short_series():
    cands = c({
        1: {"type": "tv", "series_status": "ended", "total_episodes": 8},
        2: {"type": "tv", "series_status": "ended", "total_episodes": 200},
        3: {"type": "tv", "series_status": "airing", "total_episodes": 8},
        4: {"type": "movie", "series_status": "released", "total_episodes": 0},
    })
    out, _ = filters.finishable(cands, True)
    assert set(out) == {1}


def test_finishable_off_is_a_no_op():
    cands = c({1: {"type": "movie"}})
    out, report = filters.finishable(cands, False)
    assert out is cands and report is None


# --- run_all ----------------------------------------------------------------


def test_run_all_applies_every_requested_filter():
    cands = c({
        1: {"type": "movie", "runtime": 95, "languages": ["Hindi"]},
        2: {"type": "movie", "runtime": 200, "languages": ["Hindi"]},
        3: {"type": "tv", "runtime": 45, "languages": ["Hindi"]},
    })
    out, reports = filters.run_all(cands, type="movie", runtime_max=120, language="Hindi")
    assert set(out) == {1}
    assert {r["filter"] for r in reports} == {"type", "runtime", "language"}


def test_run_all_with_no_criteria_changes_nothing():
    cands = c({1: {"type": "movie"}})
    out, reports = filters.run_all(cands)
    assert out == cands and reports == []


def test_run_all_reports_are_ordered_and_complete():
    cands = c({1: {"type": "movie", "runtime": 95}})
    _, reports = filters.run_all(cands, type="movie", runtime_max=120)
    assert [r["filter"] for r in reports] == ["type", "runtime"]
    for r in reports:
        assert set(r) >= {"filter", "removed", "kept", "unknown_metadata"}
