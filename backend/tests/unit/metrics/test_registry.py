"""Consistency checks on the metric registry."""

from __future__ import annotations

from scouting.metrics.registry import (
    BY_CODE,
    KEEPER_ONLY_KEYS,
    KNOWN_PLAYER_KEYS,
    REGISTRY,
)


def test_codes_are_unique() -> None:
    assert len(BY_CODE) == len(REGISTRY)


def test_each_source_key_is_used_once_per_source() -> None:
    keys = [(m.source, m.source_key) for m in REGISTRY]
    assert len(set(keys)) == len(keys)


def test_fractions_and_only_fractions_use_fraction_policy() -> None:
    for metric in REGISTRY:
        assert metric.is_fraction == (metric.absence_policy == "fraction_zero_attempts"), (
            metric.code
        )
        assert metric.aggregation == ("rate_from_sums" if metric.is_fraction else "sum")


def test_players_keys_were_seen_in_the_spike() -> None:
    """Registry keys must come from real responses, never be invented."""
    for metric in REGISTRY:
        if metric.source == "pitchapi_players":
            assert metric.source_key in KNOWN_PLAYER_KEYS, metric.code


def test_advanced_keys_are_group_dot_field() -> None:
    groups = {"possession_value", "passing", "carrying", "creation", "defending", "goalkeeping"}
    for metric in REGISTRY:
        if metric.source == "pitchapi_advanced":
            group, _, name = metric.source_key.partition(".")
            assert group in groups and name, metric.code


def test_keeper_metrics_use_keeper_only_keys() -> None:
    for metric in REGISTRY:
        if metric.applies_to == "gk":
            assert metric.source_key in KEEPER_ONLY_KEYS, metric.code


def test_model_outputs_are_flagged() -> None:
    models = {m.code for m in REGISTRY if m.is_model_output}
    assert {"xg", "npxg", "xa", "xgot", "xt"} <= models
    assert not {"goals", "shots", "tackles"} & models


def test_chances_created_is_not_double_counted() -> None:
    """chances_created == key_passes (spike, 277/277): only one of them is registered."""
    keys = {m.source_key for m in REGISTRY}
    assert "passing.key_passes" in keys
    assert "chances_created" not in keys and "creation.chances_created" not in keys
