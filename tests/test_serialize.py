from __future__ import annotations

import json
from pathlib import Path

from blocksnet_mcp.serialize import to_json


def test_to_json_extracts_contract_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260618-120000-abcdef"
    run_dir.mkdir()
    artifact = run_dir / "scenario.csv"
    artifact.write_text("block_id,value\n1,2\n", encoding="utf-8")
    (run_dir / "run_log.json").write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "tool": "compute_scenario_provision",
                        "observation": "pitch strong_before: 0.7 strong_after: 0.8 missing_before: 10 missing_after: 5",
                    }
                ],
                "saved_files": [{"path": str(artifact), "kind": "csv"}],
            }
        ),
        encoding="utf-8",
    )

    payload = to_json(
        {
            "input": "Где разместить спортивные площадки?",
            "output": "",
            "confidence": 0.78,
            "limitations": ["local data only"],
            "run_dir": str(run_dir),
            "sections": {
                "ANALYSIS PLAN": "plan",
                "RESULT": "recommend block_id 12",
                "HYPOTHESES": (
                    "- id: H1; claim: Спорт дефицитен; prediction: pitch below median; "
                    "test: compute_service_provision; status: supported; evidence: observed"
                ),
            },
        }
    )

    assert payload["question"] == "Где разместить спортивные площадки?"
    assert payload["analysis_plan"] == "plan"
    assert payload["confidence"] == 0.78
    assert payload["limitations"] == ["local data only"]
    assert payload["recommendation_blocks"] == [12]
    assert payload["run_id"] == "20260618-120000-abcdef"
    assert payload["artifacts"] == ["scenario.csv"]
    assert payload["hypotheses"][0]["status"] == "supported"
    assert payload["measured"]["pitch"]["strong_after"] == 0.8


def test_to_json_handles_empty_result() -> None:
    payload = to_json({})

    assert payload["question"] == ""
    assert payload["analysis_plan"] == ""
    assert payload["result"] == ""
    assert payload["hypotheses"] == []
    assert payload["measured"] == {}
    assert payload["recommendation_blocks"] == []
    assert payload["confidence"] == 0.0
    assert payload["limitations"] == []
    assert payload["artifacts"] == []
    assert payload["run_id"] == ""


def test_to_json_wraps_string_limitations_and_deduplicates_blocks() -> None:
    payload = to_json(
        {
            "input": "test",
            "output": "",
            "limitations": "single limitation",
            "sections": {
                "RESULT": "recommend block_id 12, квартал 12 and block 7",
            },
        }
    )

    assert payload["limitations"] == ["single limitation"]
    assert payload["recommendation_blocks"] == [12, 7]


def test_to_json_extracts_arrow_measured_from_run_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260618-130000-fedcba"
    run_dir.mkdir()
    (run_dir / "run_log.json").write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "tool": "compute_scenario_provision",
                        "observation": "scenario provision before→after: 0.45 → 0.62",
                    }
                ],
                "saved_files": [],
            }
        ),
        encoding="utf-8",
    )

    payload = to_json({"run_dir": str(run_dir)})

    assert payload["measured"]["scenario"]["strong_before"] == 0.45
    assert payload["measured"]["scenario"]["strong_after"] == 0.62


def test_to_json_extracts_real_agent_summary_format() -> None:
    payload = to_json(
        {
            "sections": {
                "RESULT": (
                    "- Кварталы с наименьшей доступностью спортивных услуг: "
                    "[2, 4, 5, 11, 12, 14, 15, 26, 27, 36].\n"
                    "- Улучшение обеспеченности после добавления площадок: "
                    "convenience strong 0.359→0.380, missing 790→783; "
                    "kindergarten strong 0.938→0.938, missing 669→668."
                )
            }
        }
    )

    assert payload["recommendation_blocks"] == [2, 4, 5, 11, 12, 14, 15, 26, 27, 36]
    assert payload["measured"]["convenience"] == {
        "strong_before": 0.359,
        "strong_after": 0.38,
        "missing_before": 790.0,
        "missing_after": 783.0,
    }
    assert payload["measured"]["kindergarten"]["missing_after"] == 668.0
