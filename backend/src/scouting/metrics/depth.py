"""Midfield depth for the 6/8 split (slice 2d, D-017).

Every appearance has a heatmap depth (0 = own goal line, 1 = opponent's; slice 2d-1). On its
own it mixes role with team style: a dominant team plays higher everywhere. So each central
midfielder's depth is taken relative to his own team in the same match, against one of two
reference points:

- "outfield":     the mean depth of the team's starting outfield players, each counted once
                  (so one busy centre-back can't drag the reference back on his own)
- "centre_backs": the mean depth of the team's starting centre-backs (the line a 6 screens)

A player's season value is the minutes-weighted mean of that relative depth over his starts
in central midfield (DM/CM roles) that have a heatmap. The cross-check (never deciding) is how
often he was the deepest of his team's starting central midfielders.

Agreed after the 2d-2 report (EPL 2025/26): the centre-back line decides (it separated the
anchors by 2.6 m against 0.4 m, and put Rice at Arsenal's left 8), the outfield value stays
visible as a second opinion, and the cut is 14 m in front of the centre-backs.

The deeper-partner gap (2d-2b): per match, his depth minus that of his team's deepest other
starting central midfielder; positive = someone beside him plays deeper. A 6 by depth whose
deepest partner is on average clearly deeper is an 8 beside a deeper 6 (Bernardo Silva next
to Rodri). It uses the size of the gap, not the rank, so two genuine 6s a metre apart stay
6s. The user picked G = 2 m from the 2d-2b report (D-018).

The rule (slice 2d-3, `classify_central_mids`, run by aggregation for every central mid):
- at least MIN_DEPTH_STARTS central-mid starts with a heatmap → depth decides: at or above
  the cut an 8 (CM), below it a 6 (DM) unless his deepest partner played at least G deeper
  over at least MIN_PARTNER_STARTS starts beside one, which makes him an 8 ("partner");
- otherwise the formation: a 6 if most of his central-mid minutes came as a lone DM (single
  pivot), else an 8 ("formation").
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from scouting.db.models import (
    Appearance,
    Match,
    PeerGroup,
    Player,
    PlayerScope,
    Scope,
)
from scouting.db.queries import _main_teams
from scouting.domain.positions import CENTRAL_MID_PEER_ROLES

Reference = Literal["outfield", "centre_backs"]
Label = Literal["DM", "CM"]
Method = Literal["depth", "partner", "formation"]  # as models.PEER_METHODS

REFERENCES: Final[tuple[Reference, ...]] = ("outfield", "centre_backs")
REFERENCE_LABELS: Final[dict[Reference, str]] = {
    "outfield": "the team's starting outfield players",
    "centre_backs": "the team's starting centre-backs",
}
CENTRAL_MID_ROLES: Final = ("DM", "CM")  # formation roles: the starts that count
CENTRE_BACK_ROLE: Final = "CB"
LONE_PIVOT_ROLE: Final = "DM"  # a DM-role starter with no other DM beside him: a single pivot
MIN_DEPTH_STARTS: Final = 5  # fewer central-mid starts with a heatmap: formation fallback (D-017)
MIN_PARTNER_STARTS: Final = 5  # starts beside another central mid for the partner rule (D-018)
PITCH_LENGTH_M: Final = 105.0  # for showing depth differences in metres
# Cross-check: a "6" by depth who is rarely his team's deepest midfielder, or an "8" who
# usually is, gets listed for a second look. Only with enough comparable starts.
DEEPEST_OFTEN: Final = 0.7
DEEPEST_RARELY: Final = 0.3
# The agreed rule (D-018).
DEPTH_REFERENCE: Final[Reference] = "centre_backs"
DEPTH_CUT_M: Final = 14.0  # a 6 plays less than 14 m in front of his centre-backs
PARTNER_GAP_M: Final = 2.0  # ... unless his deepest partner plays at least 2 m deeper still
PARTNER_GAP_CANDIDATES_M: Final = (2.0, 3.0, 4.0, 5.0)  # the report's table, to re-check G
BORDERLINE_M: Final = 1.0  # shown as borderline when this close to the cut
DEPTH_CUT: Final = DEPTH_CUT_M / PITCH_LENGTH_M  # the same, as shares of the pitch length
PARTNER_GAP: Final = PARTNER_GAP_M / PITCH_LENGTH_M

# The user's anchors (D-017): clear 6s and 8s in EPL 2025/26, by football judgement. Matched
# as substrings of player.search_name (unidecoded, lowercase). Provisional: "don't take my
# words as 100%", so a report that puts one on the other side is information, not an error.
ANCHORS: Final[tuple[tuple[str, Label], ...]] = (
    ("moises caicedo", "DM"),
    ("tyler adams", "DM"),
    ("granit xhaka", "DM"),
    ("carlos baleba", "DM"),
    ("sander berge", "DM"),
    ("manuel ugarte", "DM"),
    ("james garner", "CM"),  # moved from the 6s: +17.8 m from his CBs (2d-2 report)
    ("bruno guimaraes", "CM"),
    ("enzo fernandez", "CM"),
    ("alex scott", "CM"),
    ("youri tielemans", "CM"),
    ("anton stach", "CM"),
    ("conor gallagher", "CM"),
    ("alexis mac allister", "CM"),
)


class DepthReportError(Exception):
    """The season can't be reported on yet (not aggregated, or no heatmaps)."""


