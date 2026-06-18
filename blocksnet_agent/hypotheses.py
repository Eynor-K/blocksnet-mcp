from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Hypothesis:
    id: str
    claim: str
    prediction: str
    test: str
    status: str = "open"
    parent_id: str | None = None
    evidence: str = ""

    def complete(self) -> bool:
        return bool(self.claim.strip() and self.prediction.strip() and self.test.strip())


@dataclass
class HypothesisLedger:
    hypotheses: list[Hypothesis] = field(default_factory=list)

    def to_context(self) -> str:
        if not self.hypotheses:
            return ""
        lines = [
            "PTR hypothesis ledger (generated before tool calls; use as research orientation, not a fixed workflow):"
        ]
        for item in self.hypotheses:
            lines.append(
                f"- {item.id}: claim={item.claim}; prediction={item.prediction}; "
                f"candidate_test={item.test}; status={item.status}"
            )
        return "\n".join(lines)

    def to_section(self) -> str:
        if not self.hypotheses:
            return ""
        lines = []
        for item in self.hypotheses:
            parent = f"; parent_id: {item.parent_id}" if item.parent_id else ""
            evidence = f"; evidence: {item.evidence}" if item.evidence else ""
            lines.append(
                f"- id: {item.id}; claim: {item.claim}; prediction: {item.prediction}; "
                f"test: {item.test}; status: {item.status}{parent}{evidence}"
            )
        return "\n".join(lines)

    def to_jsonable(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.hypotheses]


def build_hypothesis_ledger(
    task: str,
    tool_names: list[str],
    llm_invoke,
    available_metrics: list[str] | None = None,
) -> HypothesisLedger:
    """Fail-open ex-ante hypothesis generation."""
    metrics_hint = ", ".join(available_metrics or [])
    prompt = (
        "Сформируй 2-5 фальсифицируемых гипотез для BlocksNetAgent ДО вызова инструментов.\n"
        "Это ориентир исследования, не workflow. Для каждой гипотезы дай claim, prediction и candidate test tool.\n"
        "Prediction ОБЯЗАН быть операционализируемым доступным инструментом: укажи result_key/metric, "
        "сущность (block_id N — N это ПЛЕЙСХОЛДЕР, подставь реальный) и проверяемый ориентир: "
        "'= 0', '< median', '> median' или конкретный порог.\n"
        "Хороший формат: \"competitive_provision_<service> for block_id <N> < median\" или "
        "\"scenario_provision for block_id <N> improves strong provision after compute_scenario_provision\".\n"
        "ВАЖНО про сущности: подставляй ТОЛЬКО block_id, явно названный В ВОПРОСЕ. Если вопрос НЕ называет "
        "конкретный квартал (городской/размещение/диагностика) — НЕ выдумывай номер: формулируй предсказание "
        "о кандидатных кварталах, которые надо НАЙТИ (через suggest_target_blocks), или о городской метрике, "
        "а не о произвольном block_id. Не подставляй номер из примера.\n"
        "Не используй непроверяемые формулировки вроде 'на 20%', 'лучше соседних кварталов', если такой "
        "ориентир не вычисляется доступным инструментом.\n"
        "Используй только инструменты из списка. Верни строго JSON-массив объектов с ключами "
        "id, claim, prediction, test. Не добавляй markdown.\n\n"
        f"QUESTION: {task}\n\nTOOLS: {', '.join(tool_names[:80])}\n\n"
        f"AVAILABLE_RESULT_KEYS_OR_METRICS: {metrics_hint or 'none yet; prefer tools that create measurable result_keys'}"
    )
    try:
        response = llm_invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        data = _extract_json_array(content)
    except Exception:
        return HypothesisLedger()
    hypotheses = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        hyp = Hypothesis(
            id=str(item.get("id") or f"H{index}"),
            claim=str(item.get("claim", "")).strip(),
            prediction=str(item.get("prediction", "")).strip(),
            test=str(item.get("test", "")).strip(),
        )
        if hyp.complete():
            hypotheses.append(hyp)
    return HypothesisLedger(hypotheses[:5])


def classify_hypothesis_ledger(
    ledger: HypothesisLedger,
    steps: list[dict[str, str]],
    llm_invoke,
    state: dict[str, Any] | None = None,
) -> HypothesisLedger:
    if not ledger.hypotheses:
        return ledger
    evidence = _evidence_text(steps)
    if not evidence:
        for item in ledger.hypotheses:
            item.status = "inconclusive"
            item.evidence = "no successful tool evidence"
        return ledger
    for item in ledger.hypotheses:
        if item.test and not _test_was_called(item.test, steps):
            item.status = "inconclusive"
            item.evidence = f"candidate test '{item.test}' was not called"
            continue
        metric = _classify_metric_prediction(item.prediction, evidence, state or {})
        if metric:
            item.status, item.evidence = metric
            continue
        numeric = _classify_numeric_prediction(item.prediction, evidence)
        if numeric:
            item.status, item.evidence = numeric
            continue
        delta = _classify_delta_prediction(item.prediction, evidence)
        if delta:
            item.status, item.evidence = delta
            continue
        qualitative = _classify_qualitative(item, evidence, llm_invoke)
        if qualitative:
            item.status, item.evidence = qualitative
        else:
            item.status = "inconclusive"
            item.evidence = "prediction could not be compared to observations"
    return ledger


