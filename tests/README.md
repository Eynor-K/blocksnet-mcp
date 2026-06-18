# Индекс `tests/`

Назначение: контрактные тесты локального MCP MVP.

Тесты проверяют тонкий слой обертки, а не качество LLM-рассуждения. Для поведения агента источник
истины находится в `blocksnet_agent/` и upstream-проекте `blocksnet-agent`.

## Тесты

| Файл | Что проверяет |
|---|---|
| `test_serialize.py` | Преобразование `AgentResult` в JSON-контракт |
| `test_tool_contract.py` | Входные параметры и обязательные поля выхода `analyze_urban_question` |

## Запуск

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

Текущий результат в локальном окружении Python `3.10.11`: `7 passed`.
После расширения кейсов сериализации: `7 passed`.

## Минимальные проверки

- `question` обязателен и остается в ответе.
- `analysis_plan`, `result`, `hypotheses`, `confidence`, `limitations`, `artifacts`, `run_id` имеют
  ожидаемые типы.
- `hypotheses[].status` ограничен значениями `supported`, `refuted`, `inconclusive`.
- Сериализация не требует парсинга длинного нарратива потребителем.