# --- the calculation (pure) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppearanceRow:
    match_id: int
    team_id: int
    player_id: int
    is_starter: bool | None
    is_goalkeeper: bool
    role: str | None
    minutes: int
    depth: float | None


@dataclass(frozen=True, slots=True)
class MatchDepth:
    """One central-midfield start, relative to the player's team in that match."""

    player_id: int
    match_id: int
    minutes: int
    relative: float  # his depth minus the team's reference; negative = deeper
    deepest: bool | None  # deepest of the team's starting central mids; None if he was alone
    partner_gap: float | None  # his depth minus his deepest partner's; None if he was alone


def _reference(starters: Sequence[AppearanceRow], reference: Reference) -> float | None:
    if reference == "outfield":
        depths = [r.depth for r in starters if not r.is_goalkeeper and r.depth is not None]
    else:
        depths = [r.depth for r in starters if r.role == CENTRE_BACK_ROLE and r.depth is not None]
    return fmean(depths) if depths else None


def match_depths(rows: Iterable[AppearanceRow], reference: Reference) -> list[MatchDepth]:
    """Every central-midfield start with a heatmap whose team has the reference point."""
    by_side: dict[tuple[int, int], list[AppearanceRow]] = defaultdict(list)
    for row in rows:
        if row.is_starter:
            by_side[(row.match_id, row.team_id)].append(row)
    out: list[MatchDepth] = []
    for (match_id, _), starters in sorted(by_side.items()):
        ref = _reference(starters, reference)
        mids = [r for r in starters if r.role in CENTRAL_MID_ROLES and r.depth is not None]
        if ref is None or not mids:
            continue
        depths = {r.player_id: r.depth for r in mids if r.depth is not None}
        deepest = min(depths.values())
        for r in sorted(mids, key=lambda m: m.player_id):
            assert r.depth is not None  # filtered above; narrows the type
            partners = [d for pid, d in depths.items() if pid != r.player_id]
            out.append(
                MatchDepth(
                    player_id=r.player_id,
                    match_id=match_id,
                    minutes=r.minutes,
                    relative=r.depth - ref,
                    deepest=None if not partners else r.depth == deepest,
                    partner_gap=None if not partners else r.depth - min(partners),
                )
            )
    return out


@dataclass(frozen=True, slots=True)
class SeasonDepth:
    player_id: int
    starts: int  # central-midfield starts with a heatmap and a reference point
    minutes: int
    relative: float | None  # minutes-weighted mean; None with no minutes
    comparable: int  # of those starts, how many had another central mid to compare with
    deepest: int  # ... and in how many he was the deepest
    partner_gap: float | None = None  # minutes-weighted over the comparable starts

    @property
    def deepest_share(self) -> float | None:
        return self.deepest / self.comparable if self.comparable else None

    @property
    def enough_starts(self) -> bool:
        return self.starts >= MIN_DEPTH_STARTS and self.relative is not None


def season_depths(matches: Iterable[MatchDepth]) -> dict[int, SeasonDepth]:
    by_player: dict[int, list[MatchDepth]] = defaultdict(list)
    for m in matches:
        by_player[m.player_id].append(m)
    out: dict[int, SeasonDepth] = {}
    for player_id, starts in by_player.items():
        minutes = sum(s.minutes for s in starts)
        paired = [s for s in starts if s.partner_gap is not None]
        paired_minutes = sum(s.minutes for s in paired)
        out[player_id] = SeasonDepth(
            player_id=player_id,
            starts=len(starts),
            minutes=minutes,
            relative=sum(s.relative * s.minutes for s in starts) / minutes if minutes else None,
            comparable=len(paired),
            deepest=sum(1 for s in starts if s.deepest),
            partner_gap=(
                sum((s.partner_gap or 0.0) * s.minutes for s in paired) / paired_minutes
                if paired_minutes
                else None
            ),
        )
    return out


