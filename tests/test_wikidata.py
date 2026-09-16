"""Unit tests for wikidata.py. No network: every SPARQL response is faked.

The live behaviour these stand in for was checked against the real query
service on 2026-09-15 (see PLAN.md 9.1).
"""

import pytest
import requests

import wikidata as wd


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _bindings(*rows):
    return {"results": {"bindings": [{k: {"value": v} for k, v in row.items()} for row in rows]}}


P = "http://www.wikidata.org/prop/direct/"
E = "http://www.wikidata.org/entity/"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    monkeypatch.setattr(wd, "_last_call", 0.0)


class _Sent(list):
    """The queries actually sent, with a `queue` of responses to hand back."""

    queue: list


@pytest.fixture
def respond(monkeypatch):
    sent = _Sent()
    sent.queue = []

    def fake_get(url, params=None, headers=None, timeout=None):
        sent.append(params["query"])
        assert headers["User-Agent"].startswith("re-com-video/"), (
            "WDQS blocks clients without a descriptive User-Agent"
        )
        return sent.queue.pop(0) if sent.queue else _FakeResponse(payload=_bindings())

    monkeypatch.setattr(requests, "get", fake_get)
    return sent


# --- enrichment -------------------------------------------------------------


def test_lookup_folds_triples_into_one_record_per_title(respond):
    respond.queue.append(
        _FakeResponse(
            payload=_bindings(
                {
                    "imdb": "tt23849204",
                    "item": f"{E}Q122921105",
                    "itemLabel": "12th Fail",
                    "prop": f"{P}P57",
                    "value": f"{E}Q1234",
                    "valueLabel": "Vidhu Vinod Chopra",
                },
                {
                    "imdb": "tt23849204",
                    "item": f"{E}Q122921105",
                    "itemLabel": "12th Fail",
                    "prop": f"{P}P364",
                    "value": f"{E}Q1568",
                    "valueLabel": "Hindi",
                },
            )
        )
    )
    out = wd.lookup_by_imdb(["tt23849204"])
    rec = out["tt23849204"]
    assert rec["qid"] == "Q122921105"
    assert rec["label"] == "12th Fail"
    assert rec["director"] == [{"qid": "Q1234", "label": "Vidhu Vinod Chopra"}]
    assert rec["language"] == [{"qid": "Q1568", "label": "Hindi"}]


def test_lookup_deduplicates_repeated_values(respond):
    row = {
        "imdb": "tt1",
        "item": f"{E}Q1",
        "itemLabel": "X",
        "prop": f"{P}P57",
        "value": f"{E}Q9",
        "valueLabel": "D",
    }
    respond.queue.append(_FakeResponse(payload=_bindings(row, dict(row))))
    assert wd.lookup_by_imdb(["tt1"])["tt1"]["director"] == [{"qid": "Q9", "label": "D"}]


def test_an_unknown_imdb_id_is_simply_absent(respond):
    """Absence is the honest answer -- the caller reports thin enrichment
    rather than falling back to a title match."""
    respond.queue.append(_FakeResponse(payload=_bindings()))
    assert wd.lookup_by_imdb(["tt0000000"]) == {}


def test_lookup_never_queries_by_title(respond):
    """Titles collide: "The Bear" is seven Wikidata items. P345 is the only
    join key this module uses."""
    respond.queue.append(_FakeResponse(payload=_bindings()))
    wd.lookup_by_imdb(["tt1"])
    query = respond[0]
    assert "wdt:P345" in query
    assert "rdfs:label" not in query.replace("wikibase:label", "")


def test_lookup_batches_rather_than_sending_one_query_per_title(respond):
    for _ in range(3):
        respond.queue.append(_FakeResponse(payload=_bindings()))
    wd.lookup_by_imdb([f"tt{i}" for i in range(5)], batch=2)
    assert len(respond) == 3


def test_lookup_of_nothing_sends_nothing(respond):
    assert wd.lookup_by_imdb([]) == {}
    assert respond == []


def test_lookup_ignores_blank_and_duplicate_ids(respond):
    respond.queue.append(_FakeResponse(payload=_bindings()))
    wd.lookup_by_imdb(["tt1", "tt1", "", None])
    assert respond[0].count('"tt1"') == 1


# --- neighbours -------------------------------------------------------------


def test_other_works_only_returns_titles_with_an_imdb_id(respond):
    """A candidate with no IMDb ID cannot cross into Simkl's namespace, so it
    cannot be excluded reliably and must not be recommended."""
    respond.queue.append(
        _FakeResponse(
            payload=_bindings(
                {"work": f"{E}Q1", "workLabel": "Has ID", "imdb": "tt1", "via": f"{E}Q9",
                 "viaLabel": "D", "year": "2014"},
                {"work": f"{E}Q2", "workLabel": "No ID", "via": f"{E}Q9", "viaLabel": "D"},
            )
        )
    )
    out = wd.other_works(["Q9"], "director")
    assert [w["title"] for w in out] == ["Has ID"]
    assert out[0]["year"] == 2014
    assert out[0]["signal"] == "director"


def test_other_works_rejects_an_unknown_relation(respond):
    with pytest.raises(wd.WikidataError, match="Unknown relation"):
        wd.other_works(["Q9"], "vibes")
    assert respond == []


def test_other_works_of_nobody_sends_nothing(respond):
    assert wd.other_works([], "director") == []
    assert respond == []


