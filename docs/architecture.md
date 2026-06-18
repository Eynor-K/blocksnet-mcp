# Архитектура (локальный режим)

## 1. Структура репозитория

```
blocksnet-mcp/
├── .python310/                     # локальный portable Python 3.10.11 (gitignored)
├── .venv/                          # окружение Python 3.10.11 с зависимостями (gitignored)
├── blocksnet_mcp/                  # тонкая MCP-обёртка
│   ├── __init__.py
│   ├── server.py                   # FastMCP-приложение: транспорт stdio, регистрация инструментов
│   ├── tools_mcp.py                # MCP-инструмент analyze_urban_question
│   ├── serialize.py                # AgentResult → строгий JSON (секции, леджер гипотез, before→after, артефакты)
│   └── settings.py                 # DATA_DIR, MODEL, CHAT_URL/API_KEY, лимиты итераций/времени
│
├── blocksnet_agent/                # перенос из blocksnet-agent (ядро агента, без изменений)
│   ├── __init__.py  agent.py  hypotheses.py  prompts.py
│   ├── config.py  llm.py  runtime.py  metrics.py     # metrics.py обязателен — из него импортируют инварианты C1/C2/C3
│   └── tools/  (data, network, provision, services, indicators, optimize, viz, registry, __init__)
│
├── data/                           # ★ ВАШИ ЛОКАЛЬНЫЕ ДАННЫЕ
│   ├── service_type.json  archetypes.csv             # нормативы и веса — версионируются
│   ├── blocks_with_services.gpkg  acc_mx.pickle      # геоданные — локально, gitignored
│   └── platform/                                      # GeoJSON слои сервисов, gitignored
│
├── tests/
│   ├── test_serialize.py           # контракт AgentResult → JSON
│   └── test_tool_contract.py       # схема входа/выхода инструмента
│
├── requirements.txt
├── .env.example                    # CHAT_URL/API_KEY/MODEL + DATA_DIR
├── README.md
└── .gitignore                      # .env, .venv, .python310, outputs/, кэши, бинарники data/
```

Граница ответственности: **`blocksnet_agent/` — рассуждающее ядро** (перенесено как есть),
**`blocksnet_mcp/` — тонкий слой запуска**. Обёртка не лезет в логику агента: читает
конфиг, вызывает `run()`, сериализует результат.

> **Что НЕ нужно в локальном режиме:** `context.py` (адаптер UrbanDB), `auth.py` (Bearer),
> `Dockerfile`/`docker-compose.yml`, порт/HTTP. Всё это — 🕘 Future (см. README §Future).

## 2. Модули обёртки `blocksnet_mcp/`

| Модуль | Ответственность |
|---|---|
| `server.py` | FastMCP / `mcp` Python SDK; транспорт **stdio**; регистрация инструментов из `tools_mcp.py`; чтение `settings` |
| `tools_mcp.py` | объявление инструмента `analyze_urban_question`: `BlocksNetAgent(model=…, data_dir=DATA_DIR).run(question)` → `serialize.to_json(result)`. Долгий вызов → таймаут/лимит итераций |
| `serialize.py` | `AgentResult` → строгий JSON по контракту: `result["sections"]`, леджер гипотез (`hypotheses.py`), числа `before→after` из наблюдений сценария, `confidence`, `limitations`, пути артефактов/`run_dir` |
| `settings.py` | конфиг: `DATA_DIR`, `MODEL`, `CHAT_URL`/`API_KEY` (наследуется от агента), лимиты итераций/времени |

## 3. Поток обработки запроса

```
Локальный MCP-клиент (Claude Desktop / свой клиент)
  │  запускает сервер как подпроцесс (stdio)
  │  tool call: analyze_urban_question {question, max_iterations?}
  ▼
server.py ──▶ tools_mcp.analyze_urban_question
                  │
                  ▼
           BlocksNetAgent(model, data_dir=DATA_DIR).run(question)
                  │   читает локальные data/ (ensure_blocks/ensure_acc_mx)
                  │   PTR-гипотезы → инструменты blocksnet → инварианты → измерение
                  ▼   AgentResult (sections, hypotheses, confidence, run_dir, artifacts)
           serialize.to_json(result)
                  ▼
           строгий JSON (см. tool_contract.md)
```

Рассуждающий слой (PTR-цикл, инварианты M1–M3/C1–C3/C-Hyp, скоринг `confidence`) целиком внутри
`BlocksNetAgent.run()` — обёртка его не модифицирует.

## 4. Транспорт

- **stdio** — сервер запускается локальным MCP-клиентом как подпроцесс. Без портов, без сетевой
  авторизации (локальное доверие).
- *(Опционально, не обязательно)* FastMCP поддерживает и локальный HTTP-транспорт, если удобнее
  дёргать по `http://localhost:PORT/mcp` — но для одиночного локального использования достаточно stdio.

## 5. Данные (локально)

| Что | Откуда |
|---|---|
| `blocks_with_services.gpkg`, `acc_mx.pickle` | ваши локальные файлы в `DATA_DIR` (как в исходном агенте) |
| `service_type.json`, `archetypes.csv` | нормативы/веса (версионируются) |

Адаптер данных не нужен: агент уже грузит локальную модель из `DATA_DIR`. Сборка данных из UrbanDB по
`scenario_id` — 🕘 Future (локальная gitignored-справка `docs/mas_integration_reference.md`).

## 6. Зависимости

Окружение собрано на Python `3.10.11` в `.venv/`. Из исходного агента перенесено ядро + аналитика:
[blocksnet](https://github.com/aimclub/blocksnet) (зафиксировать `1.0.0a9`),
[LangChain](https://github.com/langchain-ai/langchain) (`langchain-classic`, `langchain-openai`),
[Optuna](https://optuna.org/) (TPE), [GeoPandas](https://geopandas.org/) + matplotlib, `tiktoken`.
**Добавляется:** `mcp` Python SDK (FastMCP). Eval/исследовательский слой `blocksnet-agent` не переносится.

Проверенные команды:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -c "import blocksnet_mcp.server"
```
