"""RAG по инструментам BlocksNet (без заранее заданных workflow).

Идея: агент видит КОРОТКОЕ описание каждого инструмента (первая строка docstring) —
этого достаточно для быстрого выбора. Если не уверен, какой инструмент нужен или как
его правильно вызвать, он берёт ПОЛНОЕ описание по запросу (``get_tool_help``) или ищет
подходящие инструменты (``find_tools``). Никаких пайплайнов/рецептов под тип вопроса —
только справка по самим инструментам, чтобы агент реже ошибался в их использовании.

Полная справка живёт прямо в docstring инструмента (рядом с кодом): первая строка —
краткое описание (уходит в LLM как ``tool.description``), остальные строки — полная
справка (параметры, контракт вход/выход, интерпретация, подводные камни).
"""

from __future__ import annotations

import re

from langchain_core.tools import BaseTool, tool

_SERVICE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pitch": ("спорт", "спортив", "спортплощад", "площадк", "sports", "sport", "playground"),
    "sports_centre": ("спортцентр", "спорткомплекс", "фитнес", "sports centre", "sport centre"),
    "school": ("школ", "school", "education"),
    "kindergarten": ("детсад", "детский сад", "садик", "kindergarten"),
    "polyclinic": ("поликлиник", "клиник", "медицин", "health", "clinic"),
    "pharmacy": ("аптек", "лекарств", "pharmacy"),
    "convenience": ("магазин", "продукт", "ритейл", "convenience"),
    "bus_stop": ("останов", "автобус", "транспорт", "bus"),
    "swimming_pool": ("бассейн", "swimming"),
    "extra_education": ("допобраз", "круж", "секци", "extra education"),
    "cafe": ("кафе", "coffee", "cafe"),
    "park": ("парк", "зелён", "зелен", "park"),
}
_SERVICE_TOOLS = {
    "compute_service_provision",
    "suggest_target_blocks",
    "propose_zone_development",
    "compute_scenario_provision",
    "get_weakest_services",
}


def split_doc(doc: str | None) -> tuple[str, str]:
    """Делит docstring на (короткое описание, полная справка)."""
    text = (doc or "").strip()
    if not text:
        return "", ""
    short = text.splitlines()[0].strip()
    return short, text


def build_tool_registry(tools: list[BaseTool]) -> tuple[list[BaseTool], dict[str, dict[str, str]]]:
    """Выставляет инструментам КОРОТКИЕ описания и собирает реестр полной справки.

    Возвращает (инструменты с короткими .description, реестр name -> {short, full}).
    LLM видит только короткое описание; полное доступно через get_tool_help/find_tools.
    """
    registry: dict[str, dict[str, str]] = {}
    short_tools: list[BaseTool] = []
    for t in tools:
        short, full = split_doc(t.description)
        short = short or t.name
        registry[t.name] = {"short": short, "full": _augment_tool_doc(t.name, full or short)}
        try:
            short_tools.append(t.model_copy(update={"description": short}))
        except Exception:
            short_tools.append(t)
    return short_tools, registry


def _score(query: str, text: str) -> int:
    terms = set(_query_terms(query))
    if not terms:
        return 0
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _query_terms(query: str) -> list[str]:
    lowered = query.lower().replace("_", " ")
    terms = [term for term in re.split(r"\W+", lowered) if len(term) > 2]
    for service, aliases in _SERVICE_SYNONYMS.items():
        if service in lowered or any(alias in lowered for alias in aliases):
            terms.extend([service, *aliases, "service", "provision", "обеспеченность"])
    return terms


def _service_hints(query: str) -> list[str]:
    lowered = query.lower().replace("_", " ")
    hints = []
    for service, aliases in _SERVICE_SYNONYMS.items():
        if service in lowered or any(alias in lowered for alias in aliases):
            hints.append(service)
    return hints


def _augment_tool_doc(name: str, full: str) -> str:
    if name not in _SERVICE_TOOLS:
        return full
    aliases = ", ".join(f"{service} ({'/'.join(aliases[:3])})" for service, aliases in _SERVICE_SYNONYMS.items())
    return (
        f"{full}\n\n"
        "Индекс сервисов: русские/английские синонимы помогают выбрать валидный service_type. "
        f"Примеры: {aliases}."
    )


def make_help_tools(registry: dict[str, dict[str, str]]) -> list[BaseTool]:
    """Создаёт meta-инструменты RAG: find_tools (поиск) и get_tool_help (полная справка)."""

    @tool
    def find_tools(query: str) -> str:
        """Находит подходящие инструменты BlocksNet по запросу (keyword-поиск по описаниям).

        Используй, когда не уверен, какой инструмент нужен под задачу. Возвращает список
        наиболее релевантных инструментов с краткими описаниями. Затем посмотри полное
        описание выбранного через get_tool_help(name) перед вызовом.
        """
        scored = sorted(
            ((_score(query, f"{name} {meta['short']} {meta['full']}"), name, meta) for name, meta in registry.items()),
            key=lambda item: item[0],
            reverse=True,
        )
        hits = [(name, meta) for score, name, meta in scored if score > 0][:6]
        if not hits:
            hits = [(name, meta) for _, name, meta in scored[:6]]
        lines = ["Подходящие инструменты (затем get_tool_help(name) для полной справки):"]
        service_hints = _service_hints(query)
        if service_hints:
            lines.append(
                "Похоже на service_type: "
                + ", ".join(f"'{service}'" for service in service_hints)
                + ". Для обеспеченности используй compute_service_provision с этим именем."
            )
        lines.extend(f"- {name}: {meta['short']}" for name, meta in hits)
        return "\n".join(lines)

    @tool
    def get_tool_help(name: str) -> str:
        """Возвращает ПОЛНОЕ описание инструмента BlocksNet: параметры, контракт, интерпретацию, подводные камни.

        Вызывай перед использованием незнакомого инструмента, чтобы не ошибиться в параметрах.
        """
        meta = registry.get(name)
        if not meta:
            close = [n for n in registry if name.lower() in n.lower() or n.lower() in name.lower()]
            suggestion = f" Похожие: {', '.join(close)}." if close else ""
            return f"Инструмент '{name}' не найден.{suggestion} Все инструменты: {', '.join(sorted(registry))}"
        return f"{name}:\n{meta['full']}"

    return [find_tools, get_tool_help]
