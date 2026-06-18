# Индекс `blocksnet_mcp/`

Назначение: тонкий MCP-слой поверх `BlocksNetAgent`.

Эта папка содержит код локального MCP-сервера. Обертка не реализует городскую аналитику и не
переписывает рассуждение агента; она только принимает MCP-вызов, запускает `BlocksNetAgent.run(...)` и
сериализует результат в JSON.

## Файлы

| Файл | Ответственность |
|---|---|
| `__init__.py` | Объявление пакета |
| `server.py` | FastMCP-приложение, транспорт `stdio`, регистрация инструментов |
| `tools_mcp.py` | MCP-инструмент `analyze_urban_question(question, max_iterations?)` |
| `serialize.py` | `AgentResult -> JSON` по контракту `docs/tool_contract.md` |
| `settings.py` | Настройки окружения: `DATA_DIR`, `CHAT_URL`, `API_KEY`, `MODEL`, лимиты |

## Минимальный поток

```text
server.py
  -> tools_mcp.analyze_urban_question
  -> BlocksNetAgent(model, data_dir=DATA_DIR).run(question)
  -> serialize.to_json(result)
```

## Локальный запуск

```powershell
.\.venv\Scripts\python.exe -m blocksnet_mcp.server
```

Для MCP-клиента указывать `command` на `.venv/Scripts/python.exe`, чтобы не зависеть от системного
`PATH` и случайной версии Python.

## Границы

- Не добавлять сюда доменные расчеты `blocksnet`; они остаются в `blocksnet_agent/tools/`.
- Не добавлять UrbanDB-адаптер в локальный MVP; это future-слой.
- Не возвращать только текстовый ответ агента: контракт MCP требует отдельные JSON-поля для гипотез,
  измеренных эффектов, блоков-рекомендаций, confidence, limitations и artifacts.
