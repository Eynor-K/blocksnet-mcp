# WIKI-LLM индекс проекта

Назначение: единая карта `blocksnet-mcp` для LLM-навигации. Индекс помогает быстро выбрать нужные файлы,
не загружая весь репозиторий в контекст.

Дата индексации: 2026-06-18. Последняя актуализация: 2026-06-18. Корень проекта: `blocksnet-mcp/`.

## Как пользоваться

1. Для общего понимания сначала читать `README.md`, затем `docs/overview_and_concept.md`.
2. Для понимания текущего объема MVP читать `docs/mcp_repository_plan.md`, `docs/architecture.md`,
   `docs/tool_contract.md`, `docs/deployment.md`.
3. Для будущей MAS/UrbanDB-интеграции читать только документы `docs/mas_integration_reference.md` и
   `docs/mas_registration.md`; они помечены как Future, не описывают текущий локальный MVP и
   игнорируются Git.
4. Для реализации или правок MCP-слоя открывать `blocksnet_mcp/README.md`, затем
   `server.py`, `tools_mcp.py`, `serialize.py`, `settings.py`.
5. Для правок рассуждающего ядра открывать `blocksnet_agent/README.md`; пакет перенесен из
   `blocksnet-agent`.
6. Для локального запуска использовать `.venv/Scripts/python.exe`; окружение собрано на Python `3.10.11`.

## Верхний уровень

| Путь | Роль | Когда открывать |
|---|---|---|
| `README.md` | Главная спецификация репозитория: концепция, объем MVP, архитектура, запуск, индексы | Первый файл для общего понимания |
| `.gitattributes` | Правила Git для текстовых/бинарных файлов | При настройке репозитория |
| `.gitignore` | Игнор локального окружения, outputs, кэшей и крупных данных | При добавлении файлов |
| `.env.example` | Шаблон локальной конфигурации | При настройке `.env` |
| `requirements.txt` | Зависимости локального MCP и агента | При пересборке окружения |
| `.python310/` | Portable Python 3.10.11, локальный runtime | Не индексировать как источник; gitignored |
| `.venv/` | Виртуальное окружение проекта | Не индексировать как источник; gitignored |
| `.git/` | Git-метаданные | Не использовать как предметный источник |
| `docs/` | Документация, план, контракты, future-справки MAS | При любом анализе проекта |
| `blocksnet_mcp/` | Реализованный MCP-слой сервера | При правках обертки |
| `blocksnet_agent/` | Перенесенное ядро из `blocksnet-agent` | При правках агента |
| `data/` | Локальная модель города и нормативы | При настройке данных |
| `tests/` | Контрактные тесты | При проверке MCP-контракта |
| `scripts/` | Локальные smoke CLI для MCP | При проверке stdio-транспорта |
| `examples/` | Интерактивные notebooks | При визуальном анализе тестов и артефактов |

## Inventory папок

| Папка | Состав сейчас | Целевая роль |
|---|---|---|
| `docs/` | Markdown-документы проекта, `reports/` и этот индекс | Документация, навигация, архитектурные решения |
| `blocksnet_mcp/` | `README.md`, `__init__.py`, `server.py`, `tools_mcp.py`, `serialize.py`, `settings.py` | Код тонкой MCP-обертки |
| `blocksnet_agent/` | Python-пакет агента и `tools/` | Перенесенное ядро агента |
| `data/` | `README.md`, нормативы, локальные gpkg/pickle данные, `platform/` | Локальная модель города |
| `tests/` | `README.md`, `test_serialize.py`, `test_tool_contract.py` | Тесты сериализации и контракта инструмента |
| `scripts/` | `README.md`, `smoke_client.py` | MCP stdio smoke-клиент |
| `examples/` | `README.md`, `test_visualization.ipynb` | Интерактивная визуализация тестового процесса |
| `docs/reports/` | `test_report_20260618.md` | Отчеты по проверкам и результатам |
| `.python310/` | Embedded Python 3.10.11 + pip/virtualenv bootstrap | Локальный runtime, игнорировать |
| `.venv/` | Установленные зависимости проекта | Локальное окружение, игнорировать |
| `outputs/` | `run_*` каталоги после вызовов агента | Runtime-артефакты, игнорировать |

