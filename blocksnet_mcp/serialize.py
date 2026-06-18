from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from blocksnet_agent import AgentResult


_STATUS_VALUES = {"supported", "refuted", "inconclusive"}
_SECTION_FALLBACK = {
    "analysis_plan": "ANALYSIS PLAN",
    "result": "RESULT",
}


def to_json(result: AgentResult) -> dict[str, Any]:
    """Convert BlocksNetAgent AgentResult to the MCP JSON contract."""

    sections = dict(result.get("sections") or {})
    output = str(result.get("output") or "")
    run_dir = str(result.get("run_dir") or "")

    result_text = _result_text(sections, output)
    payload = {
        "question": str(result.get("input") or ""),
        "analysis_plan": _section_text(sections, output, "ANALYSIS PLAN"),
        "result": result_text,
        "hypotheses": _parse_hypotheses(_section_text(sections, output, "HYPOTHESES")),
        "measured": _extract_measured(run_dir, result_text),
        "recommendation_blocks": _extract_recommendation_blocks(sections, output),
        "confidence": _as_float(result.get("confidence"), default=0.0),
        "limitations": _limitations(result),
        "artifacts": _artifacts(run_dir),
        "run_id": _run_id(run_dir),
    }
    return payload


def _section_text(sections: dict[str, str], output: str, name: str) -> str:
    text = str(sections.get(name) or "").strip()
    if text:
        return text
    match = re.search(
        rf"^{re.escape(name)}:\s*(.*?)(?=^[A-Z][A-Z \-]+:|\Z)",
        output or "",
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _result_text(sections: dict[str, str], output: str) -> str:
    result = _section_text(sections, output, "RESULT")
    reflection = _section_text(sections, output, "REFLECTION")
    if result and reflection:
        return f"{result}\n\nREFLECTION: {reflection}".strip()
    return result or output.strip()


def _parse_hypotheses(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if not line:
            continue
        fields = _parse_semicolon_fields(line)
        if not fields:
            continue
        status = fields.get("status", "inconclusive").strip().lower()
        if status not in _STATUS_VALUES:
            status = "inconclusive"
        item = {
            "id": fields.get("id", str(len(items) + 1)).strip(),
            "claim": fields.get("claim", "").strip(),
            "prediction": fields.get("prediction", "").strip(),
            "test": fields.get("test", "").strip(),
            "status": status,
            "evidence": fields.get("evidence", "").strip(),
        }
        if any(value for key, value in item.items() if key != "status"):
            items.append(item)
    return items


def _parse_semicolon_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key = ""
    current_value: list[str] = []
    for part in line.split(";"):
        if ":" in part:
            if current_key:
                fields[current_key] = ";".join(current_value).strip()
            key, value = part.split(":", 1)
            current_key = key.strip().lower()
            current_value = [value.strip()]
        elif current_key:
            current_value.append(part.strip())
    if current_key:
        fields[current_key] = ";".join(current_value).strip()
    return fields


def _extract_measured(run_dir: str, result_text: str = "") -> dict[str, dict[str, float]]:
    measured = _service_before_after_values(result_text)
    if measured:
        return measured
    log = _read_run_log(run_dir)
    if not log:
        return {}
    text = "\n".join(str(call.get("observation", "")) for call in log.get("tool_calls", []))
    measured = _service_before_after_values(text)
    if measured:
        return measured
    service_match = re.search(
        r"\b([a-z][a-z0-9_]+)\s+(?:strong|missing)[_ ](?:before|after)\b",
        text,
        flags=re.IGNORECASE,
    )
    if service_match:
        values = _before_after_values(text)
        if values:
            measured[service_match.group(1)] = values
    if measured:
        return measured
    values = _before_after_values(text)
    return {"scenario": values} if values else {}


def _service_before_after_values(text: str) -> dict[str, dict[str, float]]:
    measured: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        r"\b([a-z][a-z0-9_]+)\s+strong\s+(-?\d+(?:[.,]\d+)?)\s*(?:->|→)\s*(-?\d+(?:[.,]\d+)?)"
        r"(?:[^;\n.]*?\bmissing\s+(-?\d+(?:[.,]\d+)?)\s*(?:->|→)\s*(-?\d+(?:[.,]\d+)?))?",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        service = match.group(1)
        values = {
            "strong_before": float(match.group(2).replace(",", ".")),
            "strong_after": float(match.group(3).replace(",", ".")),
        }
        if match.group(4) is not None and match.group(5) is not None:
            values["missing_before"] = float(match.group(4).replace(",", "."))
            values["missing_after"] = float(match.group(5).replace(",", "."))
        measured[service] = values
    return measured


def _before_after_values(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    patterns = {
        "strong_before": r"strong[_ ]before\D+(-?\d+(?:[.,]\d+)?)",
        "strong_after": r"strong[_ ]after\D+(-?\d+(?:[.,]\d+)?)",
        "missing_before": r"missing[_ ]before\D+(-?\d+(?:[.,]\d+)?)",
        "missing_after": r"missing[_ ]after\D+(-?\d+(?:[.,]\d+)?)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values[key] = float(match.group(1).replace(",", "."))
    arrows = re.findall(r"(-?\d+(?:[.,]\d+)?)\s*(?:->|→)\s*(-?\d+(?:[.,]\d+)?)", text)
    if arrows and "strong_before" not in values and "strong_after" not in values:
        before, after = arrows[-1]
        values["strong_before"] = float(before.replace(",", "."))
        values["strong_after"] = float(after.replace(",", "."))
    return values


def _extract_recommendation_blocks(sections: dict[str, str], output: str) -> list[int]:
    text = " ".join(str(sections.get(name, "")) for name in ("RESULT", "REFLECTION", "HYPOTHESES"))
    if not text.strip():
        text = output
    result: list[int] = []
    for bracketed in re.findall(r"(?:квартал\w*|blocks?)\D{0,80}\[([0-9,\s]+)\]", text, flags=re.IGNORECASE):
        for raw in re.findall(r"\d{1,5}", bracketed):
            value = int(raw)
            if value not in result:
                result.append(value)
    found = re.findall(r"(?:block(?:_id)?|кварт\w*)\s*№?\s*(\d{1,5})", text, flags=re.IGNORECASE)
    for raw in found:
        value = int(raw)
        if value not in result:
            result.append(value)
    return result


def _limitations(result: AgentResult) -> list[str]:
    values = result.get("limitations") or []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values if str(value).strip()]


def _artifacts(run_dir: str) -> list[str]:
    log = _read_run_log(run_dir)
    if log:
        files = []
        base = Path(run_dir)
        for item in log.get("saved_files", []):
            path = Path(str(item.get("path", "")))
            files.append(_relative_or_name(path, base))
        if files:
            return files
    path = Path(run_dir) if run_dir else None
    if not path or not path.exists():
        return []
    artifacts = [
        _relative_or_name(item, path)
        for item in path.rglob("*")
        if item.is_file() and item.name not in {"run_log.json", "run_log.md"}
    ]
    return sorted(artifacts)


def _relative_or_name(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _read_run_log(run_dir: str) -> dict[str, Any]:
    if not run_dir:
        return {}
    path = Path(run_dir) / "run_log.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_id(run_dir: str) -> str:
    if not run_dir:
        return ""
    name = Path(run_dir).name
    return name.removeprefix("run_")


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
