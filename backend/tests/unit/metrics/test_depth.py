"""Midfield depth for the 6/8 split (slice 2d-2, D-017).

The pure functions are tested on small hand-made rows with values worked out by hand; the
report on the three-match fixture season, with roles and names edited so it has central
midfielders and anchors.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from scouting.db.models import (
    PEER_METHODS,
    Appearance,
    PeerGroup,
    Percentile,
    Player,
    PlayerScope,
    PlayerSourceId,
)
from scouting.ingest.season import find_season
from scouting.metrics import depth
from scouting.metrics.aggregate import aggregate_season
from scouting.metrics.depth import (
    AppearanceRow,
    Calibration,
    Cut,
    DepthReportError,
    MatchDepth,
    Method,
    PlayerLine,
    SeasonDepth,
    best_cut,
    calibrate,
    classify,
    classify_central_mids,
    histogram,
    is_borderline,
    label_for,
    lone_pivot_labels,
    match_depths,
    season_depths,
)

LEAGUE, SEASON = "l_FIXT01", "2025/2026"


def row(
    player: int, role: str | None, depth_: float | None, *, match: int = 1, team: int = 1,
    starter: bool = True, keeper: bool = False, minutes: int = 90,
) -> AppearanceRow:  # fmt: skip
    return AppearanceRow(match, team, player, starter, keeper, role, minutes, depth_)


# One team in one match: keeper excluded from the outfield reference; outfield mean is
# (0.30 + 0.34 + 0.45 + 0.44 + 0.55 + 0.65 + 0.70) / 7 = 0.49; centre-back mean 0.32.
SIDE = [
    row(1, "GK", 0.10, keeper=True),
    row(2, "CB", 0.30),
    row(3, "CB", 0.34),
    row(4, "FB", 0.45),
    row(5, "DM", 0.44),
    row(6, "CM", 0.55),
    row(7, "W", 0.65),
    row(8, "ST", 0.70),
    row(9, None, 0.20, starter=False),  # a sub: never part of the reference or the mids
]


# --- per match ------------------------------------------------------------------------------


def test_relative_to_the_outfield_players() -> None:
    result = {m.player_id: m for m in match_depths(SIDE, "outfield")}
    assert set(result) == {5, 6}  # the central midfielders only
    assert result[5].relative == pytest.approx(0.44 - 0.49)
    assert result[6].relative == pytest.approx(0.55 - 0.49)
    assert (result[5].deepest, result[6].deepest) == (True, False)
    # Deeper-partner gap: his depth minus his deepest partner's (+ = a deeper partner).
    assert result[5].partner_gap == pytest.approx(-0.11)
    assert result[6].partner_gap == pytest.approx(0.11)


def test_partner_gap_uses_the_deepest_partner() -> None:
    # A midfield three: the 8s' gap is to the 6, not to each other.
    three = [*SIDE, row(10, "CM", 0.60)]
    result = {m.player_id: m.partner_gap for m in match_depths(three, "outfield")}
    assert result == {
        5: pytest.approx(0.44 - 0.55),
        6: pytest.approx(0.55 - 0.44),
        10: pytest.approx(0.60 - 0.44),
    }


def test_relative_to_the_centre_backs() -> None:
    result = {m.player_id: m.relative for m in match_depths(SIDE, "centre_backs")}
    assert result == {5: pytest.approx(0.12), 6: pytest.approx(0.23)}


def test_no_reference_no_rows() -> None:
    no_centre_backs = [r for r in SIDE if r.role != "CB"]
    assert match_depths(no_centre_backs, "centre_backs") == []
    assert len(match_depths(no_centre_backs, "outfield")) == 2


def test_a_lone_midfielder_has_no_partner_to_compare() -> None:
    alone = [r for r in SIDE if r.player_id != 6]
    (only,) = match_depths(alone, "outfield")
    assert only.player_id == 5 and only.deepest is None and only.partner_gap is None


def test_midfielders_without_a_heatmap_are_left_out() -> None:
    no_heatmap = [row(6, "CM", None) if r.player_id == 6 else r for r in SIDE]
    result = match_depths(no_heatmap, "outfield")
    assert [m.player_id for m in result] == [5]
    assert result[0].deepest is None  # no second midfielder with a depth to compare


def test_teams_are_kept_apart() -> None:
    other = [
        row(101, "GK", 0.10, team=2, keeper=True),
        row(102, "CB", 0.30, team=2),
        row(103, "CB", 0.34, team=2),
        row(104, "FB", 0.45, team=2),
        row(105, "DM", 0.20, team=2),
        row(106, "CM", 0.55, team=2),
        row(107, "W", 0.65, team=2),
        row(108, "ST", 0.70, team=2),
    ]
    result = {m.player_id: m.relative for m in match_depths(SIDE + other, "outfield")}
    assert result[5] == pytest.approx(0.44 - 0.49)  # unchanged by the other team
    other_mean = (0.30 + 0.34 + 0.45 + 0.20 + 0.55 + 0.65 + 0.70) / 7
    assert result[105] == pytest.approx(0.20 - other_mean)


# --- per season -----------------------------------------------------------------------------


def test_season_is_minutes_weighted() -> None:
    starts = [
        MatchDepth(5, 1, 90, -0.10, True, -0.02),
        MatchDepth(5, 2, 30, +0.10, False, +0.06),
        MatchDepth(5, 3, 60, -0.04, None, None),  # alone in midfield: not comparable
    ]
    (season,) = season_depths(starts).values()
    assert season.relative == pytest.approx((-9 + 3 - 2.4) / 180)
    assert (season.starts, season.minutes, season.comparable, season.deepest) == (3, 180, 2, 1)
    assert season.deepest_share == pytest.approx(0.5)
    # Over the starts beside a partner only: (-0.02 x 90 + 0.06 x 30) / 120.
    assert season.partner_gap == pytest.approx(0.0)


def test_season_without_a_partner_has_no_gap() -> None:
    (season,) = season_depths([MatchDepth(5, 1, 90, -0.1, None, None)]).values()
    assert (season.comparable, season.partner_gap, season.deepest_share) == (0, None, None)


@pytest.mark.parametrize(("starts", "enough"), [(4, False), (5, True), (6, True)])
def test_enough_starts(starts: int, enough: bool) -> None:
    assert SeasonDepth(1, starts, 90 * starts, -0.1, 0, 0).enough_starts is enough


# --- the cut --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchors", "cut", "wrong"),
    [
        # Clean split: the middle of the gap between the highest 6 and the lowest 8.
        ([("a", "DM", -0.06), ("b", "DM", -0.04), ("c", "CM", 0.02), ("d", "CM", 0.05)],
         -0.01, ()),
        # One 6 plays higher than an 8: the cut that gets the most anchors right.
        ([("a", "DM", -0.06), ("b", "DM", 0.03), ("c", "CM", 0.00), ("d", "CM", 0.05),
          ("e", "CM", 0.06)], -0.03, ("b",)),
        # Equal errors: the widest gap wins (0.02 to 0.08 over -0.04 to -0.02).
        ([("a", "DM", -0.04), ("b", "CM", -0.02), ("c", "DM", 0.02), ("d", "CM", 0.08)],
         0.05, ("b",)),
    ],
)  # fmt: skip
def test_best_cut(anchors: list[tuple[str, Any, float]], cut: float, wrong: tuple[str]) -> None:
    result = best_cut(anchors)
    assert result is not None
    assert result.value == pytest.approx(cut)
    assert result.wrong == wrong


def test_best_cut_needs_both_kinds() -> None:
    assert best_cut([("a", "DM", -0.1), ("b", "DM", 0.1)]) is None
    assert best_cut([]) is None


def test_best_cut_margin() -> None:
    result = best_cut([("a", "DM", -0.06), ("c", "CM", 0.02)])
    assert result == Cut(pytest.approx(-0.02), (), pytest.approx(0.04))  # type: ignore[arg-type]


def test_label_for() -> None:
    assert (label_for(-0.011, -0.01), label_for(-0.01, -0.01), label_for(0.2, -0.01)) == (
        "DM", "CM", "CM",
    )  # fmt: skip


def test_histogram() -> None:
    bins = histogram([-0.015, -0.005, 0.0, 0.004, 0.019], 0.01)
    assert [count for _, count in bins] == [1, 1, 2, 1]
    assert [start for start, _ in bins] == pytest.approx([-0.02, -0.01, 0.0, 0.01])
    assert histogram([], 0.01) == []


# --- what the report derives from a calibration ---------------------------------------------


def line(
    player: int, relative: float, starts: int = 10, deepest: int = 5, gap: float | None = None
) -> PlayerLine:
    return PlayerLine(
        player, f"P{player}", "T", SeasonDepth(player, starts, 900, relative, 10, deepest, gap)
    )


def calibration(group: list[PlayerLine], cut: float = 0.13) -> Calibration:
    return Calibration(855, group, [], cut, None)


def test_sizes_nearest_and_disagreements() -> None:
    group = [
        line(1, 0.05, deepest=9),
        line(2, 0.095, deepest=2),  # a 6 by depth, but rarely the deepest: listed
        line(3, 0.125),
        line(4, 0.14, deepest=8),  # an 8 by depth, but usually the deepest: listed
        line(5, 0.19, deepest=1),
        line(6, 0.02, starts=3),  # too few starts: formation fallback
    ]
    c = calibration(group)
    assert c.sizes() == (3, 2, 1)  # 0.05, 0.095, 0.125 are below the 0.13 cut
    assert [p.player_id for p in c.nearest(2)] == [3, 4]
    assert [p.player_id for p in c.disagreements()] == [2, 4]


def test_deeper_partner_rule() -> None:
    group = [
        line(1, 0.10, gap=0.05),  # a 6 whose partner plays 5 m deeper: an 8 beside a 6
        line(2, 0.11, gap=0.02),  # a 6 with a slightly deeper partner (2.1 m)
        line(3, 0.09, gap=-0.03),  # a 6 who is the deeper one
        line(4, 0.12, gap=None),  # a 6 never beside a partner: the rule can't apply
        line(5, 0.20, gap=0.08),  # already an 8: nothing to flip
        line(6, 0.10, gap=0.09, deepest=0),
    ]
    group[5] = PlayerLine(6, "P6", "T", SeasonDepth(6, 10, 900, 0.10, 4, 0, 0.09))  # 4 beside
    c = calibration(group)
    assert [p.player_id for p in c.sixes()] == [6, 1, 2, 3, 4]  # largest gap first
    assert [p.player_id for p in c.partner_flips(0.03)] == [1]  # P6: too few starts beside
    assert [p.player_id for p in c.partner_flips(0.02)] == [1, 2]
    assert c.partner_flips(0.06) == []
    assert c.sizes() == (5, 1, 0)
    assert c.sizes(0.02) == (3, 3, 0)


def test_no_classified_players() -> None:
    c = calibration([line(1, -0.1, starts=2)])
    assert c.sizes() == (0, 0, 1)
    assert c.nearest(5) == [] and c.disagreements() == [] and c.sixes() == []


# --- the report on the fixture season -------------------------------------------------------
#
# The fixture's 4-2-3-1 side has a left-back (depth 0.3125), a striker (0.84375) and a keeper
# (0.09375). Re-labelled here as a DM (the left-back) and a CM (the striker), with the keeper
# standing in as the side's only centre-back: the "DM" plays 0.21875 in front of him (23.0 m),
# the "CM" 0.75 (78.75 m); his deepest partner is 0.53125 deeper / higher. Against the outfield
# players (the two of them, mean 0.578125) they sit at -0.265625 and +0.265625.


def fixture_midfield(engine: Engine, *, centre_backs: bool = True) -> None:
    with Session(engine) as session, session.begin():

        def ids(*players: str) -> list[int]:
            return list(session.scalars(
                select(PlayerSourceId.player_id).where(PlayerSourceId.external_id.in_(players))
            ))  # fmt: skip

        roles = [(("p_ALB01", "p_CLB01"), "DM"), (("p_AST01", "p_CST01"), "CM")]
        if centre_backs:
            roles.append((("p_AGK01", "p_CGK01"), "CB"))
        for players, role in roles:
            session.execute(
                update(Appearance)
                .where(Appearance.player_id.in_(ids(*players)), Appearance.is_starter.is_(True))
                .values(role=role)
            )
        # The fixture reuses names per team; give some players their own, for anchor lookups.
        for player, name in (
            ("p_CLB01", "Cleo Deep"),
            ("p_CST01", "Cyd High"),
            ("p_BGK02", "Bo Keeper"),
            ("p_BGK03", "Hugo Bench"),
        ):
            session.execute(
                update(Player)
                .where(Player.id.in_(ids(player)))
                .values(name=name, search_name=name.lower())
            )
        aggregate_season(session, find_season(session, LEAGUE, SEASON).id)


@pytest.fixture
def fixture_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(depth, "MIN_DEPTH_STARTS", 1)  # the fixture has 1-2 starts each
    monkeypatch.setattr(depth, "MIN_PARTNER_STARTS", 1)
    monkeypatch.setattr(
        depth,
        "ANCHORS",
        (("cleo deep", "DM"), ("cyd high", "CM"), ("bea fullback", "DM"),
         ("bo keeper", "DM"), ("nobody here", "CM")),
    )  # fmt: skip


def run(engine: Engine, cut: float = 0.5) -> Calibration:
    with Session(engine) as session:
        return calibrate(session, find_season(session, LEAGUE, SEASON).id, cut=cut)


def test_calibration_on_the_fixture_season(synced_season: Engine, fixture_anchors: None) -> None:
    fixture_midfield(synced_season)
    result = run(synced_season)
    assert len(result.group) == 4  # A's and C's left-back and striker, now central mids
    by_name = {p.name: p for p in result.group}
    cleo, cyd = by_name["Cleo Deep"].depth, by_name["Cyd High"].depth
    assert cleo is not None and cyd is not None
    assert [cleo.relative, cyd.relative] == pytest.approx([0.21875, 0.75])
    assert [cleo.partner_gap, cyd.partner_gap] == pytest.approx([-0.53125, 0.53125])
    assert by_name["Cleo Deep"].outfield == pytest.approx(-0.265625)  # the second opinion
    assert cleo.deepest_share == 1.0  # deeper than his partner every time
    assert [p.name for p in result.group][:2] == ["Bea Fullbäck", "Cleo Deep"]  # deepest first
    status = {a.query: a.status for a in result.anchors}
    assert status["cleo deep"] == status["cyd high"] == status["bea fullback"] == "ok"
    assert status["bo keeper"] == "in the GK group (mostly GK)"
    assert status["nobody here"] == "not found in this season"
    assert result.sizes() == (2, 2, 0)
    assert result.suggested is not None  # midway between 0.21875 and 0.75
    assert result.suggested.value == pytest.approx(0.484375) and result.suggested.wrong == ()


def test_default_cut_is_14_metres(synced_season: Engine, fixture_anchors: None) -> None:
    fixture_midfield(synced_season)
    with Session(synced_season) as session:
        result = calibrate(session, find_season(session, LEAGUE, SEASON).id)
    assert result.cut == pytest.approx(14 / 105)
    assert result.sizes() == (0, 4, 0)  # 23 m and 78.75 m in front: all 8s


def test_deeper_partner_flips_on_the_fixture(synced_season: Engine, fixture_anchors: None) -> None:
    fixture_midfield(synced_season)
    result = run(synced_season, cut=0.9)  # everyone a 6 by depth
    flips = result.partner_flips(2 / 105)
    assert sorted(p.name for p in flips) == ["Cal Striker", "Cyd High"]  # 55.8 m higher
    assert result.sizes(2 / 105) == (2, 2, 0)


def test_without_centre_backs_nobody_has_a_reference(
    synced_season: Engine, fixture_anchors: None
) -> None:
    fixture_midfield(synced_season, centre_backs=False)
    result = run(synced_season)
    assert result.suggested is None and result.sizes() == (0, 0, 4)
    assert {a.status for a in result.anchors if a.query == "cleo deep"} == {
        "only 0 central-mid starts with a heatmap"
    }


def test_anchor_lookups(synced_season: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture_midfield(synced_season)
    monkeypatch.setattr(depth, "ANCHORS", (("gus ten", "CM"), ("hugo bench", "DM")))
    status = {a.query: a.status for a in run(synced_season).anchors}
    assert status["gus ten"] == "ambiguous (2 players match)"  # A's and B's
    assert status["hugo bench"] == "below the minimum (10 < 45 min)"  # two 5' cameos


def test_report_needs_aggregates(synced_season: Engine) -> None:
    with pytest.raises(DepthReportError, match="aggregate"):
        run(synced_season)


def test_report_needs_heatmap_depths(synced_season: Engine) -> None:
    fixture_midfield(synced_season)
    with Session(synced_season) as session, session.begin():
        session.execute(update(Appearance).values(depth=None))
    with pytest.raises(DepthReportError, match="sync season"):
        run(synced_season)


# --- the rule (slice 2d-3, D-018) -------------------------------------------------------------

CUT, GAP = 14 / 105, 2 / 105


def season(
    relative: float | None, *, starts: int = 10, comparable: int = 10, gap: float | None = None
) -> SeasonDepth:
    return SeasonDepth(1, starts, 90 * starts, relative, comparable, 0, gap)


@pytest.mark.parametrize(
    ("depth_", "call"),
    [
        (season(CUT), ("CM", "depth")),  # exactly on the cut: an 8
        (season(CUT - 1e-9), ("DM", "depth")),
        (season(0.20, gap=0.30), ("CM", "depth")),  # an 8 by depth: the partner rule isn't needed
        (season(0.10, gap=GAP), ("CM", "partner")),  # partner exactly G deeper: an 8
        (season(0.10, gap=GAP - 1e-9), ("DM", "depth")),
        (season(0.10, gap=None), ("DM", "depth")),  # never beside a partner
        (season(0.10, gap=0.30, comparable=4), ("DM", "depth")),  # too few starts beside one
        (season(0.10, starts=4), None),  # too few starts: the formation decides
        (season(None), None),
        (None, None),
    ],
)  # fmt: skip
def test_classify(depth_: SeasonDepth | None, call: tuple[str, str] | None) -> None:
    assert classify(depth_) == call


def test_classify_reads_the_agreed_rule() -> None:
    agreed = [depth.DEPTH_CUT, depth.PARTNER_GAP]
    assert agreed == pytest.approx([CUT, GAP])
    assert (depth.MIN_DEPTH_STARTS, depth.MIN_PARTNER_STARTS) == (5, 5)
    assert classify(season(0.10), cut=0.05) == ("CM", "depth")  # a trial cut


def test_methods_match_the_schema() -> None:
    assert set(get_args(Method)) == set(PEER_METHODS)


def test_is_borderline() -> None:
    assert is_borderline(CUT + 0.9 / 105) and is_borderline(CUT - 0.9 / 105)
    assert not is_borderline(CUT + 1.1 / 105) and not is_borderline(CUT - 1.1 / 105)


def test_formation_fallback_is_a_6_only_as_a_single_pivot() -> None:
    rows = [
        # Match 1, team 1: a 4-1-4-1, player 5 the lone DM.
        row(5, "DM", None), row(6, "CM", None), row(7, "CM", None),
        # Match 2, team 1: a 4-2-3-1 double pivot (5 and 6), 30 minutes for 5.
        row(5, "DM", None, match=2, minutes=30), row(6, "DM", None, match=2),
        # Match 3, team 1: player 8 the lone DM for 45 minutes, but a sub isn't a start.
        row(8, "DM", None, match=3, minutes=45), row(9, "DM", None, match=3, starter=False),
        row(8, "CM", None, match=4, minutes=45),
    ]  # fmt: skip
    assert lone_pivot_labels(rows) == {
        5: "DM",  # 90 of 120 minutes as the lone pivot
        6: "CM",  # a CM, then half of a double pivot
        7: "CM",
        8: "CM",  # 45 of 90 minutes: not more than half
    }


def test_classify_central_mids() -> None:
    three = [*SIDE, row(10, "CM", 0.60)]  # CB line 0.32: 5 at +0.12, 6 at +0.23, 10 at +0.28
    calls = classify_central_mids(three, [5, 6, 10, 42])
    assert {pid: (c.peer_role, c.method) for pid, c in calls.items()} == {
        # One start each (fewer than 5): the formation decides. 5 is the lone DM.
        5: ("DM", "formation"),
        6: ("CM", "formation"),
        10: ("CM", "formation"),
        42: ("CM", "formation"),  # no central-mid start at all
    }
    assert calls[5].depth is not None and calls[5].depth.relative == pytest.approx(0.12)
    assert calls[42].depth is None


def test_classify_central_mids_by_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(depth, "MIN_DEPTH_STARTS", 1)
    monkeypatch.setattr(depth, "MIN_PARTNER_STARTS", 1)
    three = [*SIDE, row(10, "CM", 0.60)]
    calls = classify_central_mids(three, [5, 6, 10])
    # 5 is 12.6 m in front of the CBs: a 6. 6 (24.2 m) and 10 (29.4 m): 8s by depth.
    assert {pid: (c.peer_role, c.method) for pid, c in calls.items()} == {
        5: ("DM", "depth"), 6: ("CM", "depth"), 10: ("CM", "depth"),
    }  # fmt: skip
    # With the cut at 30 m all are 6s by depth; 6 and 10 have a partner 11.55 m / 16.8 m deeper.
    monkeypatch.setattr(depth, "DEPTH_CUT", 30 / 105)
    calls = classify_central_mids(three, [5, 6, 10])
    assert {pid: (c.peer_role, c.method) for pid, c in calls.items()} == {
        5: ("DM", "depth"), 6: ("CM", "partner"), 10: ("CM", "partner"),
    }  # fmt: skip


def test_stored_counts_and_mismatches() -> None:
    def stored(
        player: int, relative: float, role: str, method: str, starts: int = 10
    ) -> PlayerLine:
        return PlayerLine(
            player, f"P{player}", "T", SeasonDepth(player, starts, 900, relative, 10, 5, None),
            stored=role, method=method,
        )  # fmt: skip

    c = calibration(
        [
            stored(1, 0.10, "DM", "depth"),
            stored(2, 0.20, "CM", "depth"),
            stored(3, 0.20, "DM", "depth"),  # an 8 by the rule: stale
            stored(4, 0.10, "CM", "formation", starts=3),
            stored(5, 0.10, "DM", "depth", starts=3),  # the formation decides now: stale
        ],
        cut=CUT,
    )
    roles, methods = c.stored_counts()
    assert (roles["DM"], roles["CM"]) == (3, 2)
    assert (methods["depth"], methods["formation"]) == (4, 1)
    assert [p.player_id for p in c.mismatches()] == [3, 5]


# --- aggregation stores the split (fixture season) ------------------------------------------


def stored_split(engine: Engine) -> dict[str, tuple[Any, ...]]:
    with Session(engine) as session:
        return {
            name: tuple(rest)
            for name, *rest in session.execute(
                select(
                    Player.name, PlayerScope.peer_role, PlayerScope.peer_method,
                    PlayerScope.depth, PlayerScope.depth_starts, PlayerScope.partner_gap,
                    PlayerScope.partner_starts,
                )
                .join(PlayerScope, PlayerScope.player_id == Player.id)
                .where(PlayerScope.peer_method.is_not(None))
            )
        }  # fmt: skip


def test_aggregation_splits_central_mids_by_depth(
    synced_season: Engine, fixture_anchors: None
) -> None:
    fixture_midfield(synced_season)
    split = stored_split(synced_season)
    assert set(split) == {"Bea Fullbäck", "Cal Striker", "Cleo Deep", "Cyd High"}
    role, method, depth_, starts, gap, beside = split["Cleo Deep"]
    assert (role, method, starts, beside) == ("CM", "depth", 2, 2)  # 23 m: above the 14 m cut
    assert [depth_, gap] == pytest.approx([0.21875, -0.53125])
    with Session(synced_season) as session:  # nobody else is touched
        assert session.scalar(
            select(func.count()).select_from(PlayerScope).where(
                PlayerScope.peer_method.is_(None), PlayerScope.peer_role.in_(("DM", "CM"))
            )
        ) == 0  # fmt: skip


def test_aggregation_applies_the_partner_rule(
    synced_season: Engine, fixture_anchors: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(depth, "DEPTH_CUT", 0.9)  # everyone a 6 by depth
    fixture_midfield(synced_season)
    split = stored_split(synced_season)
    assert {name: values[:2] for name, values in split.items()} == {
        "Bea Fullbäck": ("DM", "depth"),
        "Cleo Deep": ("DM", "depth"),
        "Cal Striker": ("CM", "partner"),  # his partner 55.8 m deeper
        "Cyd High": ("CM", "partner"),
    }
    with Session(synced_season) as session:
        sizes = {
            role: size
            for role, size in session.execute(select(PeerGroup.peer_role, PeerGroup.size))
        }
        dm_rows = session.scalar(
            select(func.count())
            .select_from(Percentile)
            .join(PeerGroup, PeerGroup.id == Percentile.peer_group_id)
            .where(PeerGroup.peer_role == "DM")
        )
    assert sizes["DM"] == 2 and sizes["CM"] == 2  # a DM peer group with its own percentiles
    assert dm_rows


def test_aggregation_falls_back_to_the_formation(synced_season: Engine) -> None:
    fixture_midfield(synced_season)  # 1-2 starts each, fewer than 5
    split = stored_split(synced_season)
    assert {values[1] for values in split.values()} == {"formation"}
    # The fixture's lineups have no other DM: the relabelled left-backs are lone pivots (6s),
    # the relabelled strikers CM-role starters (8s).
    assert {name: values[0] for name, values in split.items()} == {
        "Bea Fullbäck": "DM", "Cleo Deep": "DM", "Cal Striker": "CM", "Cyd High": "CM",
    }  # fmt: skip
    assert split["Cleo Deep"][2] == pytest.approx(0.21875)  # depth still stored for display
