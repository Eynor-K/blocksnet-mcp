from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from blocksnet_agent import BlocksNetAgent
from blocksnet_agent.config import Settings as AgentSettings

from blocksnet_mcp.serialize import to_json
from blocksnet_mcp.settings import get_mcp_settings


def _trace(message: str) -> None:
    if os.environ.get("BLOCKSNET_MCP_TRACE") != "1":
        return
    path = Path(__file__).resolve().parents[1] / "outputs" / "mcp_trace.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} {message}\n")


def analyze_urban_question(question: str, max_iterations: int | None = None) -> dict[str, Any]:
    """Analyze an urban question using local BlocksNet data and return structured JSON."""

    _trace("tool entered")
    normalized_question = str(question or "").strip()
    if not normalized_question:
        _trace("validation failed: empty question")
        raise ValueError("question must be a non-empty string")

    if max_iterations is not None and max_iterations < 1:
        _trace("validation failed: max_iterations")
        raise ValueError("max_iterations must be >= 1")

    _trace("loading mcp settings")
    settings = get_mcp_settings()
    iterations = max_iterations if max_iterations is not None else settings.max_iterations
    if iterations < 1:
        _trace("validation failed: resolved max_iterations")
        raise ValueError("max_iterations must be >= 1")

    _trace("building agent settings")
    agent_settings = AgentSettings(
        chat_url=settings.chat_url,
        api_key=settings.api_key,
        model=settings.model,
        data_dir=settings.data_dir,
        output_dir=settings.output_dir,
    )
    _trace("constructing agent")
    agent = BlocksNetAgent(settings=agent_settings, max_iterations=iterations)
    _trace("running agent")
    result = agent.run(normalized_question)
    _trace("serializing result")
    payload = to_json(result)
    _trace("tool completed")
    return payload
