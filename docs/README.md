# Документация `blocksnet-mcp`

**Локальный MCP-сервер городской аналитики** — обёртка над агентом BlocksNetAgent. Сервер выставляет
один инструмент `analyze_urban_question(...)`, который внутри запускает полный рассуждающий агент
(ANALYSIS PLAN → инструменты `blocksnet` → PTR-гипотезы → измеренное предложение `before→after`) и
возвращает **машиночитаемый JSON**. Интеллект агента (PTR-цикл + слой инвариантов) сохраняется целиком —
в этом смысл выбранного «Варианта 2».

> **Текущий объём (важно).** Проект работает **локально**, на **ваших собственных данных** в `data/`.
> Интеграция с UrbanDB/Urban API (`scenario_id`) и развёртывание в сети ИТМО — **вне текущего объёма**,
> отложены на будущее (помечены как «Future» в соответствующих документах). Транспорт — локальный
> **stdio** (без сетевой авторизации/портов).

---

## Содержание

| Документ | О чём |
|---|---|
| [../README.md](../README.md) | Главная спецификация репозитория: концепция, объем MVP, архитектура, локальный запуск, WIKI-LLM индексация |
| [WIKI-LLM.md](WIKI-LLM.md) | LLM-индекс проекта: карта директорий, маршруты чтения, границы источников |
| [overview_and_concept.md](overview_and_concept.md) | Что строим (локально) и зачем; что наследуется от агента; почему Вариант 2 |
| [architecture.md](architecture.md) | Структура репозитория, модули `blocksnet_mcp/`, поток запроса, транспорт stdio |
| [tool_contract.md](tool_contract.md) | `analyze_urban_question`: вход/выход, JSON-контракт, примеры |
| [deployment.md](deployment.md) | Локальный запуск: `.env`, данные, подключение MCP-клиента |
| [mcp_repository_plan.md](mcp_repository_plan.md) | План: что переносить из `blocksnet-agent`, что писать заново, объём MVP |
| [reports/test_report_20260618.md](reports/test_report_20260618.md) | Отчет о тестировании локального MVP: уровни 0-2, run IDs, артефакты, исправления |
| `mas_integration_reference.md` | 🕘 **Future:** интеграция в MAS-платформу (UrbanDB-контекст, реестр) — локальная справка, gitignored |
| `mas_registration.md` | 🕘 **Future:** строки реестра «Urban services» — локальная справка, gitignored |

---

## WIKI-LLM индексы

| Индекс | Назначение |
|---|---|
| [WIKI-LLM.md](WIKI-LLM.md) | Главная карта проекта для LLM-навигации |
| [../blocksnet_mcp/README.md](../blocksnet_mcp/README.md) | Индекс тонкого MCP-слоя |
| [../blocksnet_agent/README.md](../blocksnet_agent/README.md) | Индекс переносимого ядра агента |
| [../data/README.md](../data/README.md) | Индекс локальной модели города |
| [../tests/README.md](../tests/README.md) | Индекс контрактных тестов |
| [../scripts/README.md](../scripts/README.md) | Индекс MCP smoke-клиента |
| [../examples/README.md](../examples/README.md) | Индекс интерактивных блокнотов |

---

## Локальное окружение

| Команда | Назначение |
|---|---|
| `.\.venv\Scripts\python.exe -V` | Проверить Python `3.10.11` |
| `.\.venv\Scripts\python.exe -m pip check` | Проверить зависимости |
| `.\.venv\Scripts\python.exe -m pytest tests` | Запустить контрактные тесты |
| `.\.venv\Scripts\python.exe scripts\smoke_client.py` | Проверить MCP `list_tools()` |
| `.\.venv\Scripts\python.exe -m blocksnet_mcp.server` | Запустить stdio MCP-сервер |

Интерактивная визуализация тестов: [`../examples/test_visualization.ipynb`](../examples/test_visualization.ipynb).

`.python310/`, `.venv/`, `outputs/`, кэши и крупные локальные данные игнорируются Git.

---

## В двух словах

```jsonc
// Запрос к инструменту analyze_urban_question (локально, на ваших данных)
{ "question": "Где разместить новые спортивные площадки?" }
```

```jsonc
// Ответ (сокращённо) — машиночитаемые поля поверх нарратива
{
  "question": "Где разместить новые спортивные площадки?",
  "analysis_plan": "...",
  "result": "...",
  "hypotheses": [ { "id": "1", "claim": "...", "status": "supported", "evidence": "..." } ],
  "measured": { "pitch": { "strong_before": 0.732, "strong_after": 0.781 } },
  "recommendation_blocks": [2, 4, 5, 11, 12],
  "confidence": 0.78,
  "limitations": ["..."],
  "artifacts": ["maps/provision.png", "scenario.csv"],
  "run_id": "run_2026..."
}
```

Полный контракт — [tool_contract.md](tool_contract.md). Ядро рассуждения (PTR-цикл, инварианты) уже
перенесено в [`../blocksnet_agent/`](../blocksnet_agent/).
