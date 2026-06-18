from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


async def _run(
    question: str | None,
    max_iterations: int,
    call: bool,
    invalid: bool,
    call_timeout: float,
) -> dict[str, Any]:
    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "blocksnet_mcp.server"],
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            payload: dict[str, Any] = {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema,
                    }
                    for tool in tools_result.tools
                ]
            }
            if call:
                args = {"question": "" if invalid else question, "max_iterations": max_iterations}
                try:
                    result = await asyncio.wait_for(
                        session.call_tool("analyze_urban_question", args),
                        timeout=call_timeout,
                    )
                    payload["call"] = _jsonable(result)
                except Exception as exc:
                    payload["call_error"] = {"type": type(exc).__name__, "message": str(exc)}
            return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test blocksnet-mcp over stdio.")
    parser.add_argument("--question", default="Где разместить новые спортивные площадки?")
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--call-timeout", type=float, default=180.0)
    parser.add_argument("--call", action="store_true", help="Call analyze_urban_question after list_tools.")
    parser.add_argument("--invalid", action="store_true", help="Call with an empty question to test error handling.")
    args = parser.parse_args()

    payload = asyncio.run(_run(args.question, args.max_iterations, args.call, args.invalid, args.call_timeout))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
