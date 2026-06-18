from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any


FAILURE_MARKERS = ("Ошибка:", "Traceback", "Exception", "not found", "не найден")
WASTED_OBSERVATION_MARKERS = FAILURE_MARKERS + (
    "нет кэшированных",
    "Сначала вызови",
    "сначала вызови",
    "не найдено",
    "не удалось",
)
COMPARATIVE_TOOLS = {"compute_scenario_provision"}
SECTION_NAMES = (
    "ANALYSIS PLAN",
    "RESULT",
    "REFLECTION",
    "HYPOTHESES",
    "NUMERIC SELF-CHECK",
    "FOLLOW_UPS",
    "CONFIDENCE",
    "LIMITATIONS",
)
EFFECT_MARKERS = (
    "улучш",
    "ухудш",
    "повыш",
    "сниж",
    "увелич",
    "уменьш",
    "before->after",
    "before→after",
    "до->после",
    "до→после",
)
PROPOSAL_MARKERS = (
    "размест",
    "добав",
    "постро",
    "рекоменду",
    "увеличить емкость",
    "увеличить ёмкость",
    "предлож",
    "сценар",
)
_BLOCK_ID_RE = re.compile(r"(?:кварт\w*|block(?:_id)?)\s*№?\s*(\d{1,5})", re.IGNORECASE)
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_SERVICE_RE = re.compile(
    r"\b(?:school|kindergarten|polyclinic|convenience|pharmacy|pitch|bus_stop|"
    r"swimming_pool|extra_education|cafe|sports?_centre|park|dog_park)\b",
    re.IGNORECASE,
)
_SERVICE_FIELD_RE = re.compile(r"\b(?:service_type|service|capacity)[\s_:=\-]+([a-z][a-z0-9_]{2,})\b", re.IGNORECASE)

EVAL_WEIGHTS: dict[str, dict[str, float]] = {
    "D1": {
        "entity_in_trace": 1.0,
        "plan_present": 1.0,
        "judge_framing": 1.0,
    },
    "D2": {
        "selection_correctness": 1.0,
        "low_waste": 1.0,
        "low_duplicates": 1.0,
    },
    "D3": {
        "low_tool_error_rate": 1.0,
        "per_block_grounding": 1.0,
        "measuredness": 1.0,
        "artifact_discipline": 1.0,
        "self_correction": 0.5,
    },
    "D4": {
        "groundedness": 1.0,
        "concreteness": 1.0,
        "measuredness": 1.0,
        "confidence_calibration": 1.0,
        "ptr_quality": 1.0,
        "judge_coherence": 1.0,
        "judge_justification": 1.0,
        "judge_uncertainty": 1.0,
        "judge_metacognition": 1.0,
    },
}


def extract_sections(text: str) -> dict[str, str]:
    pattern = re.compile(rf"^({'|'.join(re.escape(name) for name in SECTION_NAMES)}):\s*(.*)$", re.MULTILINE)
    matches = list(pattern.finditer(text or ""))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = (match.group(2) + text[start:end]).strip()
        sections[name] = body
    return sections


def mentioned_block_ids(text: str) -> list[int]:
    ids: list[int] = []
    for match in _BLOCK_ID_RE.finditer(text or ""):
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if value not in ids:
            ids.append(value)
    return ids


def asserted_block_ids(output_text: str) -> list[int]:
    sections = extract_sections(output_text)
    text = " ".join(sections.get(name, "") for name in ("RESULT", "REFLECTION", "HYPOTHESES"))
    return mentioned_block_ids(text)


