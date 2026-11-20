"""Formation + slot → role (SCHEMA.md §7, D-011). Source of truth for `formation_role`.

PitchAPI's starter `position_id` is a grid code: row * 10 + column. The slot alone doesn't
decide the role (84/86 are 10s in a 3-4-2-1 but central midfielders in a 4-1-4-1), so the
mapping is written out per formation and reviewed by hand. Formations not listed here get
role = NULL and are reported by the sync; nothing is guessed.

Side comes from the column: low columns are the right flank (spike-verified: Frimpong 32,
Kerkez 38), 5 is central, and the keeper is always central.
"""

from __future__ import annotations

from typing import Final, Literal

Role = Literal["GK", "CB", "FB", "WB", "DM", "CM", "AM", "W", "ST"]
Side = Literal["L", "C", "R"]
PeerRole = Literal["GK", "CB", "FB", "CM", "AM", "W", "ST"]

ROLES: Final[tuple[Role, ...]] = ("GK", "CB", "FB", "WB", "DM", "CM", "AM", "W", "ST")
SIDES: Final[tuple[Side, ...]] = ("L", "C", "R")

GK_SLOT: Final = 11
CENTRE_COLUMN: Final = 5

# Substitutes' broad category in /lineups.
SUB_CATEGORY_GK: Final = 0
SUB_CATEGORIES: Final = {0: "GK", 1: "DEF", 2: "MID", 3: "ATT", -1: "unclassified"}

# Detailed role → peer group for percentiles (7 groups, D-011).
PEER_ROLE: Final[dict[Role, PeerRole]] = {
    "GK": "GK",
    "CB": "CB",
    "FB": "FB",
    "WB": "FB",
    "DM": "CM",
    "CM": "CM",
    "AM": "AM",
    "W": "W",
    "ST": "ST",
}

# The 8 formations seen in the Phase 0 spike (9 matches, 5 leagues).
FORMATION_ROLES: Final[dict[str, dict[int, Role]]] = {
    "4-2-3-1": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        64: "DM", 66: "DM",
        83: "W", 85: "AM", 87: "W",
        115: "ST",
    },
    "4-3-3": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        73: "CM", 75: "CM", 77: "CM",
        103: "W", 105: "ST", 107: "W",
    },
    "4-4-2": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        72: "W", 74: "CM", 76: "CM", 78: "W",
        104: "ST", 106: "ST",
    },
    "4-1-4-1": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        65: "DM",
        82: "W", 84: "CM", 86: "CM", 88: "W",
        115: "ST",
    },
    "4-1-2-3": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        65: "DM",
        84: "CM", 86: "CM",
        103: "W", 105: "ST", 107: "W",
    },
    "3-4-2-1": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        62: "WB", 64: "CM", 66: "CM", 68: "WB",
        84: "AM", 86: "AM",
        105: "ST",
    },
    "3-4-3": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        72: "WB", 74: "CM", 76: "CM", 78: "WB",
        103: "W", 105: "ST", 107: "W",
    },
    "5-3-2": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        51: "WB", 59: "WB",
        73: "CM", 75: "CM", 77: "CM",
        104: "ST", 106: "ST",
    },
}  # fmt: skip


def side_for(slot_code: int) -> Side:
    if slot_code == GK_SLOT:
        return "C"
    column = slot_code % 10
    if column < CENTRE_COLUMN:
        return "R"
    if column > CENTRE_COLUMN:
        return "L"
    return "C"


def role_for(formation: str | None, slot_code: int) -> tuple[Role, Side] | None:
    """Role and side for a starter, or None when the formation or slot isn't mapped."""
    if formation is None:
        return None
    role = FORMATION_ROLES.get(formation, {}).get(slot_code)
    return None if role is None else (role, side_for(slot_code))


def formation_role_rows() -> list[tuple[str, int, Role, Side]]:
    """Every (formation, slot, role, side) row, for seeding the `formation_role` table."""
    return [
        (formation, slot, role, side_for(slot))
        for formation, slots in FORMATION_ROLES.items()
        for slot, role in slots.items()
    ]