def inconclusive_measurement_issue(
    ledger: HypothesisLedger,
    tool_names: set[str],
    steps: list[dict[str, str]],
) -> str:
    for item in ledger.hypotheses:
        if item.status != "inconclusive":
            continue
        test = _normalize_tool_name(item.test, tool_names)
        if test and not _tool_was_called(test, steps):
            return (
                f"гипотеза {item.id} осталась inconclusive, хотя её test '{test}' доступен и не был вызван — "
                "измерь эту гипотезу инструментом или явно объясни, почему измерить реально нечем"
            )
    return ""


def hypothesis_contradiction_issue(ledger: HypothesisLedger, output_text: str) -> str:
    refuted_claims = [item.claim for item in ledger.hypotheses if item.status == "refuted"]
    if not refuted_claims:
        return ""
    text = output_text.lower()
    for claim in refuted_claims:
        words = [word for word in re.split(r"\W+", claim.lower()) if len(word) > 4]
        if words and sum(1 for word in words if word in text) >= max(2, len(words) // 2):
            return (
                "гипотеза в PTR-леджере получила status=refuted, но финальный ответ всё ещё "
                "утверждает её claim — отклони или переформулируй гипотезу и объясни наблюдение"
            )
    return ""


def merge_hypotheses_section(output_text: str, ledger: HypothesisLedger) -> str:
    section = ledger.to_section()
    if not section:
        return output_text
    pattern = re.compile(
        r"^HYPOTHESES:.*?(?=^(?:ANALYSIS PLAN|RESULT|REFLECTION|NUMERIC SELF-CHECK|FOLLOW_UPS|CONFIDENCE|LIMITATIONS):|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(output_text or ""):
        return pattern.sub(f"HYPOTHESES: {section}\n", output_text)
    return (output_text.rstrip() + f"\nHYPOTHESES: {section}").strip()


def _extract_json_array(content: str) -> list[Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _evidence_text(steps: list[dict[str, str]]) -> str:
    lines = []
    for step in steps:
        obs = str(step.get("observation", ""))
        if not obs or obs.startswith(("Ошибка:", "Traceback", "Exception")):
            continue
        lines.append(f"{step.get('tool')}: {obs}")
    return "\n".join(lines)[:8000]


def _test_was_called(test: str, steps: list[dict[str, str]]) -> bool:
    test_lower = test.lower()
    return any(str(step.get("tool", "")).lower() in test_lower or test_lower in str(step.get("tool", "")).lower() for step in steps)


def _tool_was_called(tool: str, steps: list[dict[str, str]]) -> bool:
    return any(str(step.get("tool", "")) == tool for step in steps)


def _normalize_tool_name(test: str, tool_names: set[str]) -> str:
    lowered = str(test or "").lower()
    for name in tool_names:
        if name.lower() == lowered or name.lower() in lowered:
            return name
    return ""


def _classify_metric_prediction(
    prediction: str,
    evidence: str,
    state: dict[str, Any],
) -> tuple[str, str] | None:
    pred = prediction.strip()
    result_key = _prediction_result_key(pred, state)
    block_id = _prediction_block_id(pred)
    if result_key and block_id is not None and result_key in state:
        try:
            from blocksnet_agent.tools.data import _metric_series_for_blocks

            series = _metric_series_for_blocks(state[result_key])
            if series is not None and not series.empty and block_id in series.index:
                value = float(series.loc[block_id])
                return _compare_value_to_prediction(value, float(series.median()), pred, f"{result_key} block_id {block_id}")
        except Exception:
            pass
    parsed = _metric_value_from_evidence(pred, evidence)
    if parsed:
        value, median, label = parsed
        return _compare_value_to_prediction(value, median, pred, label)
    return None


def _prediction_result_key(prediction: str, state: dict[str, Any]) -> str:
    candidates = sorted((str(key) for key in state if key not in {"blocks", "acc_mx"}), key=len, reverse=True)
    lowered = prediction.lower()
    for key in candidates:
        if key.lower() in lowered:
            return key
    match = re.search(r"\b(?:result_key|metric)\s*[:=]\s*([a-z][a-z0-9_]+)", lowered)
    return match.group(1) if match else ""


def _prediction_block_id(prediction: str) -> int | None:
    match = re.search(r"(?:block(?:_id)?|кварт\w*)\s*№?\s*(\d{1,5})", prediction, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _metric_value_from_evidence(prediction: str, evidence: str) -> tuple[float, float, str] | None:
    result_key = re.search(r"\b[a-z][a-z0-9_]*(?:provision|indicators|accessibility|centrality|diversity)[a-z0-9_]*\b", prediction)
    block_id = _prediction_block_id(prediction)
    if not result_key or block_id is None:
        return None
    key = result_key.group(0)
    for line in evidence.splitlines():
        if key.lower() not in line.lower() or f"block_id {block_id}" not in line.lower():
            continue
        tail = line.lower().split(f"block_id {block_id}", 1)[1]
        value_match = re.search(r":\s*([-+]?\d+(?:[.,]\d+)?)", tail)
        median_match = re.search(r"медиана города\s+([-+]?\d+(?:[.,]\d+)?)", line, flags=re.IGNORECASE)
        if value_match and median_match:
            return (
                float(value_match.group(1).replace(",", ".")),
                float(median_match.group(1).replace(",", ".")),
                f"{key} block_id {block_id} from evidence",
            )
        numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", tail)
        if len(numbers) >= 2:
            return (
                float(numbers[0].replace(",", ".")),
                float(numbers[1].replace(",", ".")),
                f"{key} block_id {block_id} from evidence",
            )
    return None


def _compare_value_to_prediction(value: float, median: float, prediction: str, label: str) -> tuple[str, str] | None:
    pred = prediction.lower().replace(",", ".")
    threshold_match = re.search(r"(>=|<=|>|<|=)\s*(-?\d+(?:\.\d+)?)", pred)
    if threshold_match:
        op = threshold_match.group(1)
        threshold = float(threshold_match.group(2))
        supported = {
            ">": value > threshold,
            ">=": value >= threshold,
            "<": value < threshold,
            "<=": value <= threshold,
            "=": abs(value - threshold) <= 1e-9,
        }[op]
        status = "supported" if supported else "refuted"
        return status, f"{label}: observed {value:.4f}; prediction {op} {threshold:.4f}"
    if any(marker in pred for marker in ("ниже медиан", "below median", "< median", "less than median")):
        status = "supported" if value < median else "refuted"
        return status, f"{label}: observed {value:.4f}, city median {median:.4f}; expected below median"
    if any(marker in pred for marker in ("выше медиан", "above median", "> median", "greater than median")):
        status = "supported" if value > median else "refuted"
        return status, f"{label}: observed {value:.4f}, city median {median:.4f}; expected above median"
    if any(marker in pred for marker in ("= median", "равно медиан")):
        status = "supported" if abs(value - median) <= 1e-9 else "refuted"
        return status, f"{label}: observed {value:.4f}, city median {median:.4f}; expected equal median"
    return None


def _classify_numeric_prediction(prediction: str, evidence: str) -> tuple[str, str] | None:
    pred = prediction.replace(",", ".")
    match = re.search(r"(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)", pred)
    if not match:
        return None
    op = match.group(1)
    threshold = float(match.group(2))
    numbers = [float(raw.replace(",", ".")) for raw in re.findall(r"-?\d+(?:[.,]\d+)?", evidence)]
    if not numbers:
        return ("inconclusive", "numeric threshold found, but observations contain no numeric values")
    observed = numbers[-1]
    supported = {
        ">": observed > threshold,
        ">=": observed >= threshold,
        "<": observed < threshold,
        "<=": observed <= threshold,
    }[op]
    status = "supported" if supported else "refuted"
    return status, f"numeric comparison: observed {observed:g} {op} expected threshold {threshold:g}"


def _classify_delta_prediction(prediction: str, evidence: str) -> tuple[str, str] | None:
    pred = prediction.lower()
    wants_improve = any(marker in pred for marker in ("improve", "increase", "улучш", "повыс", "увелич"))
    wants_reduce = any(marker in pred for marker in ("reduce", "decrease", "сниж", "уменьш"))
    if not (wants_improve or wants_reduce):
        return None
    pairs = [
        (float(a.replace(",", ".")), float(b.replace(",", ".")))
        for a, b in re.findall(r"(-?\d+(?:[.,]\d+)?)\s*(?:->|→)\s*(-?\d+(?:[.,]\d+)?)", evidence)
    ]
    if not pairs:
        return None
    if wants_improve:
        supported = any(after > before for before, after in pairs)
        status = "supported" if supported else "refuted"
        return status, "before→after comparison: " + "; ".join(f"{a:g}->{b:g}" for a, b in pairs[:6])
    if wants_reduce:
        supported = any(after < before for before, after in pairs)
        status = "supported" if supported else "refuted"
        return status, "before→after comparison: " + "; ".join(f"{a:g}->{b:g}" for a, b in pairs[:6])
    return None


def _classify_qualitative(item: Hypothesis, evidence: str, llm_invoke) -> tuple[str, str] | None:
    prompt = (
        "Classify one hypothesis strictly against tool observations.\n"
        "Allowed status: supported, refuted, inconclusive. Return one line as JSON object "
        "{\"status\":\"...\",\"evidence\":\"short reason\"}.\n\n"
        f"CLAIM: {item.claim}\nPREDICTION: {item.prediction}\nTEST: {item.test}\n\nOBSERVATIONS:\n{evidence}"
    )
    try:
        response = llm_invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        data = json.loads(content.strip())
        status = str(data.get("status", "")).strip().lower()
        if status not in {"supported", "refuted", "inconclusive"}:
            return None
        return status, str(data.get("evidence", "")).strip()[:500]
    except Exception:
        return None
