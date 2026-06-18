# blocksnet-mcp

`blocksnet-mcp` - локальный MCP-сервер городской аналитики поверх `BlocksNetAgent`.
Сервер предоставляет один основной инструмент `analyze_urban_question(...)`: принимает вопрос на
естественном языке, запускает полный рассуждающий агент BlocksNet на локальной модели города и
возвращает строгий машиночитаемый JSON.

Проект следует подходу **WIKI-LLM**: документация и директории индексируются так, чтобы LLM могла быстро
понять назначение репозитория, выбрать нужные файлы и не загружать весь проект в контекст.
Главный навигационный индекс: [docs/WIKI-LLM.md](docs/WIKI-LLM.md).

## Концепция

Выбран **Вариант 2**: не выносить 33 внутренних инструмента `blocksnet` как отдельные MCP-tools, а
обернуть агента целиком.

Причина: ценность системы не только в расчетах, а в рассуждающем слое агента:

1. PTR-цикл `predict -> test -> revise`: фальсифицируемые гипотезы до расчетов и классификация исхода.
2. RAG по инструментам: короткие описания плюс полные карточки через `find_tools` / `get_tool_help`.
3. Инварианты качества M1-M3, C1/C2/C3, C-Hyp: проверка заземленности, измеренности и самосогласованности.
4. Измеренные предложения развития: TPE-оптимизация зон и сценарная проверка `before -> after`.

MCP-слой остается тонким: читает настройки, вызывает `BlocksNetAgent.run(...)`, сериализует результат.

## Текущий объем

Текущий MVP рассчитан на локальную работу:

| Область | Решение |
|---|---|
| Транспорт | `stdio`, сервер запускается MCP-клиентом как подпроцесс |
| Данные | локальная папка `data/` через `DATA_DIR` |
| LLM | внешний OpenAI-совместимый API (`CHAT_URL`, `API_KEY`, `MODEL`) |
| Инструмент | `analyze_urban_question(question, max_iterations?)` |
| Выход | JSON с планом, результатом, гипотезами, измеренными эффектами, ограничениями и артефактами |

Вне текущего объема: UrbanDB-контекст (`scenario_id` / `project_id`), HTTP-транспорт, Bearer-авторизация,
Docker-деплой и регистрация в MAS. В этом репозитории реализован только локальный stdio MCP.

## Архитектура MVP

```text
Локальный MCP-клиент
  -> stdio
  -> blocksnet_mcp.server
  -> tools_mcp.analyze_urban_question
  -> BlocksNetAgent.run(question)
  -> serialize.to_json(result)
  -> JSON-ответ MCP-клиенту
```

Структура репозитория:

```text
blocksnet-mcp/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── .python310/          # локальный Python 3.10.11, gitignored
├── .venv/               # локальное окружение Python 3.10.11, gitignored
├── docs/
│   ├── README.md
│   ├── WIKI-LLM.md
│   ├── overview_and_concept.md
│   ├── architecture.md
│   ├── tool_contract.md
│   ├── deployment.md
│   ├── mcp_repository_plan.md
│   ├── mas_integration_reference.md    # 🕘 Future-справка, gitignored
│   └── mas_registration.md             # 🕘 Future-справка, gitignored
├── blocksnet_mcp/
│   ├── README.md
│   ├── server.py
│   ├── tools_mcp.py
│   ├── serialize.py
│   ├── settings.py
│   └── __init__.py
├── blocksnet_agent/
│   ├── README.md
│   └── ... ядро агента из blocksnet-agent
├── data/
│   ├── README.md
│   ├── service_type.json
│   ├── archetypes.csv
│   ├── blocks_with_services.gpkg
│   └── acc_mx.pickle
├── tests/
│   ├── README.md
│   ├── test_serialize.py
│   └── test_tool_contract.py
├── scripts/
│   ├── README.md
│   └── smoke_client.py
├── examples/
│   ├── README.md
│   └── test_visualization.ipynb
└── outputs/             # runtime-артефакты, gitignored
```

## Контракт инструмента

`analyze_urban_question(question: str, max_iterations: int = 10)` возвращает JSON:

