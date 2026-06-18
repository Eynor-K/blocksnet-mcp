from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from blocksnet_mcp.tools_mcp import analyze_urban_question as _analyze_urban_question


mcp = FastMCP("blocksnet")


@mcp.tool()
def analyze_urban_question(question: str, max_iterations: int | None = None) -> dict[str, Any]:
    """Run BlocksNetAgent on local data and return a structured urban analysis JSON."""

    return _analyze_urban_question(question=question, max_iterations=max_iterations)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
