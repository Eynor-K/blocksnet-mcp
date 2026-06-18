# Индекс `scripts/`

Назначение: локальные вспомогательные CLI для проверки `blocksnet-mcp`.

## Скрипты

| Файл | Роль |
|---|---|
| `smoke_client.py` | Минимальный MCP stdio-клиент: запускает `blocksnet_mcp.server`, проверяет `list_tools()` и опционально вызывает `analyze_urban_question` |

## Команды

```powershell
.\.venv\Scripts\python.exe scripts\smoke_client.py
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --invalid
.\.venv\Scripts\python.exe scripts\smoke_client.py --call --max-iterations 1 --call-timeout 180
```

Для полного смыслового прогона агента увеличивай `--max-iterations` до `5-10` и таймаут клиента до
нескольких минут.
