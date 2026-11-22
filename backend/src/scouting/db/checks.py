"""Read-only data checks for slices 2c and 2e: the open questions in PLAN.md (D-014, D-015).

Each check answers one question about what PitchAPI's numbers mean, from scouting.db alone
(no raw.db, no API calls, no writes). `scripts/check_2c.py` prints them; the user runs it
on the real database and pastes the output back, so the checks report evidence and leave
the call to a person.
"""

from __future__ import annotations

import itertools
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from scouting.db.models import (
    Appearance,
    Match,
    Metric,
    PeerGroup,
    Percentile,
    Player,
    PlayerScope,
    Scope,
    StatValue,
    Team,
)
from scouting.db.queries import _main_teams
from scouting.domain.positions import FORMATION_ROLES, PEER_ROLES


class CheckError(Exception):
    """A check can't run on this database (missing metric, season not aggregated)."""


def _metric_ids(session: Session, codes: Iterable[str]) -> dict[str, int]:
    wanted = set(codes)
    found = {
        code: metric_id
        for code, metric_id in session.execute(
            select(Metric.code, Metric.id).where(Metric.code.in_(wanted))
        )
    }
    missing = sorted(wanted - found.keys())
    if missing:
        raise CheckError(f"metric(s) not in scouting.db: {', '.join(missing)} (run a sync first)")
    return found


