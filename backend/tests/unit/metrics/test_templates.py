"""Position templates (slice 2e): the built-ins, the checks, and applying one to a player."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from scouting.db.models import PlayerSourceId, Scope
from scouting.db.queries import CoverageCell, CoverageRow, GroupInfo, PlayerSeason, player_season
from scouting.domain.positions import PEER_ROLES
from scouting.ingest.season import find_season
from scouting.metrics.aggregate import aggregate_season
from scouting.metrics.registry import BY_CODE
from scouting.metrics.templates import (
    SIZE_MAX,
    SIZE_MIN,
    Template,
    TemplateError,
    TemplateSection,
    apply_template,
    builtin_templates,
    check_coverage,
    check_template,
    missing_reason,
    parse_templates,
)

LEAGUE, SEASON = "l_FIXT01", "2025/2026"
TWELVE = (
    "npxg", "shots", "touches_opp_box", "xgot", "xa", "chances_created", "sca",
    "passes_completed", "miscontrols", "aerials_won", "aerials_won_count", "defensive_actions",
)  # fmt: skip


def custom(*codes: str, role: str = "ST", builtin: bool = False) -> Template:
    return Template("mine", role, "Mine", (TemplateSection("All", codes),), builtin)


def rules(template: Template) -> list[tuple[str, str]]:
    return [(f.severity, f.rule) for f in check_template(template)]


# --- the built-in templates ---------------------------------------------------------------------


def test_one_builtin_template_per_peer_role() -> None:
    templates = builtin_templates()
    assert sorted(templates) == sorted(PEER_ROLES)
    for role, template in templates.items():
        assert template.peer_role == role and template.id == f"builtin:{role}"
        assert template.builtin and template.label


def test_builtin_templates_pass_every_check() -> None:
    for template in builtin_templates().values():
        assert check_template(template) == [], template.peer_role


def test_builtin_templates_hold_the_users_picks() -> None:
    templates = builtin_templates()
    assert [len(templates[r].codes) for r in PEER_ROLES] == [8, 12, 12, 12, 12, 12, 12, 12]
    assert templates["ST"].codes == list(TWELVE)
    assert "dribbles_completed_count" in templates["W"].codes  # volume next to the rate
    assert not any(BY_CODE[c].outcome for t in templates.values() for c in t.codes)


# --- loading ------------------------------------------------------------------------------------


def test_parse_templates() -> None:
    text = '[ST]\nlabel = "Mine"\n[[ST.sections]]\nname = "Shooting"\nmetrics = ["npxg"]\n'
    template = parse_templates(text, builtin=False)["ST"]
    assert template == Template(
        "ST", "ST", "Mine", (TemplateSection("Shooting", ("npxg",)),), False
    )


@pytest.mark.parametrize(
    "text",
    [
        "[ST\nlabel = 1",  # not TOML
        '[ST]\n[[ST.sections]]\nname = "A"\nmetrics = []\n',  # no label
        '[ST]\nlabel = "x"\ncolour = "red"\nsections = []\n',  # unknown key
        '[ST]\nlabel = "x"\n[[ST.sections]]\nmetrics = ["npxg"]\n',  # section without a name
    ],
)
def test_parse_templates_rejects_bad_shapes(text: str) -> None:
    with pytest.raises(TemplateError):
        parse_templates(text, builtin=False)


# --- checks -------------------------------------------------------------------------------------


def test_a_good_custom_template_has_no_findings() -> None:
    assert check_template(custom(*TWELVE)) == []


def test_errors() -> None:
    assert ("error", "unknown_metric") in rules(custom(*TWELVE[:11], "nope"))
    assert ("error", "duplicate") in rules(custom(*TWELVE, "npxg"))
    assert ("error", "wrong_position") in rules(custom(*TWELVE[:11], "saves"))  # keepers only
    assert ("error", "wrong_position") in rules(custom("shots", role="GK"))  # outfield only
    assert ("error", "unknown_role") in rules(custom(*TWELVE, role="LW"))
    assert ("error", "empty") in rules(Template("x", "ST", "x", (), False))
    assert ("error", "empty") in rules(
        Template("x", "ST", "x", (TemplateSection("A", TWELVE), TemplateSection("B", ())), False)
    )


@pytest.mark.parametrize("builtin", [False, True])
def test_judgement_calls_warn_for_custom_and_fail_builtin(builtin: bool) -> None:
    severity = "error" if builtin else "warning"
    assert (severity, "outcome") in rules(custom(*TWELVE[:11], "goals", builtin=builtin))
    assert (severity, "near_duplicate") in rules(
        custom(*TWELVE[:11], "key_passes", builtin=builtin)
    )
    assert (severity, "size") in rules(custom(*TWELVE[: SIZE_MIN - 1], builtin=builtin))
    too_many = (*TWELVE, "tackles", "recoveries", "dispossessed")
    assert len(too_many) > SIZE_MAX
    assert (severity, "size") in rules(custom(*too_many, builtin=builtin))


def coverage(code: str, ranked: int) -> CoverageRow:
    return CoverageRow(
        code, code, "count", "pitchapi_players", False, "higher", "outfield", "per90", None,
        {"ST": CoverageCell(ranked, 0.0, 1.0, 2.0)},
    )  # fmt: skip


def test_check_coverage_warns_when_under_half_are_ranked() -> None:
    groups = [GroupInfo("ST", 33, 855)]
    rows = [coverage(code, 33) for code in TWELVE[:-1]] + [coverage("defensive_actions", 16)]
    findings = check_coverage(custom(*TWELVE), groups, rows)
    assert [(f.rule, f.code) for f in findings] == [("low_coverage", "defensive_actions")]
    assert "only 16 of 33 STs ranked" in findings[0].message
    rows[-1] = coverage("defensive_actions", 17)  # 17 of 33 is at least half
    assert check_coverage(custom(*TWELVE), groups, rows) == []
    assert check_coverage(custom(*TWELVE), [], rows)[0].message == "no ST peer group this season"


# --- applying a template ------------------------------------------------------------------------


def season_of(engine: Engine, external_id: str) -> PlayerSeason:
    with Session(engine) as session, session.begin():
        season_id = find_season(session, LEAGUE, SEASON).id
        aggregate_season(session, season_id)
    with Session(engine) as session:
        scope_id = session.scalars(select(Scope.id)).one()
        player_id = session.scalars(
            select(PlayerSourceId.player_id).where(PlayerSourceId.external_id == external_id)
        ).one()
        detail = player_season(session, scope_id, player_id)
    assert detail is not None
    return detail


def test_apply_template_to_a_striker(synced_season: Engine) -> None:
    detail = season_of(synced_season, "p_AST01")
    assert detail.peer_role == "ST" and detail.group is not None
    view = apply_template(detail, builtin_templates()["ST"])
    assert view.same_group and view.group == detail.group
    assert [name for name, _ in view.sections] == ["Shooting", "Link-up", "Aerial", "Pressing"]
    rows = {row.code: row for _, section in view.sections for row in section}
    assert list(rows) == list(TWELVE)
    npxg = rows["npxg"]
    assert npxg.is_model_output and npxg.basis == "per90" and npxg.line is not None
    assert npxg.pct is not None and npxg.reason is None and npxg.ranked_among == 2
    passes = rows["passes_completed"]
    assert passes.basis == "rate" and passes.pct is None and passes.line is not None
    assert passes.reason == f"needs 100 attempts (has {int(passes.line.denominator or 0)})"
    assert rows["miscontrols"].direction == "lower"


def test_apply_template_outside_his_group_says_so(synced_season: Engine) -> None:
    detail = season_of(synced_season, "p_AST01")
    view = apply_template(detail, builtin_templates()["W"])
    assert not view.same_group and view.group is not None and view.group.peer_role == "ST"


def test_apply_template_to_an_unranked_player(synced_season: Engine) -> None:
    detail = season_of(synced_season, "p_BGK03")  # a sub keeper, below the minimum
    assert detail.group is None
    view = apply_template(detail, builtin_templates()["GK"])
    reasons = {row.reason for _, section in view.sections for row in section}
    assert reasons == {f"not ranked: below {detail.group_min_minutes} min"}


def test_missing_reason(synced_season: Engine) -> None:
    detail = season_of(synced_season, "p_AST01")
    assert detail.group is not None
    assert missing_reason(None, detail) == "no data"
    line = next(s for s in detail.stats if s.code == "npxg")
    short = replace(line, minutes=detail.group.min_minutes - 1, pct=None)
    assert missing_reason(short, detail) == (
        f"{detail.group.min_minutes - 1} min with data (needs {detail.group.min_minutes})"
    )
    assert missing_reason(line, replace(detail, group=None, peer_role=None)) == "no peer role"


def test_apply_template_refuses_unknown_metrics(synced_season: Engine) -> None:
    detail = season_of(synced_season, "p_AST01")
    with pytest.raises(TemplateError):
        apply_template(detail, custom("nope"))