def test_other_works_handles_a_missing_year(respond):
    respond.queue.append(
        _FakeResponse(
            payload=_bindings(
                {"work": f"{E}Q1", "workLabel": "X", "imdb": "tt1", "via": f"{E}Q9", "viaLabel": "D"}
            )
        )
    )
    assert wd.other_works(["Q9"], "director")[0]["year"] is None


def test_maker_neighbours_caps_what_one_person_contributes(respond):
    """re-com's longest-running defect was an artist-centric graph where two
    seeds sharing an artist returned nearly the same results. Here the
    equivalent is one director's whole filmography flooding a seed."""
    respond.queue.append(_FakeResponse(payload=_bindings()))
    rec = {"imdb": "tt-seed", "director": [{"qid": "Q9", "label": "D"}]}
    wd.maker_neighbours(rec, signals=("director",), per_person=5)
    assert "LIMIT 5" in respond[0]


def test_maker_neighbours_excludes_the_seed_itself(respond):
    respond.queue.append(
        _FakeResponse(
            payload=_bindings(
                {"work": f"{E}Q1", "workLabel": "Seed", "imdb": "tt-seed", "via": f"{E}Q9",
                 "viaLabel": "D"},
                {"work": f"{E}Q2", "workLabel": "Other", "imdb": "tt-other", "via": f"{E}Q9",
                 "viaLabel": "D"},
            )
        )
    )
    rec = {"imdb": "tt-seed", "director": [{"qid": "Q9", "label": "D"}]}
    out = wd.maker_neighbours(rec, signals=("director",))
    assert [w["imdb"] for w in out] == ["tt-other"]


def test_maker_neighbours_tags_each_candidate_with_its_seed_and_signal(respond):
    """Score is the count of distinct (seed, signal) pairs, so a candidate that
    forgets which seed or signal surfaced it cannot be scored or explained."""
    respond.queue.append(
        _FakeResponse(
            payload=_bindings(
                {"work": f"{E}Q2", "workLabel": "PK", "imdb": "tt2338151", "via": f"{E}Q9",
                 "viaLabel": "Shantanu Moitra"}
            )
        )
    )
    rec = {"imdb": "tt-seed", "composer": [{"qid": "Q9", "label": "Shantanu Moitra"}]}
    out = wd.maker_neighbours(rec, signals=("composer",))
    assert out[0]["seed_imdb"] == "tt-seed"
    assert out[0]["signal"] == "composer"
    assert out[0]["via"]["label"] == "Shantanu Moitra"


def test_maker_neighbours_skips_a_signal_the_seed_lacks(respond):
    """62% of Indian films since 2015 carry a director, so a seed with none is
    routine, not exceptional."""
    out = wd.maker_neighbours({"imdb": "tt1"}, signals=("director", "writer"))
    assert out == []
    assert respond == []


def test_cast_is_not_a_default_signal():
    """A blockbuster lists dozens of cast members; "shares a cast member" is
    noise until it can be restricted to billed principals."""
    assert "cast" not in wd.MAKER_SIGNALS
    assert "cast" in wd.PROPERTIES


# --- failure modes ----------------------------------------------------------


def test_a_transient_502_is_retried(respond):
    """Observed live on this module's first run: WDQS's nginx returns a bare
    502 under load, on a query that succeeds unchanged moments later."""
    respond.queue.append(_FakeResponse(status_code=502, text="bad gateway"))
    respond.queue.append(_FakeResponse(payload=_bindings()))
    wd.lookup_by_imdb(["tt1"])
    assert len(respond) == 2


def test_a_persistent_502_is_reported_as_an_outage(respond):
    for _ in range(wd._RETRIES):
        respond.queue.append(_FakeResponse(status_code=503, text="unavailable"))
    with pytest.raises(wd.WikidataError, match="service outage, not a bad query"):
        wd.lookup_by_imdb(["tt1"])


def test_a_429_is_not_retried(respond):
    """Retrying a throttle is how a client gets blocked."""
    respond.queue.append(_FakeResponse(status_code=429, text=""))
    with pytest.raises(wd.WikidataError, match="throttling"):
        wd.lookup_by_imdb(["tt1"])
    assert len(respond) == 1


def test_a_query_timeout_says_to_stop_aggregating(respond):
    """A country-wide aggregate over US films timed out at 60s in the Phase 0
    probe; the fix is per-title lookups, and the error has to say so."""
    respond.queue.append(
        _FakeResponse(status_code=500, text="java.util.concurrent.TimeoutException")
    )
    with pytest.raises(wd.WikidataError, match="individually rather than in aggregate"):
        wd.lookup_by_imdb(["tt1"])


def test_a_network_error_is_translated(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.Timeout("x"))
    )
    with pytest.raises(wd.WikidataError, match="Network error"):
        wd.lookup_by_imdb(["tt1"])


def test_an_unreadable_body_is_translated(respond):
    respond.queue.append(_FakeResponse(payload=None, text="<html>nope</html>"))
    with pytest.raises(wd.WikidataError, match="Unreadable response"):
        wd.lookup_by_imdb(["tt1"])


# --- coverage reporting -----------------------------------------------------


def test_coverage_reports_what_is_actually_present():
    rec = {"director": [{"qid": "Q1"}], "language": [{"qid": "Q2"}], "writer": []}
    cov = wd.coverage(rec)
    assert cov["director"] is True
    assert cov["language"] is True
    assert cov["writer"] is False
    assert cov["composer"] is False


def test_coverage_of_an_empty_record_is_all_false():
    assert not any(wd.coverage({}).values())