## Документация

| Путь | Роль |
|---|---|
| `docs/README.md` | Человекочитаемый индекс документации |
| `docs/WIKI-LLM.md` | Этот LLM-навигационный индекс |
| `docs/overview_and_concept.md` | Концепция: локальный MCP-сервер, Вариант 2, наследование BlocksNetAgent, границы применимости |
| `docs/architecture.md` | Архитектура локального режима: структура, модули `blocksnet_mcp/`, поток запроса, stdio |
| `docs/tool_contract.md` | Контракт `analyze_urban_question`: вход, JSON-выход, поля, примеры |
| `docs/deployment.md` | Локальный запуск, `.env`, подключение MCP-клиента |
| `docs/mcp_repository_plan.md` | План репозитория: что переносить, что писать заново, фазы P0/Future |
| `docs/reports/test_report_20260618.md` | Отчет о тестировании локального MVP: unit, direct agent, MCP stdio, run IDs и артефакты |
| `examples/test_visualization.ipynb` | Интерактивный блокнот визуализации: уровни тестов, run logs, charts, artifacts, maps |
| `docs/mas_integration_reference.md` | 🕘 Future-справка по MAS: UrbanDB-контекст, деплой, реестр, гэпы; gitignored |
| `docs/mas_registration.md` | 🕘 Future-значения строк для таблицы Urban services; gitignored |

## Целевой MCP-слой

Папка `blocksnet_mcp/` содержит только тонкую обертку. Она не изменяет логику агента.

| Путь | Роль |
|---|---|
| `blocksnet_mcp/README.md` | Индекс и правила ответственности MCP-слоя |
| `blocksnet_mcp/__init__.py` | Пакет MCP-сервера |
| `blocksnet_mcp/server.py` | FastMCP-приложение, транспорт stdio, регистрация инструментов |
| `blocksnet_mcp/tools_mcp.py` | Инструмент `analyze_urban_question` и вызов `BlocksNetAgent.run(...)` |
| `blocksnet_mcp/serialize.py` | Преобразование `AgentResult` в строгий JSON по `docs/tool_contract.md` |
| `blocksnet_mcp/settings.py` | `DATA_DIR`, `CHAT_URL`, `API_KEY`, `MODEL`, лимиты итераций/таймаутов |

## Переносимое ядро агента

Папка `blocksnet_agent/` перенесена из соседнего проекта `blocksnet-agent` как есть, без переписывания
логики под MCP.

Минимальные источники при переносе:

| Путь | Роль |
|---|---|
| `blocksnet_agent/__init__.py` | Публичный API: `BlocksNetAgent`, `AgentResult` |
| `blocksnet_agent/agent.py` | Основной ReAct/tool-calling агент, инварианты, confidence |
| `blocksnet_agent/hypotheses.py` | PTR-цикл гипотез |
| `blocksnet_agent/prompts.py` | System prompt и формат ответа |
| `blocksnet_agent/config.py` | Настройки и корень проекта |
| `blocksnet_agent/llm.py` | OpenAI-compatible LLM |
| `blocksnet_agent/runtime.py` | `outputs/run_*`, `run_log`, регистрация артефактов |
| `blocksnet_agent/metrics.py` | Метрики и инварианты C1/C2/C3, нужные агенту |
| `blocksnet_agent/tools/` | Доменные инструменты BlocksNet и RAG-справка по tools |

Не переносить в локальный MVP из upstream `blocksnet-agent`: `scripts/`, `examples/`, `docs/evaluation/`, `docs/bench/`,
`docs/reports/`, `outputs/` из `blocksnet-agent`.

## Данные

Папка `data/` является локальным источником модели города. Путь задается через `DATA_DIR`.

