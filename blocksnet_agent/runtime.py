"""Per-run artifact context and logging.

Каждый запуск агента создаёт отдельный каталог ``run_<timestamp>-<id>`` внутри
``output_dir``. Туда складываются CSV-результаты инструментов, картограммы (подкаталог
``maps``) и финальный ``run_log.{json,md}``. Инструменты регистрируют сохранённые
файлы через :func:`record_file`, а карты пишут в :attr:`RunContext.maps_dir`.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_current: "RunContext | None" = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class RunLogger:
    # T1.3: потолок картограмм на один запуск — батч-операции не должны плодить десятки PNG.
    MAP_BUDGET = 24

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.saved_files: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._maps_reserved = 0

    def reserve_map(self) -> bool:
        """Резервирует слот под картограмму; False, если бюджет запуска исчерпан (T1.3)."""
        with self._lock:
            if self._maps_reserved >= self.MAP_BUDGET:
                return False
            self._maps_reserved += 1
            return True

    def record_file(
        self,
        path: str | Path,
        kind: str,
        source: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        p = Path(path)
        entry: dict[str, Any] = {
            "path": str(p),
            "kind": kind,
            "size": _safe_size(p),
            "mime": mimetypes.guess_type(str(p))[0] or "",
            "meta": meta or {},
        }
        if source:
            entry["source"] = source
        with self._lock:
            if not any(item.get("path") == entry["path"] for item in self.saved_files):
                self.saved_files.append(entry)

    def record_tool_call(self, tool: str, args: Any, observation: str) -> None:
        with self._lock:
            self.tool_calls.append(
                {"tool": tool, "args": args, "observation": str(observation)[:1000]}
            )

    def to_json(self) -> dict[str, Any]:
        with self._lock:
            return {
                "saved_files": list(self.saved_files),
                "tool_calls": list(self.tool_calls),
            }


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_dir: Path
    maps_dir: Path
    logger: RunLogger = field(default_factory=RunLogger)
    started_at: str = field(default_factory=_now_iso)


def start_run(base_output_dir: str | Path) -> RunContext:
    """Создаёт каталог нового запуска и делает его активным контекстом."""
    global _current
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = Path(base_output_dir) / f"run_{run_id}"
    maps_dir = run_dir / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    _current = RunContext(run_id=run_id, run_dir=run_dir, maps_dir=maps_dir)
    return _current


def get_run_context() -> RunContext | None:
    return _current


def get_run_dir(fallback: str | Path) -> Path:
    """Возвращает каталог активного запуска либо создаёт fallback-каталог."""
    ctx = get_run_context()
    if ctx:
        return ctx.run_dir
    path = Path(fallback)
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_file(
    path: str | Path,
    kind: str,
    source: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    ctx = get_run_context()
    if ctx:
        ctx.logger.record_file(path, kind, source=source, meta=meta)


def write_run_log(
    ctx: RunContext,
    question: str,
    model: str,
    final_answer: str,
    tool_calls: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    self_confidence: str | None = None,
    limitations: list[str] | None = None,
) -> dict[str, str]:
    """Сохраняет run_log.json / run_log.md в каталоге запуска."""
    logger_data = ctx.logger.to_json()
    payload = {
        "run_id": ctx.run_id,
        "question": question,
        "model": model,
        "started_at": ctx.started_at,
        "finished_at": _now_iso(),
        "confidence": confidence,
        "self_confidence": self_confidence,
        "limitations": limitations or [],
        "tool_calls": tool_calls or logger_data["tool_calls"],
        "saved_files": logger_data["saved_files"],
        "final_answer": final_answer,
    }
    json_path = ctx.run_dir / "run_log.json"
    md_path = ctx.run_dir / "run_log.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    return {"log_json": str(json_path), "log_md": str(md_path)}


def _to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Run Log",
        "",
        f"- Run ID: `{payload.get('run_id', '')}`",
        f"- Model: `{payload.get('model', '')}`",
        f"- Started: `{payload.get('started_at', '')}`",
        f"- Finished: `{payload.get('finished_at', '')}`",
        "",
        "## Question",
        str(payload.get("question", "")),
        "",
        "## Confidence",
        _confidence_line(payload),
        "",
        "## Tool Calls",
        "| Tool | Args | Observation |",
        "|---|---|---|",
    ]
    for call in payload.get("tool_calls", []):
        lines.append(
            "| `{tool}` | `{args}` | {obs} |".format(
                tool=_md_cell(call.get("tool", "")),
                args=_md_cell(str(call.get("args", ""))[:200]),
                obs=_md_cell(str(call.get("observation", ""))[:240]),
            )
        )
    lines.extend(["", "## Saved Files", "| Path | Kind | Size |", "|---|---|---:|"])
    for item in payload.get("saved_files", []):
        lines.append(
            f"| `{_md_cell(item.get('path', ''))}` | {item.get('kind', '')} | {item.get('size', 0)} |"
        )
    limitations = payload.get("limitations") or []
    if limitations:
        lines.extend(["", "## Limitations"])
        lines.extend(f"- {_md_cell(str(item))}" for item in limitations)
    lines.extend(["", "## Final Answer", str(payload.get("final_answer", ""))])
    return "\n".join(lines)


def _confidence_line(payload: dict[str, Any]) -> str:
    """T1.5: показываем авторитетный авто-скоринг и самооценку модели раздельно."""
    scored = payload.get("confidence")
    self_reported = payload.get("self_confidence")
    scored_text = f"{scored:.2f}" if isinstance(scored, (int, float)) else "—"
    parts = [f"- Scored (авторитетный): `{scored_text}`"]
    if self_reported:
        parts.append(f"- Self-reported (модель): `{self_reported}`")
    return "\n".join(parts)


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")
