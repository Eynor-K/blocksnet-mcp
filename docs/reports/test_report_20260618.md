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

Фактический run (после фикса резолвинга):

| Поле | Значение |
|---|---|
| Run ID | `20260618-182405-08a76e` |
| Каталог | `outputs/run_20260618-182405-08a76e/` |
| Вопрос | `Где разместить новые спортивные площадки?` |
| Confidence | `0.45` |
| Длительность | `86 с` |
| Артефакты | `13` файлов |
| Кандидатный квартал | `409` |

Цепочка сценария: `propose_zone_development(block_ids=[409], service_set='sports')` →
`compute_scenario_provision(scenario={'409': {'pitch': 10}})`. То есть `service_set='sports'`
корректно сфокусировался на `pitch`, и измерен **именно `pitch`** (а не сторонние сервисы):

| Service | strong before→after | full before→after | partial before→after | missing before→after |
|---|---:|---:|---:|---:|
| `pitch` | `0.732 → 0.732` | `97 → 97` | `21 → 21` | `785 → 785` |

> Эффект плоский: добавление 10 ед. ёмкости в один квартал (409) не сдвинуло общегородской агрегат.
> Это содержательный результат (точечное вмешательство мало меняет город), а не ошибка — **ключевое,
> что измеряется корректный целевой сервис `pitch`**. Для заметного эффекта нужен сценарий по нескольким
> кварталам / большей ёмкости.

**До фикса (демонстрация бага), run `20260618-140741-90e191`:** на тот же вопрос измерялись
посторонние `convenience` (0.359→0.380), `kindergarten`, `school`, а `pitch` выпадал — `service_set='sports'`
молча уходил в пресет `basic`. См. раздел
[«Маршрутизация сервисов»](#маршрутизация-сервисов-регрессия-после-фикса).

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

## Маршрутизация сервисов (регрессия, после фикса)

«Верный вывод» из блокнота [`examples/test_visualization.ipynb`](../../examples/test_visualization.ipynb)
(ячейка *Service Routing Regression*) и прямая проверка хелперов ядра:

| Проверка | Вход | Проверяется | Исключено | Ожидание | Статус |
|---|---|---|---|---|---|
| `service_set` alias | `sports` | `pitch` | — | `pitch` | ✅ pass |
| Нормализация сценария | `{sports: 10, convenience: 5}` | `{pitch: 10, convenience: 5}` | — | `sports→pitch` | ✅ pass |
| Фильтр сценарной проверки | `service_set=sports` | `pitch` | `convenience` | проверяется только `pitch` | ✅ pass |

Резолвинг имён (data-driven, без хардкода): `sports→pitch` (1.00), `спортивные площадки→pitch` (0.84),
`школа→school`, `аптека→pharmacy`, `бассейн→swimming_pool`; неизвестное имя → `UnknownServiceSet`
(честная ошибка вместо тихого `basic`).

## Найденные проблемы и исправления

| Проблема | Где проявилась | Исправление |
|---|---|---|
| **`service_set='sports'` молча уходил в пресет `basic`** — для вопроса про спорт измерялись convenience/kindergarten/school, целевой `pitch` выпадал | Уровень 1 / анализ прогона | Вариант A: data-driven резолвер имён (`service_type.json`: `name`+`name_ru`+`keywords`, `data/service_aliases.json`) + нормализация сценария (`_normalize_addition_services`) + честная ошибка `UnknownServiceSet`; хардкод алиасов удалён |
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
