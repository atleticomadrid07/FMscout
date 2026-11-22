"""Player search (slice 3b, D-020 decision 1): an in-memory index per scope, matched with
rapidfuzz on accent-free names. Pure: built from db.queries.search_entries, so it's tested
without a database.

Ranking, best first:
1. the whole name or short name equals the query ("rodri" → Rodri, "g. rodriguez");
2. the surname equals it ("salah" → Mohamed Salah, "kane" → Harry Kane before Kane Smith);
3. an initial plus a surname ("E. Haaland": first name starts with E, surname ≈ haaland);
4. otherwise the best rapidfuzz WRatio against the name, the short name and the surname
   (typos and partial names: "odegard", "haland"), capped below the tiers above.
Scores under MIN_SCORE are dropped; ties go to more minutes, then the name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz

from scouting.db.queries import SearchEntry
from scouting.domain.names import search_name

MIN_SCORE: Final = 75.0
EXACT: Final = 100.0
SURNAME: Final = 97.0
INITIAL_SURNAME: Final = 95.0  # an "E. Haaland" match: below exact, above fuzzy ones
INITIAL_SURNAME_RATIO: Final = 85.0  # how close the surname must be


def normalize(text: str) -> str:
    """Accent-free, lower-case, dots and hyphens as spaces, single spaces."""
    return " ".join(search_name(text).replace(".", " ").replace("-", " ").split())


@dataclass(frozen=True, slots=True)
class Hit:
    entry: SearchEntry
    score: float
    exact: bool


@dataclass(frozen=True, slots=True)
class _Indexed:
    entry: SearchEntry
    names: tuple[str, ...]  # the normalized name, then the short name if any
    first: str  # first token of the full name
    surname: str  # the rest of the full name ("" for a one-word name)
    team: str  # normalized team, for the filter


class PlayerIndex:
    def __init__(self, entries: list[SearchEntry]) -> None:
        self._items = []
        for entry in entries:
            full = normalize(entry.name)
            names = (full,) + ((normalize(entry.short_name),) if entry.short_name else ())
            first, _, surname = full.partition(" ")
            self._items.append(_Indexed(entry, names, first, surname, normalize(entry.team or "")))

    def __len__(self) -> int:
        return len(self._items)

    def search(
        self,
        query: str,
        *,
        team: str | None = None,
        role: str | None = None,
        min_minutes: int = 0,
        limit: int = 10,
    ) -> list[Hit]:
        q = normalize(query)
        team_q = normalize(team) if team else None
        tokens = q.split()
        initial = tokens[0] if len(tokens) >= 2 and len(tokens[0]) == 1 else None
        rest = " ".join(tokens[1:]) if initial else ""
        hits = []
        for item in self._items:
            entry = item.entry
            if role is not None and entry.peer_role != role:
                continue
            if entry.minutes < min_minutes:
                continue
            if team_q is not None and team_q not in item.team:
                continue
            score = self._score(q, item, initial, rest)
            if score >= MIN_SCORE:
                hits.append(Hit(entry, score, score == EXACT))
        hits.sort(key=lambda h: (-h.score, -h.entry.minutes, h.entry.name))
        return hits[:limit]

    @staticmethod
    def _score(q: str, item: _Indexed, initial: str | None, rest: str) -> float:
        if q in item.names:
            return EXACT
        if item.surname and (q == item.surname or q == item.surname.split()[-1]):
            return SURNAME
        if (
            initial is not None
            and item.surname
            and item.first.startswith(initial)
            and fuzz.ratio(rest, item.surname) >= INITIAL_SURNAME_RATIO
        ):
            return INITIAL_SURNAME
        candidates = [*item.names, item.surname] if item.surname else list(item.names)
        # Never let a fuzzy match tie with an exact one.
        return min(max(fuzz.WRatio(q, name) for name in candidates), INITIAL_SURNAME - 1)