@dataclass(frozen=True, slots=True)
class Cut:
    value: float  # relative depth: below = 6 (DM), at or above = 8 (CM)
    wrong: tuple[str, ...]  # anchors on the other side of it
    margin: float  # distance from the cut to the nearest anchor


def best_cut(anchors: Sequence[tuple[str, Label, float]]) -> Cut | None:
    """The cut that puts the fewest anchors on the wrong side; among equals, the one in the
    widest gap (largest margin), then the lowest. None without at least one 6 and one 8."""
    if not any(label == "DM" for _, label, _ in anchors) or not any(
        label == "CM" for _, label, _ in anchors
    ):
        return None
    values = sorted({v for _, _, v in anchors})
    candidates = [(a + b) / 2 for a, b in pairwise(values)]
    if not candidates:  # every anchor at the same depth: no line separates anyone
        candidates = [values[0]]

    def wrong(cut: float) -> tuple[str, ...]:
        return tuple(
            name
            for name, label, value in anchors
            if (label == "DM" and value >= cut) or (label == "CM" and value < cut)
        )

    def margin(cut: float) -> float:
        return min(abs(v - cut) for _, _, v in anchors)

    best = min(candidates, key=lambda c: (len(wrong(c)), -margin(c), c))
    return Cut(best, wrong(best), margin(best))


def label_for(relative: float, cut: float) -> Label:
    return "DM" if relative < cut else "CM"


def classify(
    depth: SeasonDepth | None, cut: float | None = None, gap: float | None = None
) -> tuple[Label, Method] | None:
    """The 6/8 call from a season's depth (D-018), or None when the formation must decide.
    `cut` and `gap` default to the agreed DEPTH_CUT and PARTNER_GAP (read at call time)."""
    cut = DEPTH_CUT if cut is None else cut
    gap = PARTNER_GAP if gap is None else gap
    if depth is None or not depth.enough_starts or depth.relative is None:
        return None
    if depth.relative >= cut:
        return "CM", "depth"
    if (
        depth.partner_gap is not None
        and depth.comparable >= MIN_PARTNER_STARTS
        and depth.partner_gap >= gap
    ):
        return "CM", "partner"
    return "DM", "depth"


def is_borderline(relative: float, cut: float | None = None) -> bool:
    """Within BORDERLINE_M of the cut (default: the agreed one); the distribution has no
    natural gap, so these labels are close calls (D-018)."""
    cut = DEPTH_CUT if cut is None else cut
    return abs(relative - cut) < BORDERLINE_M / PITCH_LENGTH_M


def lone_pivot_labels(rows: Iterable[AppearanceRow]) -> dict[int, Label]:
    """The formation fallback for every player with a central-mid start: a 6 if more than
    half of those minutes came as the only DM-role starter in his team (a single pivot),
    else an 8. A double pivot's DMs (4-2-3-1) mix 6s and 8s, so they count as 8s here."""
    by_side: dict[tuple[int, int], list[AppearanceRow]] = defaultdict(list)
    for row in rows:
        if row.is_starter and row.role in CENTRAL_MID_ROLES:
            by_side[(row.match_id, row.team_id)].append(row)
    lone: dict[int, int] = defaultdict(int)
    total: dict[int, int] = defaultdict(int)
    for mids in by_side.values():
        pivots = [r for r in mids if r.role == LONE_PIVOT_ROLE]
        for r in mids:
            total[r.player_id] += r.minutes
            if len(pivots) == 1 and r is pivots[0]:
                lone[r.player_id] += r.minutes
    return {pid: "DM" if lone[pid] * 2 > minutes else "CM" for pid, minutes in total.items()}


@dataclass(frozen=True, slots=True)
class Classification:
    peer_role: Label
    method: Method
    depth: SeasonDepth | None  # against DEPTH_REFERENCE; None: no start with a heatmap


def classify_central_mids(
    rows: Sequence[AppearanceRow], player_ids: Iterable[int]
) -> dict[int, Classification]:
    """The 6/8 group of each given player (the central mids of one season), by depth where
    he has enough starts with a heatmap, else by formation. Pure: aggregation stores it."""
    depths = season_depths(match_depths(rows, DEPTH_REFERENCE))
    fallback = lone_pivot_labels(rows)
    out: dict[int, Classification] = {}
    for player_id in player_ids:
        season = depths.get(player_id)
        call = classify(season)
        out[player_id] = (
            Classification(fallback.get(player_id, "CM"), "formation", season)
            if call is None
            else Classification(call[0], call[1], season)
        )
    return out


