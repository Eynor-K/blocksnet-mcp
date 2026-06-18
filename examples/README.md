# Индекс `examples/`

Назначение: интерактивные блокноты для локальной диагностики и визуализации результатов.

## Блокноты

| Файл | Роль |
|---|---|
| `test_visualization.ipynb` | Визуализация процесса тестирования `blocksnet-mcp`: уровни плана, run logs, confidence, tool calls, артефакты и карты |

## Запуск

Открывай блокнот в VS Code / Jupyter, выбрав интерпретатор:

```text
.\.venv\Scripts\python.exe
```

В `.venv` должны быть установлены notebook-зависимости из `requirements.txt`
(`ipython`, `ipykernel`, `nbformat`, `nbclient`).

Блокнот запускает актуальный `pytest` прямо из ячейки `Live pytest` и строит график результата.
Долгие LLM/MCP-вызовы выключены по умолчанию: для них нужно вручную поставить `RUN_COMMANDS = True`.