def _scope_id(session: Session, season_id: int) -> int:
    scope_id = session.scalar(select(Scope.id).where(Scope.season_id == season_id))
    if scope_id is None:
        raise CheckError("season isn't aggregated yet: run `scouting aggregate`")
    return scope_id


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson's r, or None when it's undefined (fewer than 2 points, or a constant)."""
    if len(xs) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


# --- 1. was_fouled: does NULL mean 0? -----------------------------------------------------------
#
# Every foul a team commits is a foul another player suffers, so per match and team,
# Σ was_fouled of the team should equal Σ fouls_committed of the opponent. Reading NULL as 0
# makes the two sides comparable. If NULL really meant "unknown, maybe 3", the gap
# (opponent fouls - known was_fouled) would grow with the number of NULL rows; if NULL means
# 0, the gap stays flat whatever the NULL count. Keepers have no was_fouled row
# (applies_to = outfield), so a foul on a keeper shows up as a gap of 1.

# Team-matches grouped by how many of the team's outfield rows are NULL: (low, high, label).
NULL_BUCKETS: Final[tuple[tuple[int, int | None, str], ...]] = (
    (0, 0, "0"),
    (1, 3, "1-3"),
    (4, 6, "4-6"),
    (7, 9, "7-9"),
    (10, None, "10+"),
)
GAP_CLAMP: Final = 3  # gaps beyond ±3 are counted together in the distribution


@dataclass(frozen=True, slots=True)
class NullBucket:
    label: str
    team_matches: int
    mean_gap: float | None
    exact: int  # team-matches with a gap of 0


@dataclass(frozen=True, slots=True)
class FouledCheck:
    team_matches: int
    exact: int
    within_one: int
    gaps: dict[int, int]  # gap (clamped to ±GAP_CLAMP) -> team-matches
    buckets: list[NullBucket]
    values: dict[float | None, int]  # stored was_fouled value -> outfield rows
    opponent_fouls: float
    known_fouled: float
    fouls_null_rows: int  # fouls_committed rows stored as NULL (counted as 0 here)
    one_sided: int  # team-matches skipped: the opponent has no appearances


def check_was_fouled(session: Session, season_id: int) -> FouledCheck:
    ids = _metric_ids(session, ("was_fouled", "fouls_committed"))
    fouled, fouls = aliased(StatValue), aliased(StatValue)
    rows = session.execute(
        select(
            Appearance.match_id,
            Appearance.team_id,
            fouled.metric_id,
            fouled.value,
            fouls.metric_id,
            fouls.value,
        )
        .join(Match, Match.id == Appearance.match_id)
        .outerjoin(
            fouled,
            and_(fouled.appearance_id == Appearance.id, fouled.metric_id == ids["was_fouled"]),
        )
        .outerjoin(
            fouls,
            and_(fouls.appearance_id == Appearance.id, fouls.metric_id == ids["fouls_committed"]),
        )
        .where(Match.season_id == season_id)
    )
    known: dict[tuple[int, int], float] = defaultdict(float)
    nulls: dict[tuple[int, int], int] = defaultdict(int)
    committed: dict[tuple[int, int], float] = defaultdict(float)
    values: Counter[float | None] = Counter()
    fouls_null_rows = 0
    for match_id, team_id, fouled_row, fouled_value, fouls_row, fouls_value in rows:
        key = (match_id, team_id)
        committed[key] += fouls_value or 0.0  # no row or NULL: nothing committed that we know
        if fouls_row is not None and fouls_value is None:
            fouls_null_rows += 1
        if fouled_row is None:  # a keeper: was_fouled doesn't apply
            continue
        values[fouled_value] += 1
        if fouled_value is None:
            nulls[key] += 1
        else:
            known[key] += fouled_value

    sides = {
        match_id: (home, away)
        for match_id, home, away in session.execute(
            select(Match.id, Match.home_team_id, Match.away_team_id).where(
                Match.season_id == season_id
            )
        )
    }
    gaps: Counter[int] = Counter()
    by_bucket: dict[str, list[float]] = defaultdict(list)
    exact = within_one = one_sided = 0
    opponent_total = known_total = 0.0
    for match_id, team_id in sorted(committed):
        home, away = sides[match_id]
        opponent = (match_id, away if team_id == home else home)
        if opponent not in committed:
            one_sided += 1
            continue
        gap = committed[opponent] - known[(match_id, team_id)]
        opponent_total += committed[opponent]
        known_total += known[(match_id, team_id)]
        rounded = round(gap)
        gaps[max(-GAP_CLAMP, min(GAP_CLAMP, rounded))] += 1
        exact += rounded == 0
        within_one += abs(rounded) <= 1
        by_bucket[_bucket(nulls[(match_id, team_id)])].append(gap)

    buckets = [
        NullBucket(
            label,
            len(by_bucket[label]),
            statistics.fmean(by_bucket[label]) if by_bucket[label] else None,
            sum(round(g) == 0 for g in by_bucket[label]),
        )
        for _, _, label in NULL_BUCKETS
    ]
    return FouledCheck(
        team_matches=sum(len(g) for g in by_bucket.values()),
        exact=exact,
        within_one=within_one,
        gaps=dict(sorted(gaps.items())),
        buckets=buckets,
        values=dict(sorted(values.items(), key=lambda kv: (kv[0] is not None, kv[0] or 0))),
        opponent_fouls=opponent_total,
        known_fouled=known_total,
        fouls_null_rows=fouls_null_rows,
        one_sided=one_sided,
    )


def _bucket(null_rows: int) -> str:
    for low, high, label in NULL_BUCKETS:
        if null_rows >= low and (high is None or null_rows <= high):
            return label
    raise ValueError(f"negative NULL count: {null_rows}")  # pragma: no cover


# --- 2. keeper rates: does the sent rate equal the ratio of the counts? ---------------------------
#
# launch_pct and claim_rate are stored as rate x weight over weight (value_is_rate), so the
# rate PitchAPI sent for one match is value / denominator. If launch_pct means launches /
# distributions (x 100), it's redundant with the counts and either can go on a template.

Field = Literal["value", "denominator"]
RATE_TOLERANCE_PCT: Final = 0.5  # percentage points: covers a provider rounding to whole %
WORST_ROWS: Final = 5


@dataclass(frozen=True, slots=True)
class RatePart:
    code: str
    field: Field


@dataclass(frozen=True, slots=True)
class RateSpec:
    rate_code: str
    numerator: RatePart
    denominator: RatePart
    formula: str


RATE_SPECS: Final[tuple[RateSpec, ...]] = (
    RateSpec(
        "launch_pct",
        RatePart("launches", "value"),
        RatePart("distributions", "value"),
        "launches / distributions",
    ),
    # Not one of 2c's questions, but the same test settles 2d's claims_won/claim_rate pair.
    RateSpec(
        "claim_rate",
        RatePart("claims_won", "value"),
        RatePart("claims_won", "denominator"),
        "claims_won / claims",
    ),
)


@dataclass(frozen=True, slots=True)
class RateRow:
    player: str
    match_label: str
    sent: float
    from_counts: float  # on the same scale as `sent`
    attempts: float

    @property
    def diff(self) -> float:
        return self.sent - self.from_counts


@dataclass(frozen=True, slots=True)
class RateCheck:
    rate_code: str
    formula: str
    rows_with_rate: int  # keeper appearances with a stored rate row
    compared: int
    no_attempts: int  # attempts = 0: the rate is undefined, nothing to compare
    missing: int  # the rate or a count is NULL or has no row
    scale: int  # 100 if PitchAPI sends percentages, 1 if fractions
    tolerance: float  # on `scale`
    within_tolerance: int
    mean_diff: float | None
    max_abs_diff: float | None
    worst: list[RateRow]


def check_rate(session: Session, season_id: int, spec: RateSpec) -> RateCheck:
    codes = {spec.rate_code, spec.numerator.code, spec.denominator.code}
    ids = _metric_ids(session, codes)
    code_by_id = {metric_id: code for code, metric_id in ids.items()}
    stored: dict[int, dict[str, tuple[float | None, float | None]]] = defaultdict(dict)
    for appearance_id, metric_id, value, denominator in session.execute(
        select(StatValue.appearance_id, StatValue.metric_id, StatValue.value, StatValue.denominator)
        .join(Appearance, Appearance.id == StatValue.appearance_id)
        .join(Match, Match.id == Appearance.match_id)
        .where(Match.season_id == season_id, StatValue.metric_id.in_(ids.values()))
    ):
        stored[appearance_id][code_by_id[metric_id]] = (value, denominator)

    def part(values: dict[str, tuple[float | None, float | None]], p: RatePart) -> float | None:
        if p.code not in values:
            return None
        value, denominator = values[p.code]
        return value if p.field == "value" else denominator

    pairs: list[tuple[int, float, float, float]] = []  # appearance, sent, ratio, attempts
    rows_with_rate = no_attempts = missing = 0
    for appearance_id, values in stored.items():
        if spec.rate_code not in values:
            continue
        rows_with_rate += 1
        weighted, weight = values[spec.rate_code]
        numerator, attempts = part(values, spec.numerator), part(values, spec.denominator)
        if attempts == 0 or weight == 0:
            no_attempts += 1
            continue
        if weighted is None or weight is None or numerator is None or attempts is None:
            missing += 1
            continue
        pairs.append((appearance_id, weighted / weight, numerator / attempts, attempts))

    scale = 100 if any(sent > 1 for _, sent, _, _ in pairs) else 1
    tolerance = RATE_TOLERANCE_PCT * scale / 100
    diffs = [sent - ratio * scale for _, sent, ratio, _ in pairs]
    worst_pairs = sorted(zip(pairs, diffs, strict=True), key=lambda pd: (-abs(pd[1]), pd[0][0]))[
        :WORST_ROWS
    ]
    labels = _appearance_labels(session, [p[0] for p, _ in worst_pairs])
    return RateCheck(
        rate_code=spec.rate_code,
        formula=spec.formula,
        rows_with_rate=rows_with_rate,
        compared=len(pairs),
        no_attempts=no_attempts,
        missing=missing,
        scale=scale,
        tolerance=tolerance,
        within_tolerance=sum(abs(d) <= tolerance for d in diffs),
        mean_diff=statistics.fmean(diffs) if diffs else None,
        max_abs_diff=max((abs(d) for d in diffs), default=None),
        worst=[
            RateRow(*labels[appearance_id], sent, ratio * scale, attempts)
            for (appearance_id, sent, ratio, attempts), _ in worst_pairs
        ],
    )


def _appearance_labels(session: Session, appearance_ids: list[int]) -> dict[int, tuple[str, str]]:
    """appearance -> (player name, "date Home v Away")."""
    home, away = aliased(Team), aliased(Team)
    return {
        appearance_id: (name, f"{match_date} {h} v {a}")
        for appearance_id, name, match_date, h, a in session.execute(
            select(Appearance.id, Player.name, Match.match_date, home.name, away.name)
            .join(Player, Player.id == Appearance.player_id)
            .join(Match, Match.id == Appearance.match_id)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .where(Appearance.id.in_(appearance_ids))
        )
    }


# --- 3. role judgement calls: who actually starts in the disputed slots? -----------------------


@dataclass(frozen=True, slots=True)
class SlotSpec:
    formation: str | None  # None: the slot in every formation that has it
    slots: tuple[int, ...]


SLOT_SPECS: Final[tuple[SlotSpec, ...]] = (
    SlotSpec("4-2-3-1", (64, 66)),
    SlotSpec("3-4-2-1", (84, 86)),
    SlotSpec(None, (95,)),
)


@dataclass(frozen=True, slots=True)
class SlotPlayer:
    name: str
    team: str
    starts: int
    minutes: int
    breakdown: str  # starts per slot ("64 x12, 66 x3") or per formation for a shared slot
    primary_role: str | None  # the season's role after every start, None if not aggregated
    peer_role: str | None
    season_minutes: int | None


@dataclass(frozen=True, slots=True)
class SlotGroup:
    label: str
    mapped_role: str  # what FORMATION_ROLES says now
    starts: int
    players: list[SlotPlayer]  # most minutes in these slots first


def check_slots(session: Session, season_id: int, spec: SlotSpec) -> SlotGroup:
    stmt = (
        select(
            Appearance.player_id,
            Player.name,
            Team.name,
            Appearance.formation,
            Appearance.slot_code,
            Appearance.minutes,
        )
        .join(Match, Match.id == Appearance.match_id)
        .join(Player, Player.id == Appearance.player_id)
        .join(Team, Team.id == Appearance.team_id)
        .where(
            Match.season_id == season_id,
            Appearance.is_starter.is_(True),
            Appearance.slot_code.in_(spec.slots),
        )
    )
    if spec.formation is not None:
        stmt = stmt.where(Appearance.formation == spec.formation)

    starts: Counter[int] = Counter()
    minutes: Counter[int] = Counter()
    breakdown: dict[int, Counter[str]] = defaultdict(Counter)
    teams: dict[int, Counter[str]] = defaultdict(Counter)
    names: dict[int, str] = {}
    for player_id, name, team, formation, slot, played in session.execute(stmt):
        if spec.formation is None and formation not in FORMATION_ROLES:
            continue  # an unmapped formation: no role was assigned from this slot
        names[player_id] = name
        starts[player_id] += 1
        minutes[player_id] += played
        teams[player_id][team] += played
        breakdown[player_id][str(slot) if spec.formation else str(formation)] += 1

    scope_id = session.scalar(select(Scope.id).where(Scope.season_id == season_id))
    season: dict[int, tuple[str | None, str | None, int]] = {}
    if scope_id is not None and names:
        season = {
            player_id: (primary, peer, played)
            for player_id, primary, peer, played in session.execute(
                select(
                    PlayerScope.player_id,
                    PlayerScope.primary_role,
                    PlayerScope.peer_role,
                    PlayerScope.minutes,
                ).where(PlayerScope.scope_id == scope_id, PlayerScope.player_id.in_(names))
            )
        }

    players = [
        SlotPlayer(
            name=names[player_id],
            team=teams[player_id].most_common(1)[0][0],
            starts=starts[player_id],
            minutes=minutes[player_id],
            breakdown=", ".join(
                f"{key} x{count}" for key, count in sorted(breakdown[player_id].items())
            ),
            primary_role=season[player_id][0] if player_id in season else None,
            peer_role=season[player_id][1] if player_id in season else None,
            season_minutes=season[player_id][2] if player_id in season else None,
        )
        for player_id in sorted(names, key=lambda pid: (-minutes[pid], names[pid]))
    ]
    return SlotGroup(
        label=_slot_label(spec),
        mapped_role=_mapped_role(spec),
        starts=sum(starts.values()),
        players=players,
    )


def _slot_label(spec: SlotSpec) -> str:
    slots = "/".join(str(s) for s in spec.slots)
    return f"{spec.formation} slots {slots}" if spec.formation else f"slot {slots}, any formation"


def _mapped_role(spec: SlotSpec) -> str:
    formations = [spec.formation] if spec.formation else list(FORMATION_ROLES)
    roles = sorted(
        {
            FORMATION_ROLES[f][slot]
            for f in formations
            for slot in spec.slots
            if slot in FORMATION_ROLES.get(f, {})
        }
    )
    return "/".join(roles) or "unmapped"


# --- 4. xA vs xAG ----------------------------------------------------------------------------
#
# Two PitchAPI models of the same idea (/players expected_assists, advanced creation.xag).
# What matters for a template is whether they rank players differently, so the check
# compares per-match values, then each peer group's percentiles on both.

XA_EQUAL_TOLERANCE: Final = 0.005  # per match: equal to two decimals
MOVERS: Final = 3  # biggest non-zero percentile gaps shown per peer group


@dataclass(frozen=True, slots=True)
class XaMover:
    name: str
    team: str
    xa_per90: float
    xa_pct: float
    xag_per90: float
    xag_pct: float

    @property
    def gap(self) -> float:
        return self.xag_pct - self.xa_pct


@dataclass(frozen=True, slots=True)
class XaGroup:
    peer_role: str
    min_minutes: int
    ranked_on_both: int
    correlation: float | None  # of the per-90 values
    mean_abs_gap: float | None  # percentile points
    max_abs_gap: float | None
    movers: list[XaMover]


@dataclass(frozen=True, slots=True)
class XaCheck:
    with_both: int  # appearances with a known value for both
    equal: int  # of those, |xa - xag| <= XA_EQUAL_TOLERANCE
    xa_only: int
    xag_only: int
    total_xa: float  # over appearances with both
    total_xag: float
    correlation: float | None  # per match
    groups: list[XaGroup]


def check_xa_xag(session: Session, season_id: int) -> XaCheck:
    ids = _metric_ids(session, ("xa", "xag"))
    scope_id = _scope_id(session, season_id)
    xa, xag = aliased(StatValue), aliased(StatValue)
    per_match: list[tuple[float, float]] = []
    xa_only = xag_only = 0
    for xa_value, xag_value in session.execute(
        select(xa.value, xag.value)
        .select_from(Appearance)
        .join(Match, Match.id == Appearance.match_id)
        .outerjoin(xa, and_(xa.appearance_id == Appearance.id, xa.metric_id == ids["xa"]))
        .outerjoin(xag, and_(xag.appearance_id == Appearance.id, xag.metric_id == ids["xag"]))
        .where(Match.season_id == season_id)
    ):
        if xa_value is not None and xag_value is not None:
            per_match.append((xa_value, xag_value))
        elif xa_value is not None:
            xa_only += 1
        elif xag_value is not None:
            xag_only += 1

    groups = sorted(
        session.scalars(select(PeerGroup).where(PeerGroup.scope_id == scope_id)),
        key=lambda g: (PEER_ROLES.index(g.peer_role), g.min_minutes),
    )
    teams = _main_teams(session, scope_id)
    return XaCheck(
        with_both=len(per_match),
        equal=sum(abs(a - b) <= XA_EQUAL_TOLERANCE for a, b in per_match),
        xa_only=xa_only,
        xag_only=xag_only,
        total_xa=sum(a for a, _ in per_match),
        total_xag=sum(b for _, b in per_match),
        correlation=_correlation([a for a, _ in per_match], [b for _, b in per_match]),
        groups=[
            group for g in groups if (group := _xa_group(session, g, ids, teams)).ranked_on_both > 0
        ],
    )


def _xa_group(
    session: Session, group: PeerGroup, ids: dict[str, int], teams: dict[int, str]
) -> XaGroup:
    ranks: dict[int, dict[int, tuple[float, float]]] = defaultdict(dict)
    names: dict[int, str] = {}
    for player_id, name, metric_id, value, pct in session.execute(
        select(Percentile.player_id, Player.name, Percentile.metric_id, Percentile.value,
               Percentile.pct)
        .join(Player, Player.id == Percentile.player_id)
        .where(Percentile.peer_group_id == group.id, Percentile.metric_id.in_(ids.values()))
    ):  # fmt: skip
        ranks[player_id][metric_id] = (value, pct)
        names[player_id] = name
    both = [
        XaMover(
            names[pid],
            teams.get(pid, "?"),
            *ranks[pid][ids["xa"]],
            *ranks[pid][ids["xag"]],
        )
        for pid in sorted(ranks)
        if len(ranks[pid]) == 2
    ]
    gaps = [abs(m.gap) for m in both]
    return XaGroup(
        peer_role=group.peer_role,
        min_minutes=group.min_minutes,
        ranked_on_both=len(both),
        correlation=_correlation([m.xa_per90 for m in both], [m.xag_per90 for m in both]),
        mean_abs_gap=statistics.fmean(gaps) if gaps else None,
        max_abs_gap=max(gaps, default=None),
        movers=sorted((m for m in both if m.gap), key=lambda m: (-abs(m.gap), m.name))[:MOVERS],
    )


# --- 5. is /players' expected_assists really xA? -----------------------------------------------
#
# xAG credits a pass only when a shot followed (the shot's xG). Standard xA credits every
# completed pass by its chance of becoming an assist, shot or not. So in matches where a
# player created no chance, xAG must be 0, while real xA is usually still above 0. Checked
# against both chance counts: /players' chances_created (same endpoint as xa) and the
# advanced key_passes (same endpoint as xag).

CHANCE_COUNTS: Final[tuple[str, ...]] = ("chances_created", "key_passes")


@dataclass(frozen=True, slots=True)
class NoChanceRows:
    count_code: str  # the chance count that is 0 in these rows
    rows: int  # outfield appearances with xa, xag and this count known, count = 0
    xa_positive: int  # of those, xa > 0
    xag_positive: int  # of those, xag > 0 (expected ~0 if xag needs a shot)
    xa_share: float | None  # share of the season's xa total that comes from these rows


@dataclass(frozen=True, slots=True)
class XaDefinitionCheck:
    rows: int  # outfield appearances with xa, xag and both counts known
    total_xa: float
    no_chance: list[NoChanceRows]


def check_xa_definition(session: Session, season_id: int) -> XaDefinitionCheck:
    ids = _metric_ids(session, ("xa", "xag", *CHANCE_COUNTS))
    values = {code: aliased(StatValue) for code in ids}
    stmt = (
        select(*(values[code].value for code in ids))
        .select_from(Appearance)
        .join(Match, Match.id == Appearance.match_id)
        .where(Match.season_id == season_id)
    )
    for code, alias in values.items():
        stmt = stmt.join(
            alias, and_(alias.appearance_id == Appearance.id, alias.metric_id == ids[code])
        )
    codes = list(ids)
    rows = [
        dict(zip(codes, row, strict=True))
        for row in session.execute(stmt)
        if all(v is not None for v in row)
    ]
    total_xa = sum(r["xa"] for r in rows)
    no_chance = []
    for count_code in CHANCE_COUNTS:
        zero = [r for r in rows if r[count_code] == 0]
        no_chance.append(
            NoChanceRows(
                count_code=count_code,
                rows=len(zero),
                xa_positive=sum(r["xa"] > 0 for r in zero),
                xag_positive=sum(r["xag"] > 0 for r in zero),
                xa_share=sum(r["xa"] for r in zero) / total_xa if total_xa else None,
            )
        )
    return XaDefinitionCheck(rows=len(rows), total_xa=total_xa, no_chance=no_chance)


# --- slice 2e: what the striker template's xGOT and defensive actions contain -----------------


def _appearance_values(
    session: Session, season_id: int, codes: Sequence[str], *, outfield: bool = True
) -> list[dict[str, float | None]]:
    """One dict per appearance in the season: code -> stored value, None when there's no row
    or the stored value is NULL."""
    ids = _metric_ids(session, codes)
    values = {code: aliased(StatValue) for code in codes}
    stmt = (
        select(*(values[code].value for code in codes))
        .select_from(Appearance)
        .join(Match, Match.id == Appearance.match_id)
        .where(Match.season_id == season_id)
    )
    if outfield:
        stmt = stmt.where(Appearance.is_goalkeeper.is_(False))
    for code, alias in values.items():
        stmt = stmt.outerjoin(
            alias, and_(alias.appearance_id == Appearance.id, alias.metric_id == ids[code])
        )
    return [dict(zip(codes, row, strict=True)) for row in session.execute(stmt)]


@dataclass(frozen=True, slots=True)
class OneShotGoals:
    rows: int  # appearances whose only shot was a goal of this kind
    xgot_positive: int  # of those, xgot > 0 (a scored shot is on target)
    mean_xgot: float | None


@dataclass(frozen=True, slots=True)
class XgotPenaltyCheck:
    penalty: OneShotGoals  # the only shot was a scored penalty (npxg = 0, xg > 0)
    open_play: OneShotGoals  # control: the only shot was a scored non-penalty (npxg > 0)


def check_xgot_penalties(session: Session, season_id: int) -> XgotPenaltyCheck:
    """Does xGOT include penalties? Appearances whose single shot was a scored penalty have
    xgot > 0 only if it does. Scored non-penalty single shots are the control (~100%)."""
    rows = [
        r
        for r in _appearance_values(session, season_id, ("shots", "goals", "xg", "npxg", "xgot"))
        if r["shots"] == 1 and (r["goals"] or 0) >= 1 and r["xg"] is not None
    ]

    def summarise(kind: list[dict[str, float | None]]) -> OneShotGoals:
        xgots = [r["xgot"] or 0.0 for r in kind]
        return OneShotGoals(
            rows=len(kind),
            xgot_positive=sum(x > 0 for x in xgots),
            mean_xgot=statistics.fmean(xgots) if xgots else None,
        )

    penalty = [r for r in rows if not r["npxg"] and (r["xg"] or 0) > 0]
    open_play = [r for r in rows if (r["npxg"] or 0) > 0]
    return XgotPenaltyCheck(summarise(penalty), summarise(open_play))


DEFENSIVE_COMPONENTS: Final[tuple[str, ...]] = (
    "tackles", "interceptions", "clearances", "headed_clearances", "shot_blocks",
    "recoveries", "clearances_off_line", "last_man_tackles", "duels_won",
)  # fmt: skip
FORMULAS_SHOWN: Final = 3


@dataclass(frozen=True, slots=True)
class Formula:
    components: tuple[str, ...]
    exact: int  # appearances where defensive_actions equals the sum exactly
    mean_gap: float  # mean of defensive_actions minus the sum (+ = it counts more)


@dataclass(frozen=True, slots=True)
class DefensiveActionsCheck:
    rows: int  # outfield appearances with defensive_actions stored
    best: list[Formula]  # the sums that match most often, best first


def check_defensive_actions(session: Session, season_id: int) -> DefensiveActionsCheck:
    """Which counts add up to defensive_actions? Tries every combination of the candidate
    components (a missing count is 0) and reports how often each sum matches exactly."""
    codes = ("defensive_actions", *DEFENSIVE_COMPONENTS)
    rows = [
        r
        for r in _appearance_values(session, season_id, codes)
        if r["defensive_actions"] is not None
    ]
    targets = [r["defensive_actions"] or 0.0 for r in rows]
    columns = {c: [r[c] or 0.0 for r in rows] for c in DEFENSIVE_COMPONENTS}
    formulas = []
    for size in range(1, len(DEFENSIVE_COMPONENTS) + 1):
        for combo in itertools.combinations(DEFENSIVE_COMPONENTS, size):
            sums = [sum(parts) for parts in zip(*(columns[c] for c in combo), strict=True)]
            gaps = [t - s for t, s in zip(targets, sums, strict=True)]
            formulas.append(
                Formula(
                    components=combo,
                    exact=sum(abs(g) < 1e-9 for g in gaps),
                    mean_gap=statistics.fmean(gaps) if gaps else 0.0,
                )
            )
    formulas.sort(key=lambda f: (-f.exact, abs(f.mean_gap), len(f.components)))
    return DefensiveActionsCheck(rows=len(rows), best=formulas[:FORMULAS_SHOWN])
