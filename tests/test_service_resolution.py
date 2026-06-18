from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from blocksnet.enums import LandUse

from blocksnet_agent.tools.data import resolve_service_name
from blocksnet_agent.tools.optimize import _available_service_weights, make_optimize_tools, UnknownServiceSet
from blocksnet_agent.tools.provision import make_provision_tools

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
AVAILABLE = [
    "pitch", "school", "kindergarten", "convenience",
    "pharmacy", "polyclinic", "bus_stop", "swimming_pool", "sports_centre",
]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("sports", "pitch"),            # англ. алиас из service_aliases.json
        ("спорт", "pitch"),             # рус. алиас
        ("спортивные площадки", "pitch"),  # рус. name_ru, фаззи
        ("school", "school"),           # точное каноническое имя
        ("школа", "school"),            # name_ru
        ("аптека", "pharmacy"),
        ("детский сад", "kindergarten"),
        ("бассейн", "swimming_pool"),
    ],
)
def test_resolve_known_services(query, expected):
    canon, _ranked = resolve_service_name(query, DATA_DIR, AVAILABLE)
    assert canon == expected


def test_resolve_unknown_returns_none():
    canon, ranked = resolve_service_name("zzz_not_a_service", DATA_DIR, AVAILABLE)
    assert canon is None
    assert isinstance(ranked, list)


def _synthetic_blocks():
    return pd.DataFrame({
        "population": [100, 200],
        "capacity_pitch": [1, 2],
        "capacity_school": [1, 2],
        "capacity_kindergarten": [1, 2],
        "capacity_convenience": [1, 2],
    })


def test_service_set_alias_focuses_target_service():
    # Регрессия: вопрос про спорт ('sports') не должен молча уходить в пресет 'basic'.
    weights = _available_service_weights("sports", _synthetic_blocks(), LandUse.RESIDENTIAL, DATA_DIR)
    assert weights == {"pitch": 1.0}


def test_service_set_preset_still_expands():
    weights = _available_service_weights("basic", _synthetic_blocks(), LandUse.RESIDENTIAL, DATA_DIR)
    assert "school" in weights and "pitch" in weights


def test_unknown_service_set_raises_instead_of_basic():
    with pytest.raises(UnknownServiceSet):
        _available_service_weights("zzz_not_a_service", _synthetic_blocks(), LandUse.RESIDENTIAL, DATA_DIR)


def test_compute_service_provision_resolves_alias(monkeypatch, tmp_path):
    import blocksnet_agent.tools.provision as provision_module

    def fake_compute(_state, _data_dir, _output_dir, service_type, *_args, **_kwargs):
        return {
            "service_type": service_type,
            "accessibility_minutes": 15,
            "strong": 0.1,
            "weak": 0.2,
            "full": 1,
            "partial": 0,
            "missing": 1,
        }

    monkeypatch.setattr(provision_module, "_compute_single_service_provision", fake_compute)
    ctx = {"state": {"blocks": _synthetic_blocks(), "acc_mx": pd.DataFrame([[0, 1], [1, 0]])}, "data_dir": DATA_DIR, "output_dir": tmp_path}
    compute_service_provision = make_provision_tools(ctx)[0]

    result = compute_service_provision.invoke({"service_type": "sports"})

    assert "сервисом 'pitch'" in result


def test_scenario_provision_filters_to_requested_service_set(monkeypatch, tmp_path):
    import blocksnet_agent.tools.provision as provision_module

    calls = []

    def fake_compute(_state, _data_dir, _output_dir, service_type, *_args, **_kwargs):
        calls.append(service_type)
        return {
            "service_type": service_type,
            "accessibility_minutes": 15,
            "strong": 0.1,
            "weak": 0.2,
            "full": 1,
            "partial": 0,
            "missing": 1,
        }

    monkeypatch.setattr(provision_module, "_compute_single_service_provision", fake_compute)
    ctx = {"state": {"blocks": _synthetic_blocks(), "acc_mx": pd.DataFrame([[0, 1], [1, 0]])}, "data_dir": DATA_DIR, "output_dir": tmp_path}
    compute_scenario_provision = make_optimize_tools(ctx)[3]

    result = compute_scenario_provision.invoke({
        "scenario": {"0": {"sports": 10, "convenience": 5}},
        "service_set": "sports",
    })

    assert "| pitch |" in result
    assert "| convenience |" not in result
    assert calls == ["pitch", "pitch"]


def test_scenario_provision_reports_target_service_mismatch(monkeypatch, tmp_path):
    import blocksnet_agent.tools.provision as provision_module

    monkeypatch.setattr(provision_module, "_compute_single_service_provision", lambda *_args, **_kwargs: {})
    ctx = {"state": {"blocks": _synthetic_blocks(), "acc_mx": pd.DataFrame([[0, 1], [1, 0]])}, "data_dir": DATA_DIR, "output_dir": tmp_path}
    compute_scenario_provision = make_optimize_tools(ctx)[3]

    result = compute_scenario_provision.invoke({
        "scenario": {"0": {"convenience": 5}},
        "service_set": "sports",
    })

    assert "сценарий содержит только ['convenience']" in result
    assert "['pitch']" in result
