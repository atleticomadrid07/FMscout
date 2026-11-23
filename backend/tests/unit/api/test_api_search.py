"""Slice 3b: the in-memory player search (pure; no database)."""

from __future__ import annotations

import pytest

from scouting.api.search import (
    EXACT,
    INITIAL_SURNAME,
    MIN_SCORE,
    SURNAME,
    Hit,
    PlayerIndex,
    normalize,
)
from scouting.db.queries import SearchEntry

# Hand-made entries in the shape of real names (accents, one-word names, initials). The
# ids are made up; these are not PitchAPI records.
ENTRIES = [
    SearchEntry(1, "p_T01", "Mohamed Salah", "M. Salah", "Red Team", "W", 2900),
    SearchEntry(2, "p_T02", "Martin Ødegaard", "M. Ødegaard", "Gun Team", "AM", 2100),
    SearchEntry(3, "p_T03", "Erling Haaland", "E. Haaland", "Sky Team", "ST", 2800),
    SearchEntry(4, "p_T04", "Rodri", None, "Sky Team", "DM", 600),
    SearchEntry(5, "p_T05", "Rodrigo Muniz", "R. Muniz", "Cottage Team", "ST", 1500),
    SearchEntry(6, "p_T06", "Guido Rodríguez", "G. Rodríguez", "Hammer Team", "DM", 1200),
    SearchEntry(7, "p_T07", "Harry Kane", "H. Kane", "Away Team", "ST", 3000),
    SearchEntry(8, "p_T08", "Kane Smith", "K. Smith", "Small Team", "FB", 400),
    SearchEntry(9, "p_T09", "Jean-Philippe Mateta", "J. Mateta", "Palace Team", "ST", 2500),
    SearchEntry(10, "p_T10", "Hal Backup", None, "Sky Team", "GK", 0),
]


@pytest.fixture
def index() -> PlayerIndex:
    return PlayerIndex(ENTRIES)


def ids(hits: list[Hit]) -> list[str]:
    return [h.entry.external_id for h in hits]


def test_normalize_strips_accents_dots_and_hyphens() -> None:
    assert normalize("  M. Ødegaard ") == "m odegaard"
    assert normalize("Jean-Philippe MATETA") == "jean philippe mateta"
    assert normalize("Guido Rodríguez") == "guido rodriguez"


def test_an_exact_name_wins_and_is_flagged(index: PlayerIndex) -> None:
    hits = index.search("rodri")
    assert hits[0].entry.name == "Rodri" and hits[0].score == EXACT and hits[0].exact
    assert all(not h.exact for h in hits[1:])


def test_an_exact_short_name_counts_as_exact(index: PlayerIndex) -> None:
    hits = index.search("g. rodriguez")
    assert hits[0].entry.external_id == "p_T06" and hits[0].exact


def test_a_surname_scores_just_below_exact(index: PlayerIndex) -> None:
    hits = index.search("salah")
    assert ids(hits)[:1] == ["p_T01"] and hits[0].score == SURNAME and not hits[0].exact


def test_a_surname_beats_a_first_name(index: PlayerIndex) -> None:
    hits = index.search("kane")
    assert ids(hits)[:2] == ["p_T07", "p_T08"]
    assert hits[0].score == SURNAME and hits[1].score < SURNAME


def test_initial_and_surname(index: PlayerIndex) -> None:
    exact = index.search("E Haaland")  # also his short name, so exact
    assert ids(exact) == ["p_T03"] and exact[0].score == EXACT
    typo = index.search("e. halland")
    assert ids(typo) == ["p_T03"] and typo[0].score == INITIAL_SURNAME


def test_the_initial_must_match_the_first_name(index: PlayerIndex) -> None:
    hits = index.search("x. haaland")
    assert ids(hits) == ["p_T03"] and hits[0].score < INITIAL_SURNAME  # only a fuzzy match


def test_accents_and_typos_match_fuzzily(index: PlayerIndex) -> None:
    hits = index.search("odegard")
    assert hits[0].entry.external_id == "p_T02"
    assert MIN_SCORE <= hits[0].score < INITIAL_SURNAME


def test_hyphenated_names(index: PlayerIndex) -> None:
    assert ids(index.search("jean philippe mateta"))[:1] == ["p_T09"]
    assert ids(index.search("mateta"))[:1] == ["p_T09"]


def test_unrelated_queries_find_nothing(index: PlayerIndex) -> None:
    assert index.search("zzzz") == []
    assert index.search("qwerty") == []


def test_filters(index: PlayerIndex) -> None:
    assert ids(index.search("rodri")) == ["p_T04", "p_T05", "p_T06"]
    assert ids(index.search("rodri", role="DM")) == ["p_T04", "p_T06"]
    assert ids(index.search("rodri", team="SKY")) == ["p_T04"]
    assert ids(index.search("rodri", min_minutes=1000)) == ["p_T05", "p_T06"]
    assert ids(index.search("backup")) == ["p_T10"]
    assert index.search("backup", min_minutes=1) == []


def test_ties_go_to_minutes_then_name_and_limit_applies() -> None:
    twins = [
        SearchEntry(1, "p_A", "Ada Keeper", None, "A", "GK", 90),
        SearchEntry(2, "p_B", "Ada Keeper", None, "B", "GK", 180),
        SearchEntry(3, "p_C", "Ada Keeper", None, "C", "GK", 180),
    ]
    assert ids(PlayerIndex(twins).search("ada keeper")) == ["p_B", "p_C", "p_A"]
    assert ids(PlayerIndex(twins).search("ada keeper", limit=2)) == ["p_B", "p_C"]
