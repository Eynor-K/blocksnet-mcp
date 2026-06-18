from typing import Any, TypedDict


class AgentResult(TypedDict, total=False):
    input: str
    output: str
    log: list[Any]
    # Метакогнитивные поля структурированного вывода:
    confidence: float
    limitations: list[str]
    sections: dict[str, str]
    run_dir: str


def __getattr__(name: str) -> Any:
    if name == "BlocksNetAgent":
        from blocksnet_agent.agent import BlocksNetAgent

        return BlocksNetAgent
    raise AttributeError(name)


__all__ = ["AgentResult", "BlocksNetAgent"]