def histogram(values: Sequence[float], width: float) -> list[tuple[float, int]]:
    """(bin start, count) for consecutive bins of `width` covering every value."""
    if not values:
        return []
    first = math.floor(min(values) / width)
    last = math.floor(max(values) / width)
    counts = [0] * (last - first + 1)
    for v in values:
        counts[math.floor(v / width) - first] += 1
    return [((first + i) * width, n) for i, n in enumerate(counts)]


# --- the report (reads scouting.db) -----------------------------------------------------------


def load_rows(session: Session, season_id: int) -> list[AppearanceRow]:
    return [
        AppearanceRow(*row)
        for row in session.execute(
            select(
                Appearance.match_id,
                Appearance.team_id,
                Appearance.player_id,
                Appearance.is_starter,
                Appearance.is_goalkeeper,
                Appearance.role,
                Appearance.minutes,
                Appearance.depth,
            )
            .join(Match, Match.id == Appearance.match_id)
            .where(Match.season_id == season_id)
            .order_by(Appearance.id)
        )
    ]


@dataclass(frozen=True, slots=True)
class PlayerLine:
    player_id: int
    name: str
    team: str
    depth: SeasonDepth | None  # against DEPTH_REFERENCE; None: no central-mid start with a heatmap
    outfield: float | None = None  # the second opinion: relative to the outfield players
    stored: str | None = None  # the peer role aggregation stored (DM or CM)
    method: str | None = None  # ... and how it was decided (depth, partner, formation)


@dataclass(frozen=True, slots=True)
class AnchorLine:
    query: str  # as written in ANCHORS
    label: Label  # the user's call
    player: PlayerLine | None  # None if not found, ambiguous, or not in the group
    status: str  # "ok", or why the anchor can't be used


@dataclass(frozen=True, slots=True)
class Calibration:
    min_minutes: int
    group: list[PlayerLine]  # the DM + CM peer groups, deepest first; too few starts last
    anchors: list[AnchorLine]
    cut: float  # the agreed cut (relative depth), or one being tried
    suggested: Cut | None  # the cut that best separates the anchors, for comparison

    @property
    def classified(self) -> list[PlayerLine]:
        return [p for p in self.group if p.depth is not None and p.depth.enough_starts]

    def sixes(self) -> list[PlayerLine]:
        """6s by depth alone, the largest deeper-partner gap first (none at the end)."""

        def order(p: PlayerLine) -> tuple[float, str]:
            gap = _gap(p)
            return (-gap if gap is not None else math.inf, p.name)

        return sorted((p for p in self.classified if _relative(p) < self.cut), key=order)

    def partner_flips(self, gap: float) -> list[PlayerLine]:
        """6s by depth whose deepest partner is on average at least `gap` deeper (with
        enough starts beside a partner): 8s beside a deeper 6 under that rule."""
        return [
            p
            for p in self.sixes()
            if p.depth is not None
            and p.depth.comparable >= MIN_PARTNER_STARTS
            and (g := _gap(p)) is not None
            and g >= gap
        ]

    def sizes(self, gap: float | None = None) -> tuple[int, int, int]:
        """(DM, CM, formation fallback) with the cut, and the partner rule if `gap` is set."""
        flipped = 0 if gap is None else len(self.partner_flips(gap))
        dm = sum(1 for p in self.classified if _relative(p) < self.cut) - flipped
        return dm, len(self.classified) - dm, len(self.group) - len(self.classified)

    def stored_counts(self) -> tuple[Counter[str | None], Counter[str | None]]:
        """(players per stored peer role, per method) in the group."""
        return Counter(p.stored for p in self.group), Counter(p.method for p in self.group)

    def mismatches(self) -> list[PlayerLine]:
        """Players whose stored group or method isn't what the rule gives now (at this cut and
        the agreed G): the aggregates are older than the rule, so `scouting aggregate`."""
        out = []
        for p in self.group:
            call = classify(p.depth, self.cut)
            if call is None:  # the formation decides; only the method can be checked here
                if p.method != "formation":
                    out.append(p)
            elif (p.stored, p.method) != call:
                out.append(p)
        return out

    def nearest(self, n: int) -> list[PlayerLine]:
        """The n classified players closest to the cut."""
        return sorted(self.classified, key=lambda p: (abs(_relative(p) - self.cut), p.name))[:n]

    def disagreements(self) -> list[PlayerLine]:
        """Players the cross-check would label differently: a 6 who is rarely his team's
        deepest midfielder, or an 8 who usually is (with enough comparable starts)."""
        out = []
        for p in self.classified:
            assert p.depth is not None
            share = p.depth.deepest_share
            if share is None or p.depth.comparable < MIN_DEPTH_STARTS:
                continue
            label = label_for(_relative(p), self.cut)
            if (label == "DM" and share <= DEEPEST_RARELY) or (
                label == "CM" and share >= DEEPEST_OFTEN
            ):
                out.append(p)
        return out


