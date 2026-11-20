"""The formation → role table (SCHEMA.md §7): structure checks plus known real players."""

from __future__ import annotations

import pytest

from scouting.domain.positions import (
    FORMATION_ROLES,
    GK_SLOT,
    PEER_ROLE,
    ROLES,
    formation_role_rows,
    listed_slot_roles,
    role_for,
    side_for,
)


@pytest.mark.parametrize("formation", sorted(FORMATION_ROLES))
def test_each_formation_has_eleven_slots_and_one_keeper(formation: str) -> None:
    slots = FORMATION_ROLES[formation]
    assert len(slots) == 11
    assert [s for s, role in slots.items() if role == "GK"] == [GK_SLOT]


@pytest.mark.parametrize("formation", sorted(FORMATION_ROLES))
def test_outfield_slots_match_the_formation_string(formation: str) -> None:
    """ "4-2-3-1" has 4 + 2 + 3 + 1 = 10 outfield slots, in that many distinct rows."""
    outfield = [slot for slot in FORMATION_ROLES[formation] if slot != GK_SLOT]
    lines = [int(n) for n in formation.split("-")]
    assert len(outfield) == sum(lines)


@pytest.mark.parametrize("formation", sorted(FORMATION_ROLES))
def test_formations_are_left_right_symmetric(formation: str) -> None:
    """Mirroring a slot across the pitch (column c ↔ 10 - c) gives the same role."""
    slots = FORMATION_ROLES[formation]
    for slot, role in slots.items():
        row, column = divmod(slot, 10)
        mirror = row * 10 + (10 - column)
        if slot != GK_SLOT:
            assert slots.get(mirror) == role, f"{formation} {slot} vs {mirror}"


@pytest.mark.parametrize(
    ("slot", "side"),
    [(11, "C"), (32, "R"), (38, "L"), (35, "C"), (83, "R"), (87, "L"), (105, "C"), (115, "C"),
     (51, "R"), (59, "L"), (103, "R"), (107, "L")],
)  # fmt: skip
def test_side_low_column_is_right(slot: int, side: str) -> None:
    assert side_for(slot) == side


@pytest.mark.parametrize(
    ("player", "formation", "slot", "expected"),
    [
        # Known players from the spike (PITCHAPI_NOTES): sides and roles must come out right.
        ("Frimpong (RB)", "4-2-3-1", 32, ("FB", "R")),
        ("Kerkez (LB)", "4-2-3-1", 38, ("FB", "L")),
        ("Calafiori (LB)", "4-3-3", 38, ("FB", "L")),
        ("Wirtz (10)", "4-2-3-1", 85, ("AM", "C")),
        ("Salah (RW)", "4-2-3-1", 83, ("W", "R")),
        ("Saka (RW)", "4-3-3", 103, ("W", "R")),
        ("Rice (left CM)", "4-3-3", 77, ("CM", "L")),
        ("Haaland (ST)", "4-3-3", 105, ("ST", "C")),
        ("Alisson (GK)", "4-2-3-1", 11, ("GK", "C")),
    ],
)
def test_known_players(player: str, formation: str, slot: int, expected: tuple[str, str]) -> None:
    assert role_for(formation, slot) == expected, player


def test_same_slot_differs_by_formation() -> None:
    assert role_for("3-4-2-1", 84) == ("AM", "R")  # a 10
    assert role_for("4-1-4-1", 84) == ("CM", "R")  # an 8 in front of a single pivot


@pytest.mark.parametrize(("formation", "slot"), [("4-2-3-1", 99), ("9-9-9", 11), (None, 11)])
def test_unmapped_returns_none(formation: str | None, slot: int) -> None:
    assert role_for(formation, slot) is None


def test_every_role_has_a_peer_group() -> None:
    assert set(PEER_ROLE) == set(ROLES)
    assert PEER_ROLE["AM"] == "AM"  # 10s are their own group (D-011)
    assert PEER_ROLE["WB"] == "FB" and PEER_ROLE["DM"] == "CM"


def test_formation_role_rows_cover_every_slot() -> None:
    rows = formation_role_rows()
    assert len(rows) == 11 * len(FORMATION_ROLES)
    assert len({(f, s) for f, s, _, _ in rows}) == len(rows)


@pytest.mark.parametrize(
    ("formation", "slot", "expected"),
    [
        ("4-4-1-1", 95, ("AM", "C")),  # a: behind a lone striker → AM
        ("3-5-1-1", 95, ("AM", "C")),
        ("5-3-1-1", 95, ("AM", "C")),
        ("4-1-3-2", 83, ("CM", "R")),  # b: narrow diamond → CM
        ("4-1-3-2", 87, ("CM", "L")),
        ("4-3-2-1", 84, ("AM", "R")),  # c: as in 3-4-2-1
        ("5-4-1", 72, ("W", "R")),  # d: wide midfielders go with wingers (D-011)
        ("5-4-1", 51, ("WB", "R")),
    ],
)
def test_user_judgement_calls_after_the_epl_crawl(
    formation: str, slot: int, expected: tuple[str, str]
) -> None:
    assert role_for(formation, slot) == expected


def test_every_formation_has_one_keeper_and_ten_outfield_slots() -> None:
    for formation, slots in FORMATION_ROLES.items():
        assert list(slots.values()).count("GK") == 1, formation
        defenders = sum(1 for slot in slots if 30 <= slot < 60)
        assert defenders == int(formation.split("-")[0]), formation  # rows 3 and 5


@pytest.mark.parametrize(
    ("slot", "expected"),
    [
        (11, ("GK", "GK")),
        (32, ("FB", "FB")),
        (36, ("CB", "CB")),
        (64, (None, "CM")),  # DM in 4-2-3-1, CM in 3-4-2-1: same peer group
        (85, ("AM", "AM")),
        (95, ("AM", "AM")),
        (115, ("ST", "ST")),
        (84, (None, None)),  # AM in 3-4-2-1 but CM in 4-1-4-1: undecided
        (83, (None, None)),  # W in 4-2-3-1 but CM in 4-1-3-2
        (72, (None, None)),  # W in 4-4-2 but WB in 3-4-3
        (2, (None, None)),  # a broad category, not a slot
        (None, (None, None)),
    ],
)
def test_listed_slot_roles_only_when_every_formation_agrees(
    slot: int | None, expected: tuple[str | None, str | None]
) -> None:
    assert listed_slot_roles(slot) == expected
