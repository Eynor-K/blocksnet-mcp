from __future__ import annotations

import pytest

from blocksnet_mcp.tools_mcp import analyze_urban_question


def test_analyze_urban_question_requires_question() -> None:
    with pytest.raises(ValueError, match="question"):
        analyze_urban_question("")


def test_analyze_urban_question_requires_positive_iterations() -> None:
    with pytest.raises(ValueError, match="max_iterations"):
        analyze_urban_question("test", max_iterations=0)