def _relative(p: PlayerLine) -> float:
    assert p.depth is not None and p.depth.relative is not None
    return p.depth.relative


def _gap(p: PlayerLine) -> float | None:
    return None if p.depth is None else p.depth.partner_gap


def calibrate(
    session: Session,
    season_id: int,
    cut: float | None = None,
    rows: list[AppearanceRow] | None = None,
) -> Calibration:
    """The central mids' season depths (DEPTH_REFERENCE, plus the outfield value as a second
    opinion) with their stored groups, the anchors, the cut, and the cut the anchors alone
    would suggest. The group is the DM + CM peer groups together."""
    scope_id = session.scalar(select(Scope.id).where(Scope.season_id == season_id))
    if scope_id is None:
        raise DepthReportError("season isn't aggregated yet: run `scouting aggregate`")
    group = session.scalars(
        select(PeerGroup)
        .where(PeerGroup.scope_id == scope_id, PeerGroup.peer_role.in_(CENTRAL_MID_PEER_ROLES))
        .order_by(PeerGroup.id)
    ).first()
    if group is None:
        raise DepthReportError("no central-midfield peer group: run `scouting aggregate`")
    rows = load_rows(session, season_id) if rows is None else rows
    if not any(r.depth is not None for r in rows):
        raise DepthReportError(
            "no heatmap depths in this season yet: run `scouting sync season` online once"
        )

    depths = season_depths(match_depths(rows, DEPTH_REFERENCE))
    second = season_depths(match_depths(rows, "outfield"))
    teams = _main_teams(session, scope_id)
    members = {
        player_id: PlayerLine(
            player_id,
            name,
            teams.get(player_id, "?"),
            depths.get(player_id),
            second[player_id].relative if player_id in second else None,
            stored,
            method,
        )
        for player_id, name, stored, method in session.execute(
            select(Player.id, Player.name, PlayerScope.peer_role, PlayerScope.peer_method)
            .join(PlayerScope, PlayerScope.player_id == Player.id)
            .where(
                PlayerScope.scope_id == scope_id,
                PlayerScope.peer_role.in_(CENTRAL_MID_PEER_ROLES),
                PlayerScope.minutes >= group.min_minutes,
            )
        )
    }
    anchors = [
        _anchor(session, scope_id, group.min_minutes, query, label, members)
        for query, label in ANCHORS
    ]
    usable = [
        (a.query, a.label, _relative(a.player)) for a in anchors if a.status == "ok" and a.player
    ]
    ordered = sorted(
        members.values(),
        key=lambda p: (
            not (p.depth is not None and p.depth.enough_starts),
            _relative(p) if p.depth is not None and p.depth.relative is not None else 0.0,
            p.name,
        ),
    )
    return Calibration(
        group.min_minutes,
        ordered,
        anchors,
        DEPTH_CUT if cut is None else cut,
        best_cut(usable),
    )


def _anchor(
    session: Session,
    scope_id: int,
    min_minutes: int,
    query: str,
    label: Label,
    members: dict[int, PlayerLine],
) -> AnchorLine:
    found = session.execute(
        select(Player.id, PlayerScope.peer_role, PlayerScope.primary_role, PlayerScope.minutes)
        .join(PlayerScope, PlayerScope.player_id == Player.id)
        .where(PlayerScope.scope_id == scope_id, Player.search_name.contains(query))
    ).all()
    if not found:
        return AnchorLine(query, label, None, "not found in this season")
    if len(found) > 1:
        return AnchorLine(query, label, None, f"ambiguous ({len(found)} players match)")
    player_id, peer_role, primary_role, minutes = found[0]
    if player_id in members:
        line = members[player_id]
        if line.depth is None or not line.depth.enough_starts:
            starts = 0 if line.depth is None else line.depth.starts
            return AnchorLine(
                query, label, line, f"only {starts} central-mid starts with a heatmap"
            )
        return AnchorLine(query, label, line, "ok")
    if minutes < min_minutes:
        return AnchorLine(query, label, None, f"below the minimum ({minutes} < {min_minutes} min)")
    return AnchorLine(
        query, label, None, f"in the {peer_role or 'no'} group (mostly {primary_role or '?'})"
    )