def successful_steps(steps: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [
        step
        for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(FAILURE_MARKERS)
    ]


def successful_evidence_text(steps: Iterable[dict[str, str]]) -> str:
    return "\n".join(
        f"{step.get('tool_input', '')} {step.get('observation', '')}" for step in successful_steps(steps)
    )


def entity_grounding_issue(output_text: str, steps: list[dict[str, str]]) -> str:
    block_ids = asserted_block_ids(output_text)
    if not block_ids:
        return ""
    evidence = successful_evidence_text(steps)
    missing = [str(bid) for bid in block_ids if not re.search(rf"\b{bid}\b", evidence)]
    if missing:
        return (
            f"ответ утверждает о кварталах {', '.join(missing)}, но ни один инструмент не вызывался "
            f"по ним (нет get_block_info / поквартального значения метрики) — общегородские агрегаты "
            f"нельзя выдавать за показатели конкретного квартала"
        )
    return ""


def has_proposal(output_text: str) -> bool:
    sections = extract_sections(output_text)
    text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES", "REFLECTION")).lower()
    return any(marker in text for marker in PROPOSAL_MARKERS)


def has_comparative_step(steps: list[dict[str, str]]) -> bool:
    return any(step.get("tool", "") in COMPARATIVE_TOOLS for step in successful_steps(steps))


def proposal_measurement_issue(output_text: str, steps: list[dict[str, str]]) -> str:
    sections = extract_sections(output_text)
    claim_text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES")).lower()
    effect_claim = any(marker in claim_text for marker in EFFECT_MARKERS)
    proposal_claim = has_proposal(output_text)
    if not (effect_claim or proposal_claim):
        return ""
    if has_comparative_step(steps):
        return ""
    if proposal_claim:
        return (
            "ответ содержит предложение развития, но не выполнен сравнительный before→after вывод "
            "инструмента — если эффект можно измерить, сначала измерь его (например compute_scenario_provision); "
            "unverified допустим только когда измерить реально нечем"
        )
    return (
        "ответ утверждает эффект (улучшение/изменение метрики), но не выполнен сравнительный "
        "before→after вывод инструмента — измерь эффект (например compute_scenario_provision) "
        "или пометь утверждение unverified, не выдавая непроверенное за подтверждённое"
    )


def recommendation_concreteness_issue(output_text: str, _steps: list[dict[str, str]]) -> str:
    if not has_proposal(output_text):
        return ""
    sections = extract_sections(output_text)
    proposal_text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES", "REFLECTION"))
    has_block = bool(mentioned_block_ids(proposal_text))
    has_service = _has_service_name(proposal_text)
    if has_block and has_service:
        return ""
    missing = []
    if not has_service:
        missing.append("сервис")
    if not has_block:
        missing.append("block_id")
    return "предложение развития неконкретно — назови " + " и ".join(missing) + " предложения"


def groundedness(output_text: str, steps: list[dict[str, str]]) -> float:
    block_ids = asserted_block_ids(output_text)
    if not block_ids:
        return 1.0
    evidence = successful_evidence_text(steps)
    grounded = sum(1 for bid in block_ids if re.search(rf"\b{bid}\b", evidence))
    return grounded / len(block_ids)


def measuredness(output_text: str, steps: list[dict[str, str]]) -> int:
    return int((has_proposal(output_text) or _has_effect_claim(output_text)) and has_comparative_step(steps))


def concreteness(output_text: str) -> int:
    sections = extract_sections(output_text)
    text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES", "REFLECTION"))
    return int(bool(mentioned_block_ids(text)) and _has_service_name(text) and bool(_NUMBER_RE.search(text)))


def run_metrics(
    output_text: str,
    steps: list[dict[str, str]],
    expected_tools: list[str] | None = None,
    expected_entity: Any | None = None,
    category: str | None = None,
    saved_files: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    self_confidence: str | None = None,
    elapsed: float | None = None,
) -> dict[str, Any]:
    calls = [step.get("tool", "") for step in steps]
    unique_calls = {f"{step.get('tool', '')}({step.get('tool_input', '')})" for step in steps}
    files = saved_files or []
    metrics = {
        "groundedness": groundedness(output_text, steps),
        "measuredness": measuredness(output_text, steps),
        "concreteness": concreteness(output_text),
        "calls": len(calls),
        "unique_calls": len(unique_calls),
        "duplicate_calls": max(0, len(calls) - len(unique_calls)),
        "artifacts_csv": sum(1 for item in files if _kind_or_path(item, "csv")),
        "artifacts_png": sum(1 for item in files if _kind_or_path(item, "png")),
        "confidence": confidence,
        "self_confidence": self_confidence,
        "wasted_calls": wasted_calls(steps),
        "index_usage": index_usage(steps),
        "selection_correctness": selection_correctness(steps, expected_tools or []),
        "tool_error_rate": tool_error_rate(steps),
        "self_correction": self_correction(steps),
        "per_block_grounding": per_block_grounding(output_text, steps, expected_entity),
        "artifact_discipline": artifact_discipline(files, category),
        "confidence_calibration": confidence_calibration(confidence, groundedness(output_text, steps)),
        "ptr_quality": ptr_quality(output_text),
        **hypothesis_status_metrics(output_text),
        "elapsed": elapsed,
    }
    metrics.update(
        dimension_scores(
            {
                **metrics,
                "output_text": output_text,
                "steps": steps,
                "expected_entity": expected_entity,
                "category": category,
            }
        )
    )
    return metrics


def wasted_calls(steps: list[dict[str, str]]) -> int:
    return sum(1 for step in steps if _wasted_observation(str(step.get("observation", ""))))


def tool_error_rate(steps: list[dict[str, str]]) -> float:
    if not steps:
        return 0.0
    errors = sum(1 for step in steps if _wasted_observation(str(step.get("observation", ""))))
    return errors / len(steps)


def self_correction(steps: list[dict[str, str]]) -> int:
    failed_families: set[str] = set()
    for step in steps:
        family = _tool_family(str(step.get("tool", "")))
        if not family:
            continue
        if _wasted_observation(str(step.get("observation", ""))):
            failed_families.add(family)
        elif family in failed_families:
            return 1
    return 0


def per_block_grounding(output_text: str, steps: list[dict[str, str]], expected_entity: Any | None = None) -> float:
    expected = [value for value in _entity_values(expected_entity) if value.isdigit()]
    asserted = [str(item) for item in asserted_block_ids(output_text)]
    block_ids = expected or asserted
    if not block_ids:
        return 1.0
    evidence = successful_evidence_text(steps)
    grounded = sum(1 for block_id in block_ids if re.search(rf"\b{re.escape(block_id)}\b", evidence))
    return grounded / len(block_ids)


def artifact_discipline(saved_files: list[dict[str, Any]] | None, category: str | None = None) -> float:
    count = len(saved_files or [])
    category_key = str(category or "").lower()
    budget = 2 if category_key in {"c", "e", "diagnostic", "robustness"} else 6
    if count <= budget:
        return 1.0
    return max(0.0, 1.0 - (count - budget) / max(1, budget))


def confidence_calibration(confidence: float | None, grounded: float | None) -> float | None:
    if confidence is None or grounded is None:
        return None
    try:
        return max(0.0, min(1.0, 1.0 - abs(float(confidence) - float(grounded))))
    except (TypeError, ValueError):
        return None


def ptr_quality(output_text: str) -> float:
    sections = extract_sections(output_text)
    hypotheses = sections.get("HYPOTHESES", output_text or "")
    lines = [line.strip() for line in hypotheses.splitlines() if line.strip()]
    if not lines:
        return 0.0
    falsifiable = sum(1 for line in lines if _status_count(line, "supported") or _status_count(line, "refuted") or _NUMBER_RE.search(line))
    status = hypothesis_status_metrics(output_text)
    has_resolution = bool(status.get("hyp_supported") or status.get("hyp_refuted"))
    return (falsifiable / len(lines)) * (1.0 if has_resolution else 0.5)


def dimension_scores(run: dict[str, Any], judge: dict[str, Any] | None = None) -> dict[str, float]:
    sections = extract_sections(str(run.get("output_text") or run.get("final_answer") or ""))
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    expected_entity = run.get("expected_entity")
    calls = _float(run.get("calls"), len(steps))
    duplicate_calls = _float(run.get("duplicate_calls"), 0.0)
    wasted = _float(run.get("wasted_calls"), 0.0)
    grounded = _float(run.get("groundedness"), 1.0)
    measured = _float(run.get("measuredness"), 0.0)
    concrete = _float(run.get("concreteness"), 0.0)
    selection = _float(run.get("selection_correctness"), None)
    tool_errors = _float(run.get("tool_error_rate"), None)
    block_grounding = _float(run.get("per_block_grounding"), None)
    artifacts = _float(run.get("artifact_discipline"), None)
    correction = _float(run.get("self_correction"), None)
    calibration = _float(run.get("confidence_calibration"), None)
    ptr = _float(run.get("ptr_quality"), None)
    judge_scores = _normalise_judge_scores(judge)

    entity_score = _entity_in_trace(expected_entity, steps, str(run.get("output_text") or run.get("final_answer") or ""))
    plan_present = int(bool(sections.get("ANALYSIS PLAN", "").strip()))

    d1 = _weighted_mean(
        "D1",
        {
            "entity_in_trace": entity_score,
            "plan_present": plan_present,
            "judge_framing": judge_scores.get("framing"),
        },
    )
    d2 = _weighted_mean(
        "D2",
        {
            "selection_correctness": selection,
            "low_waste": max(0.0, 1.0 - wasted / max(1.0, calls)),
            "low_duplicates": max(0.0, 1.0 - duplicate_calls / max(1.0, calls)),
        },
    )
    d3 = _weighted_mean(
        "D3",
        {
            "low_tool_error_rate": None if tool_errors is None else 1.0 - tool_errors,
            "per_block_grounding": block_grounding,
            "measuredness": measured,
            "artifact_discipline": artifacts,
            "self_correction": correction,
        },
    )
    d4 = _weighted_mean(
        "D4",
        {
            "groundedness": grounded,
            "concreteness": concrete,
            "measuredness": measured,
            "confidence_calibration": calibration,
            "ptr_quality": ptr,
            "judge_coherence": judge_scores.get("coherence"),
            "judge_justification": judge_scores.get("justification"),
            "judge_uncertainty": judge_scores.get("uncertainty"),
            "judge_metacognition": judge_scores.get("metacognition"),
        },
    )
    composite = _mean([d1, d2, d3, d4])
    return {"D1": d1, "D2": d2, "D3": d3, "D4": d4, "composite": composite}


def scorecard(run: dict[str, Any], judge: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**run, **dimension_scores(run, judge=judge)}


def index_usage(steps: list[dict[str, str]]) -> int:
    return int(any(step.get("tool", "") in {"find_tools", "get_tool_help"} for step in steps))


def selection_correctness(steps: list[dict[str, str]], expected_tools: list[str]) -> float | None:
    expected = [tool for tool in expected_tools if tool]
    if not expected:
        return None
    observed = [step.get("tool", "") for step in steps]
    if not observed:
        return 0.0
    matched_positions = [observed.index(tool) for tool in expected if tool in observed]
    if not matched_positions:
        return 0.0
    first_expected = min(matched_positions)
    wasted_before = sum(1 for step in steps[:first_expected] if _wasted_observation(str(step.get("observation", ""))))
    duplicate_expected = sum(max(0, observed.count(tool) - 1) for tool in expected)
    score = len(matched_positions) / len(expected)
    penalty = 0.15 * wasted_before + 0.1 * duplicate_expected
    return max(0.0, min(1.0, score - penalty))


def hypothesis_status_metrics(output_text: str) -> dict[str, Any]:
    sections = extract_sections(output_text)
    hypotheses = sections.get("HYPOTHESES", output_text or "")
    counts = {
        "hyp_supported": _status_count(hypotheses, "supported"),
        "hyp_refuted": _status_count(hypotheses, "refuted"),
        "hyp_inconclusive": _status_count(hypotheses, "inconclusive"),
        "hyp_abandoned": _status_count(hypotheses, "abandoned"),
    }
    total = sum(counts.values())
    rates = {
        "hyp_total": total,
        "hyp_supported_rate": counts["hyp_supported"] / total if total else None,
        "hyp_refuted_rate": counts["hyp_refuted"] / total if total else None,
        "hyp_inconclusive_rate": counts["hyp_inconclusive"] / total if total else None,
    }
    return {**counts, **rates}


def _has_effect_claim(output_text: str) -> bool:
    sections = extract_sections(output_text)
    claim_text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES")).lower()
    return any(marker in claim_text for marker in EFFECT_MARKERS)


def _has_service_name(text: str) -> bool:
    return bool(_SERVICE_RE.search(text) or _SERVICE_FIELD_RE.search(text))


def _kind_or_path(item: dict[str, Any], suffix: str) -> bool:
    kind = str(item.get("kind", "")).lower()
    path = Path(str(item.get("path", ""))).suffix.lower().lstrip(".")
    return kind == suffix or path == suffix


def _wasted_observation(observation: str) -> bool:
    return observation.startswith(WASTED_OBSERVATION_MARKERS) or any(
        marker in observation for marker in WASTED_OBSERVATION_MARKERS if marker not in FAILURE_MARKERS
    )


def _status_count(text: str, status: str) -> int:
    return len(re.findall(rf"\bstatus:\s*{re.escape(status)}\b", text or "", flags=re.IGNORECASE))


def _tool_family(tool: str) -> str:
    if not tool:
        return ""
    return re.sub(r"^(compute|get|list|suggest|propose)_", "", tool).split("_")[0]


def _entity_values(expected_entity: Any | None) -> list[str]:
    if expected_entity in (None, ""):
        return []
    if isinstance(expected_entity, (list, tuple, set)):
        return [str(item) for item in expected_entity if str(item)]
    return [str(expected_entity)]


def _entity_in_trace(expected_entity: Any | None, steps: list[dict[str, str]], output_text: str) -> float:
    values = _entity_values(expected_entity)
    if not values:
        return 1.0
    haystack = output_text + "\n" + "\n".join(
        f"{step.get('tool', '')} {step.get('tool_input', '')} {step.get('observation', '')}" for step in steps
    )
    hits = sum(1 for value in values if re.search(rf"\b{re.escape(value)}\b", haystack, flags=re.IGNORECASE))
    return hits / len(values)


def _normalise_judge_scores(judge: dict[str, Any] | None) -> dict[str, float]:
    if not judge:
        return {}
    scores: dict[str, float] = {}
    for key, value in judge.items():
        score = value.get("score") if isinstance(value, dict) else value
        number = _float(score, None)
        if number is None:
            continue
        scores[str(key)] = max(0.0, min(1.0, (number - 1.0) / 4.0))
    return scores


def _weighted_mean(dimension: str, values: dict[str, float | int | None]) -> float:
    weights = EVAL_WEIGHTS[dimension]
    total = 0.0
    weight_total = 0.0
    for name, value in values.items():
        if value is None:
            continue
        weight = weights.get(name, 1.0)
        total += max(0.0, min(1.0, float(value))) * weight
        weight_total += weight
    return total / weight_total if weight_total else 0.0


def _mean(values: list[float | None]) -> float:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else 0.0


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