| Путь | Формат | Роль | Git |
|---|---|---|---|
| `data/README.md` | Markdown | Индекс данных | да |
| `data/service_type.json` | JSON | Нормативы сервисов | да |
| `data/archetypes.csv` | CSV | Веса архетипов для TPE | да |
| `data/blocks_with_services.gpkg` | GeoPackage | Кварталы, сервисы, геометрия, население | локально, gitignored |
| `data/acc_mx.pickle` | Pickle | Предвычисленная матрица доступности | локально, gitignored |
| `data/platform/` | GeoJSON | Слои сервисов для локальной модели | локально, gitignored |

Правило: не пересчитывать матрицу доступности без явной задачи; использовать готовый `acc_mx.pickle`.

## Окружение

| Путь/команда | Роль |
|---|---|
| `.python310/python.exe` | Локальный portable Python `3.10.11` |
| `.venv/Scripts/python.exe` | Основной интерпретатор проекта |
| `.venv/Scripts/python.exe -m pytest tests` | Контрактные тесты (`7 passed`) |
| `.venv/Scripts/python.exe -m pip check` | Проверка зависимостей (`No broken requirements found`) |
| `.venv/Scripts/python.exe scripts/smoke_client.py` | MCP `list_tools()` smoke |
| `.venv/Scripts/python.exe scripts/smoke_client.py --call --max-iterations 1 --call-timeout 180` | MCP end-to-end smoke |
| `.venv/Scripts/python.exe -m blocksnet_mcp.server` | Точка входа stdio MCP-сервера |

`.python310/`, `.venv/`, `.pytest_cache/`, `__pycache__/`, `outputs/`, `data/*.gpkg`,
`data/*.pickle`, `data/*.geojson`, `data/platform/` игнорируются Git. В индексе остаются
`data/README.md`, `data/service_type.json`, `data/archetypes.csv`.

## Тесты

Целевая папка `tests/` проверяет не поведение всей LLM-системы, а контракт MCP-обертки.

| Путь | Роль |
|---|---|
| `tests/README.md` | Индекс тестов |
| `tests/test_serialize.py` | Проверка `AgentResult -> JSON` |
| `tests/test_tool_contract.py` | Проверка схемы входа/выхода `analyze_urban_question` |

## Потоки работ

| Задача | Минимальный контекст |
|---|---|
| Понять продукт | `README.md`, `docs/overview_and_concept.md`, `docs/WIKI-LLM.md` |
| Реализовать MCP-сервер | `docs/architecture.md`, `docs/tool_contract.md`, `blocksnet_mcp/README.md` |
| Настроить локальный запуск | `README.md`, `docs/deployment.md`, `data/README.md`, `.env.example` |
| Перенести ядро агента | `docs/mcp_repository_plan.md`, `blocksnet_agent/README.md`, соседний `blocksnet-agent/` |
| Написать сериализацию | `docs/tool_contract.md`, `blocksnet_mcp/README.md`, `blocksnet_mcp/serialize.py` |
| Добавить тесты | `docs/tool_contract.md`, `tests/README.md` |
| Проверить MCP stdio | `docs/plans/test_plan.md`, `scripts/README.md`, `scripts/smoke_client.py` |
| Посмотреть результаты тестирования | `docs/reports/test_report_20260618.md`, `outputs/run_*/run_log.json` |
| Визуализировать тесты | `examples/test_visualization.ipynb`, `docs/reports/test_report_20260618.md`, `outputs/run_*` |
| Готовить MAS-интеграцию | `docs/mas_integration_reference.md`, `docs/mas_registration.md` |

## Границы источников

- Источник истины по текущему локальному MVP: `README.md`, `docs/overview_and_concept.md`,
  `docs/architecture.md`, `docs/tool_contract.md`, `docs/deployment.md`.
- Источник истины по плану переноса: `docs/mcp_repository_plan.md`.
- Источник истины по будущему MAS/UrbanDB: `docs/mas_integration_reference.md`,
  `docs/mas_registration.md`.
- Источник истины по поведению агента после переноса: код `blocksnet_agent/` и upstream
  `blocksnet-agent`.
- Источник истины по локальному окружению: `.venv/Scripts/python.exe`, `requirements.txt`,
  `docs/deployment.md`.
- Future-документы не должны менять текущий контракт локального MVP без явной задачи.
