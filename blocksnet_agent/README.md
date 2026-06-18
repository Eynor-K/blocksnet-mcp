# Индекс `blocksnet_agent/`

Назначение: переносимое рассуждающее ядро агента городской аналитики из соседнего проекта
`blocksnet-agent`.

Для локального MVP пакет перенесен **как есть**. MCP-репозиторий не должен менять логику PTR-цикла,
инвариантов, tool-calling и доменных расчетов без отдельной задачи.

## Состав

| Файл или папка | Роль |
|---|---|
| `__init__.py` | Публичный API: `BlocksNetAgent`, `AgentResult` |
| `agent.py` | Основной агент, запуск tool-calling, инварианты, confidence |
| `hypotheses.py` | PTR-цикл: генерация, проверка и ревизия гипотез |
| `prompts.py` | System prompt и формат ответа |
| `config.py` | Настройки и загрузка окружения |
| `llm.py` | OpenAI-compatible LLM |
| `runtime.py` | Каталоги `outputs/run_*`, run logs, артефакты |
| `metrics.py` | Метрики и проверки, используемые агентом |
| `tools/` | Инструменты BlocksNet и RAG-справка |

## Что не переносить в этот пакет

Eval-скрипты, notebooks, HTML-отчеты и runtime-outputs из `blocksnet-agent` не относятся к локальному
MCP MVP. Если они понадобятся позже, индексировать их отдельно.

## Источник

Upstream-контекст: `P:\AI_asistent\ITMO\blocksnet-agent`.
