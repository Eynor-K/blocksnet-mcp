import json
from pathlib import Path

from langchain_core.tools import BaseTool

from blocksnet_agent.metrics import FAILURE_MARKERS
from blocksnet_agent.tools.data import make_data_tools
from blocksnet_agent.tools.indicators import make_indicators_tools
from blocksnet_agent.tools.network import make_network_tools
from blocksnet_agent.tools.optimize import make_optimize_tools
from blocksnet_agent.tools.provision import make_provision_tools
from blocksnet_agent.tools.registry import build_tool_registry, make_help_tools
from blocksnet_agent.tools.services import make_services_tools
from blocksnet_agent.tools.viz import make_viz_tools

# T1.2: инструменты, для которых повторный вызов с теми же аргументами в рамках ОДНОГО запуска
# идемпотентен — результат можно отдать из кэша, не пересчитывая и не пересохраняя артефакты.
# Сюда НЕ входят генераторы/оптимизаторы (TPE стохастичен), viz (render_*) и meta-инструменты RAG.
_MEMOIZABLE_PREFIXES = ("compute_", "list_", "load_")
_NON_MEMOIZABLE_TOOLS = {"compute_scenario_provision", "list_cached_data"}
_STALE_OBSERVATION_MARKERS = (
    "нет кэшированных",
    "Сначала вызови",
    "сначала вызови",
    "не найден",
    "не удалось",
)


def _memoize_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Оборачивает идемпотентные инструменты кэшем (tool, args)->observation на время запуска.

    Убирает дубли вызовов, которые порождает слой согласованности при реентри: повторный
    идентичный вызов возвращает прошлое наблюдение и не плодит повторные CSV/карты.
    """
    cache: dict[str, str] = {}
    wrapped: list[BaseTool] = []
    for tool in tools:
        original_func = getattr(tool, "func", None)
        if (
            original_func is None
            or tool.name in _NON_MEMOIZABLE_TOOLS
            or not any(tool.name.startswith(p) for p in _MEMOIZABLE_PREFIXES)
        ):
            wrapped.append(tool)
            continue

        def make_wrapped(func, name):
            def memoized(*args, **kwargs):
                try:
                    key = name + "|" + json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=str)
                except Exception:
                    return func(*args, **kwargs)
                if key in cache:
                    return cache[key]
                result = func(*args, **kwargs)
                if isinstance(result, str) and _cacheable_observation(result):
                    cache[key] = result
                return result

            return memoized

        try:
            wrapped.append(tool.model_copy(update={"func": make_wrapped(original_func, tool.name)}))
        except Exception:
            wrapped.append(tool)
    return wrapped


def _cacheable_observation(result: str) -> bool:
    text = result.strip()
    if text.startswith(FAILURE_MARKERS):
        return False
    return not any(marker in text for marker in _STALE_OBSERVATION_MARKERS)


def make_tools(state: dict, data_dir: Path, output_dir: Path) -> list[BaseTool]:
    ctx = {"state": state, "data_dir": data_dir, "output_dir": output_dir}
    domain_tools = (
        make_data_tools(ctx)
        + make_network_tools(ctx)
        + make_provision_tools(ctx)
        + make_services_tools(ctx)
        + make_indicators_tools(ctx)
        + make_optimize_tools(ctx)
        + make_viz_tools(ctx)
    )
    # Двухуровневые доки: LLM видит короткое описание (.description ← первая строка docstring),
    # полное — через get_tool_help/find_tools (RAG по инструментам, без заданных workflow).
    short_tools, registry = build_tool_registry(domain_tools)
    tools = short_tools + make_help_tools(registry)
    return _memoize_tools(tools)


__all__ = ["make_tools"]
