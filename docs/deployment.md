# Локальный запуск

Проект работает **локально, на ваших данных**. Сетевой деплой (ИТМО, Docker, Bearer-авторизация) —
🕘 Future, вне текущего объёма (см. §4).

---

## 1. Конфигурация (`.env`)

```env
CHAT_URL=https://openrouter.ai/api/v1   # OpenAI-совместимый API (OpenRouter / lite-llm / локальный)
API_KEY=sk-...                          # ключ LLM
MODEL=openai/gpt-4o                      # модель агента
DATA_DIR=./data                          # ваша локальная модель города
```

| Переменная | Назначение |
|---|---|
| `CHAT_URL` / `API_KEY` / `MODEL` | подключение к внешнему LLM (наследуется от агента) |
| `DATA_DIR` | каталог локальной модели (`blocks_with_services.gpkg`, `acc_mx.pickle`, нормативы) |

> Сетевых переменных (`MCP_BEARER_TOKEN`, `MCP_PORT`, `URBAN_API_URL`) в локальном режиме **не нужно**.

## 2. Установка и запуск

В этом рабочем каталоге окружение уже собрано на Python `3.10.11`:

| Путь | Назначение |
|---|---|
| `.python310/` | portable Python 3.10.11, локальный runtime |
| `.venv/` | виртуальное окружение с зависимостями из `requirements.txt` |

```bash
.\.venv\Scripts\python.exe -V
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests
copy .env.example .env            # заполнить CHAT_URL/API_KEY/MODEL
```

Сервер запускается **по stdio** — его поднимает MCP-клиент как подпроцесс (отдельно «слушать порт»
не требуется). Точка входа:

```powershell
.\.venv\Scripts\python.exe -m blocksnet_mcp.server
```

## 3. Подключение локального MCP-клиента

Пример конфигурации клиента (формат Claude Desktop / совместимых):

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

После подключения клиент видит инструмент `analyze_urban_question`; вызов:
```jsonc
{ "question": "Где разместить спортивные площадки?" }
```

## 4. Эксплуатационные заметки

- **Версия `blocksnet`:** зафиксировать `blocksnet==1.0.0a9` в `requirements.txt` — расчёты чувствительны к версии.
- **Python:** использовать `3.10.11`; окружение `.venv/` собрано именно на этой версии.
- **Таймаут:** прогон агента долгий (60–120 с, с TPE дольше) — увеличить таймаут MCP-клиента, при необходимости снизить `max_iterations`.
- **Артефакты:** CSV и картограммы пишутся локально в `outputs/run_*`.
- **Git:** `.python310/`, `.venv/`, `outputs/`, кэши и крупные данные `data/*.gpkg`, `data/*.pickle`, `data/platform/` игнорируются.
- **GPU не нужен** (LLM внешний по `CHAT_URL`); ресурсы определяются geopandas + TPE-оптимизацией.

## 5. 🕘 Future (вне текущего объёма)

Понадобится только при выходе на MAS-платформу:
- HTTP-транспорт + `Bearer`-авторизация + Docker (`Dockerfile`/`compose`), деплой в сети ИТМО, адрес `http://10.32.1.X:PORT/mcp`;
- адаптер данных `scenario_id` → UrbanDB;
- регистрация в реестре «Urban services» — локальная gitignored-справка `docs/mas_registration.md`.
