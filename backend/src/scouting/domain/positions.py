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
PEER_ROLES: Final[tuple[PeerRole, ...]] = ("GK", "CB", "FB", "CM", "AM", "W", "ST")

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

# The 8 formations seen in the Phase 0 spike (9 matches, 5 leagues), then the 11 more the
# EPL 2025/26 crawl found: every formation in that season.
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
    # Added after the EPL 2025/26 crawl (`report formations`), with the user's calls:
    # slot 95 (behind a lone striker) is AM; 4-1-3-2's 83/87 are CM (a narrow diamond);
    # 4-3-2-1's 84/86 are AM (as in 3-4-2-1); 5-4-1's wide midfielders are W (D-011).
    "3-5-2": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        71: "WB", 73: "CM", 75: "CM", 77: "CM", 79: "WB",
        104: "ST", 106: "ST",
    },
    "5-4-1": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        51: "WB", 59: "WB",
        72: "W", 74: "CM", 76: "CM", 78: "W",
        115: "ST",
    },
    "4-4-1-1": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        72: "W", 74: "CM", 76: "CM", 78: "W",
        95: "AM",
        115: "ST",
    },
    "3-4-1-2": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        62: "WB", 64: "CM", 66: "CM", 68: "WB",
        85: "AM",
        104: "ST", 106: "ST",
    },
    "4-1-3-2": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        65: "DM",
        83: "CM", 85: "AM", 87: "CM",
        104: "ST", 106: "ST",
    },
    "4-3-2-1": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        63: "CM", 65: "CM", 67: "CM",
        84: "AM", 86: "AM",
        115: "ST",
    },
    "4-5-1": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        71: "W", 73: "CM", 75: "CM", 77: "CM", 79: "W",
        115: "ST",
    },
    "4-2-2-2": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        64: "DM", 66: "DM",
        82: "W", 88: "W",
        104: "ST", 106: "ST",
    },
    "4-3-1-2": {
        11: "GK",
        32: "FB", 34: "CB", 36: "CB", 38: "FB",
        63: "CM", 65: "CM", 67: "CM",
        85: "AM",
        104: "ST", 106: "ST",
    },
    "3-5-1-1": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        71: "WB", 73: "CM", 75: "CM", 77: "CM", 79: "WB",
        95: "AM",
        115: "ST",
    },
    "5-3-1-1": {
        11: "GK",
        33: "CB", 35: "CB", 37: "CB",
        51: "WB", 59: "WB",
        73: "CM", 75: "CM", 77: "CM",
        95: "AM",
        115: "ST",
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


def listed_slot_roles(slot_code: int | None) -> tuple[Role | None, PeerRole | None]:
    """Fallback role for a player with no starts, from the profile's listed slot (SCHEMA §7).

    Formation-free: a slot counts only as far as every mapped formation agrees on it. 64/66
    is DM in a 4-2-3-1 but CM in a 3-4-2-1, so the role is unknown but the peer role is CM.
    84/86 is AM in some formations and CM in others, so both are unknown. Broad categories
    (0-3) and unmapped slots give nothing.
    """
    if slot_code is None:
        return None, None
    roles = {slots[slot_code] for slots in FORMATION_ROLES.values() if slot_code in slots}
    peers = {PEER_ROLE[role] for role in roles}
    role = next(iter(roles)) if len(roles) == 1 else None
    peer = next(iter(peers)) if len(peers) == 1 else None
    return role, peer
