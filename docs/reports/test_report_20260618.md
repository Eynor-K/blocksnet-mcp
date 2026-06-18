# Отчет о тестировании `blocksnet-mcp`

Дата: 2026-06-18  
Объем: локальный MCP MVP, транспорт `stdio`, локальные данные `data/`, внешний OpenAI-compatible LLM.  
План: [`docs/plans/test_plan.md`](../plans/test_plan.md) *(локальный рабочий план, gitignored)*.

## Резюме

Локальный MVP проверен по уровням 0-2. Контрактная логика, прямой вызов агента и MCP stdio-транспорт
работают. GUI-проверка в Claude Desktop / целевом MCP-клиенте не выполнялась из этой сессии.

| Уровень | Проверка | Статус | Итог |
|---|---|---:|---|
| 0 | Unit/contract без LLM | ✅ | `22 passed` |
| 1 | Прямой вызов `analyze_urban_question` | ✅ | JSON-контракт, run logs и артефакты созданы |
| 2 | MCP stdio через `scripts/smoke_client.py` | ✅ | `list_tools`, invalid call и valid call проверены |
| 3 | Реальный GUI-клиент | ⏭️ | Не запускалось; конфиг готов |

## Окружение

| Компонент | Значение |
|---|---|
| Python | `3.10.11` |
| Интерпретатор | `.\.venv\Scripts\python.exe` |
| Зависимости | `requirements.txt` |
| LLM | `openai/gpt-4o` через OpenRouter-compatible endpoint |
| Данные | `data/blocks_with_services.gpkg`, `data/acc_mx.pickle`, `data/service_type.json`, `data/archetypes.csv` |
| MCP entrypoint | `.\.venv\Scripts\python.exe -m blocksnet_mcp.server` |

Проверка зависимостей:

```text
.\.venv\Scripts\python.exe -m pip check
No broken requirements found.
```

## Уровень 0

Команда:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Результат:

```text
22 passed in 3.51s
```

Покрытие:

| Тест | Что проверяет |
|---|---|
| `tests/test_tool_contract.py` | `question` обязателен; `max_iterations < 1` отклоняется |
| `tests/test_serialize.py` | `AgentResult -> JSON`, пустой результат, строковые `limitations`, дедупликация кварталов |
| `tests/test_serialize.py` | парсинг `before→after` и реального формата агента `service strong before→after, missing before→after` |
| `tests/test_service_resolution.py` | распознавание известных сервисов, fallback для неизвестных сервисов и поведение `service_set` |
| `tests/test_service_resolution.py` | регрессии сценария: alias `sports→pitch`, фильтрация `compute_scenario_provision` по целевому сервису, явный mismatch |

## Уровень 1

Прямой вызов без MCP:

```powershell
.\.venv\Scripts\python.exe -c "from blocksnet_mcp.tools_mcp import analyze_urban_question; import json; r=analyze_urban_question('Где разместить новые спортивные площадки?', max_iterations=5); print(json.dumps(r, ensure_ascii=False, indent=2))"
```

Фактический run:

| Поле | Значение |
|---|---|
| Run ID | `20260618-140741-90e191` |
| Каталог | `outputs/run_20260618-140741-90e191/` |
| Вопрос | `Где разместить новые спортивные площадки?` |
| Confidence | `0.55` |
| Артефакты | `16` файлов |
| Кандидатные кварталы | `2, 4, 5, 11, 12, 14, 15, 26, 27, 36` |

Измеренный эффект из сериализации:

| Service | strong before | strong after | missing before | missing after |
|---|---:|---:|---:|---:|
| `convenience` | `0.359` | `0.380` | `790` | `783` |
| `kindergarten` | `0.938` | `0.938` | `669` | `668` |
| `school` | `0.978` | `0.978` | `627` | `627` |

Ключевые артефакты:

| Файл | Тип |
|---|---|
| `services_density.csv` | CSV |
| `population_centrality.csv` | CSV |
| `competitive_provision_pitch.csv` | CSV |
| `solution_services.csv` | CSV |
| `solution_area.csv` | CSV |
| `delta_demand.csv` | CSV |
| `maps/services_density.png` | PNG |
| `maps/population_centrality.png` | PNG |
| `maps/competitive_provision_pitch.png` | PNG |
| `maps/proposed_services_count.png` | PNG |

## Уровень 2

Smoke-клиент: [`scripts/smoke_client.py`](../../scripts/smoke_client.py).

### `list_tools`

Команда:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_client.py
```

Результат:

```text
tools: analyze_urban_question
inputSchema.required: question
max_iterations: integer|null
```

### Невалидный вызов

Команда:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --invalid --call-timeout 20
```

Результат:

```text
isError: true
Error executing tool analyze_urban_question: question must be a non-empty string
```

### Валидный вызов

Команда:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --max-iterations 1 --call-timeout 180
```

Фактический run:

| Поле | Значение |
|---|---|
| Run ID | `20260618-151424-01760d` |
| Каталог | `outputs/run_20260618-151424-01760d/` |
| MCP result | `isError=false`, есть `structuredContent` |
| Confidence | `0.45` |
| Артефакты | `7` файлов |

Артефакты MCP smoke:

| Файл | Тип |
|---|---|
| `mean_accessibility.csv` | CSV |
| `services_density.csv` | CSV |
| `services_centrality.csv` | CSV |
| `services_collocation.csv` | CSV |
| `maps/mean_accessibility.png` | PNG |
| `maps/services_density.png` | PNG |
| `maps/services_centrality.png` | PNG |

## Найденные проблемы и исправления

| Проблема | Где проявилась | Исправление |
|---|---|---|
| `blocksnet_agent.Settings` падал на дополнительных `.env` полях `MAX_ITERATIONS` / `OUTPUT_DIR` | Уровень 1 | В `blocksnet_agent/config.py` добавлено `extra="ignore"` |
| Сериализация не доставала список кварталов из текста вида `[2, 4, ...]` | Уровень 1 | В `blocksnet_mcp/serialize.py` добавлен парсинг bracket-list |
| Сериализация не доставала `service strong 0.359→0.380, missing 790→783` | Уровень 1 | В `blocksnet_mcp/serialize.py` добавлен парсинг service before/after |
| MCP valid call зависал на lazy-import агента внутри tool-call | Уровень 2 | Импорт `BlocksNetAgent` перенесен на startup `blocksnet_mcp/tools_mcp.py` |
| Ручной MCP smoke был не воспроизводим без Inspector | Уровень 2 | Добавлен `scripts/smoke_client.py` |

## Ограничения

- Уровень 3 в Claude Desktop / другом GUI-клиенте не запускался.
- `max_iterations=1` подходит для smoke, но часто дает неполные аналитические выводы и `inconclusive` гипотезы.
- Смысловой прогон с `max_iterations=5` занимает около двух минут и зависит от внешнего LLM.
- `outputs/` gitignored; run logs и карты доступны локально, но не являются версионируемыми артефактами.

## Команды для повторения

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts\smoke_client.py
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --invalid --call-timeout 20
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --max-iterations 1 --call-timeout 180
```

## Вывод

Локальный `blocksnet-mcp` готов к использованию как stdio MCP-сервер в локальном клиенте. Для рабочего
аналитического результата следует использовать `max_iterations >= 5` и таймаут клиента не меньше
нескольких минут.