```json
{
  "question": "...",
  "analysis_plan": "...",
  "result": "...",
  "hypotheses": [
    {
      "id": "1",
      "claim": "...",
      "prediction": "...",
      "test": "...",
      "status": "supported|refuted|inconclusive",
      "evidence": "..."
    }
  ],
  "measured": {
    "pitch": {
      "strong_before": 0.732,
      "strong_after": 0.781,
      "missing_before": 785,
      "missing_after": 769
    }
  },
  "recommendation_blocks": [2, 4, 5, 11, 12],
  "confidence": 0.78,
  "limitations": ["..."],
  "artifacts": ["maps/provision.png", "scenario.csv"],
  "run_id": "run_20260617_004403"
}
```

Полная спецификация: [docs/tool_contract.md](docs/tool_contract.md).

## Локальный запуск

Рабочее локальное окружение уже создано в репозитории:

| Путь | Назначение | Git |
|---|---|---|
| `.python310/` | portable Python `3.10.11` для проекта | ignored |
| `.venv/` | виртуальное окружение на Python `3.10.11` | ignored |
| `requirements.txt` | зависимости локального MCP и BlocksNetAgent | yes |

```bash
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts\smoke_client.py
.\.venv\Scripts\python.exe -m blocksnet_mcp.server
```

Минимальный `.env`:

```env
CHAT_URL=https://openrouter.ai/api/v1
API_KEY=sk-...
MODEL=openai/gpt-4o
DATA_DIR=./data
```

Пример конфигурации MCP-клиента:

```jsonc
{
  "mcpServers": {
    "blocksnet": {
      "command": "P:/AI_asistent/ITMO/blocksnet-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "blocksnet_mcp.server"],
      "cwd": "P:/AI_asistent/ITMO/blocksnet-mcp",
      "env": {
        "CHAT_URL": "https://openrouter.ai/api/v1",
        "API_KEY": "sk-...",
        "MODEL": "openai/gpt-4o",
        "DATA_DIR": "./data"
      }
    }
  }
}
```

Подробнее: [docs/deployment.md](docs/deployment.md).

## Индексация WIKI-LLM

| Индекс | Назначение |
|---|---|
| [docs/WIKI-LLM.md](docs/WIKI-LLM.md) | Главная карта проекта для LLM-навигации |
| [docs/README.md](docs/README.md) | Человекочитаемый индекс документации |
| [blocksnet_mcp/README.md](blocksnet_mcp/README.md) | Индекс реализованного MCP-слоя |
| [blocksnet_agent/README.md](blocksnet_agent/README.md) | Индекс переносимого ядра агента |
| [data/README.md](data/README.md) | Индекс локальных данных |
| [tests/README.md](tests/README.md) | Индекс контрактных тестов |
| [scripts/README.md](scripts/README.md) | Индекс локальных smoke-проверок MCP |
| [examples/README.md](examples/README.md) | Индекс интерактивных блокнотов |

## Документация

| Документ | О чем |
|---|---|
| [docs/overview_and_concept.md](docs/overview_and_concept.md) | что строится, зачем MCP-обертка целиком, границы локального режима |
| [docs/architecture.md](docs/architecture.md) | структура, модули, поток запроса, транспорт |
| [docs/tool_contract.md](docs/tool_contract.md) | вход и выход `analyze_urban_question` |
| [docs/deployment.md](docs/deployment.md) | локальная установка и подключение MCP-клиента |
| [docs/mcp_repository_plan.md](docs/mcp_repository_plan.md) | план переноса ядра агента и реализации MVP |
| [examples/test_visualization.ipynb](examples/test_visualization.ipynb) | интерактивная визуализация тестов, run logs, артефактов и карт |
| `docs/mas_integration_reference.md` 🕘 | future-справка по MAS/UrbanDB, локальная и gitignored |
| `docs/mas_registration.md` 🕘 | future-строки реестра Urban services, локальная и gitignored |

## Статус

Локальный MVP реализован: ядро `BlocksNetAgent` перенесено, stdio MCP-сервер добавлен, JSON-сериализация
и контрактные тесты находятся в `tests/`. Окружение собрано на Python `3.10.11`; `pip check` чистый,
`pytest` проходит (`7 passed`), MCP stdio smoke проходит через `scripts/smoke_client.py`. Источник истины
по текущему объему - этот README, [docs/WIKI-LLM.md](docs/WIKI-LLM.md), код `blocksnet_mcp/`, контракт
[docs/tool_contract.md](docs/tool_contract.md) и тест-план `docs/plans/test_plan.md`.
