from __future__ import annotations

import json
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from langchain_core.tools import tool
from blocksnet.enums import LandUse


def _shape(value) -> str:
    if hasattr(value, "shape"):
        return str(value.shape)
    if hasattr(value, "number_of_nodes") and hasattr(value, "number_of_edges"):
        return f"граф ({value.number_of_nodes()} узлов, {value.number_of_edges()} ребер)"
    return type(value).__name__


def _service_columns(blocks: pd.DataFrame) -> list[str]:
    return sorted(c.replace("capacity_", "", 1) for c in blocks.columns if c.startswith("capacity_"))


def _metadata_by_name(data_dir) -> dict[str, dict]:
    return {str(item.get("name", "")).strip(): item for item in _read_service_type_metadata(data_dir)}


def _provision_available(item: dict | None) -> bool:
    if not item:
        return False
    demand = item.get("demand")
    accessibility = item.get("accessibility")
    return pd.notna(demand) and pd.notna(accessibility)


def _read_service_type_metadata(data_dir) -> list[dict]:
    """Читает нормативы сервисов: сначала из data/service_type.json, иначе из конфига blocksnet."""
    path = data_dir / "service_type.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            return data
    return _service_type_metadata_from_blocksnet()


def _service_type_metadata_from_blocksnet() -> list[dict]:
    """Fallback: достаёт demand/accessibility из blocksnet.config.service_types."""
    try:
        from blocksnet.config.service_types.config import SERVICE_TYPES
    except Exception:
        return []
    rows: list[dict] = []
    for name, row in SERVICE_TYPES.iterrows():
        rows.append(
            {
                "name": str(name),
                "name_ru": str(row.get("name", name)) if hasattr(row, "get") else str(name),
                "demand": row.get("demand") if hasattr(row, "get") else None,
                "accessibility": row.get("accessibility") if hasattr(row, "get") else None,
            }
        )
    return rows


# --- Резолвер имён сервисов (data-driven, без хардкода алиасов в коде) ------------
#
# Имя сервиса определяется из каталога data/service_type.json: совпадение по
# каноническому `name`, по `name_ru` и по необязательным `keywords` (если заданы в
# каталоге), плюс из необязательного data/service_aliases.json. Никаких per-service
# таблиц в коде — система сама сопоставляет ввод с известными именами по данным.


@lru_cache(maxsize=8)
def _service_aliases(data_dir_str: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Загружает необязательный data/service_aliases.json: {canonical: [синонимы]}."""
    path = Path(data_dir_str) / "service_aliases.json"
    if not path.exists():
        return ()
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return ()
    if not isinstance(data, dict):
        return ()
    items: list[tuple[str, tuple[str, ...]]] = []
    for canon, terms in data.items():
        if not isinstance(terms, list):
            continue
        cleaned = tuple(str(term).strip().lower() for term in terms if str(term).strip())
        if cleaned:
            items.append((str(canon), cleaned))
    return tuple(items)


def _service_label_index(data_dir) -> dict[str, set[str]]:
    """Строит {canonical_name: {поисковые метки}} из каталога и алиасов."""
    catalog = _read_service_type_metadata(Path(data_dir))
    index: dict[str, set[str]] = {}
    for item in catalog:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        labels = {name.lower()}
        name_ru = str(item.get("name_ru", "")).strip().lower()
        if name_ru:
            labels.add(name_ru)
        for keyword in item.get("keywords") or []:
            text = str(keyword).strip().lower()
            if text:
                labels.add(text)
        index.setdefault(name, set()).update(labels)
    for canon, terms in _service_aliases(str(data_dir)):
        index.setdefault(canon, set()).update(terms)
    return index


def rank_service_candidates(query: str, data_dir, available=None) -> list[tuple[str, float]]:
    """Ранжирует канонические имена сервисов по близости к запросу (0..1)."""
    text = str(query).strip().lower()
    if not text:
        return []
    query_tokens = set(text.replace("_", " ").split())
    available_set = {str(name).lower() for name in available} if available is not None else None
    scored: dict[str, float] = {}
    for canon, labels in _service_label_index(data_dir).items():
        if available_set is not None and canon.lower() not in available_set:
            continue
        best = 0.0
        for label in labels:
            ratio = SequenceMatcher(None, text, label).ratio()
            label_tokens = set(label.replace("_", " ").split())
            if query_tokens & label_tokens:
                ratio = max(ratio, len(query_tokens & label_tokens) / max(len(query_tokens), len(label_tokens)))
            if ratio > best:
                best = ratio
        if best > 0:
            scored[canon] = round(best, 3)
    return sorted(scored.items(), key=lambda item: item[1], reverse=True)


def resolve_service_name(query: str, data_dir, available=None, cutoff: float = 0.72):
    """Возвращает (canonical|None, ranked). Точное имя из available — сразу; иначе фаззи."""
    text = str(query).strip().lower()
    available_set = {str(name).lower() for name in available} if available is not None else set()
    if text and text in available_set:
        return text, []
    ranked = rank_service_candidates(query, data_dir, available)
    if ranked and ranked[0][1] >= cutoff:
        return ranked[0][0], ranked
    return None, ranked


def _parse_land_use(value):
    if isinstance(value, LandUse):
        return value
    if isinstance(value, str):
        key = value.split(".")[-1].upper()
        try:
            return LandUse[key]
        except KeyError:
            return value
    return value


def ensure_blocks(state: dict, data_dir):
    """Возвращает кварталы из кэша, загружая их при первом обращении."""
    if "blocks" not in state:
        blocks = gpd.read_file(data_dir / "blocks_with_services.gpkg")
        if "land_use" in blocks.columns:
            blocks["land_use"] = blocks["land_use"].apply(_parse_land_use)
        blocks["site_area"] = blocks.geometry.area
        state["blocks"] = blocks
    return state["blocks"]


def ensure_acc_mx(state: dict, data_dir):
    """Возвращает матрицу доступности из кэша, загружая её при первом обращении."""
    if "acc_mx" not in state:
        acc_mx = pd.read_pickle(data_dir / "acc_mx.pickle")
        state["acc_mx"] = acc_mx.astype("float64")
    return state["acc_mx"]


def make_data_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def load_blocks() -> str:
        """Загружает GeoDataFrame кварталов с сервисами из data/blocks_with_services.gpkg."""
        try:
            blocks = ensure_blocks(state, data_dir)

            land_use_counts = blocks["land_use"].value_counts(dropna=False).to_dict() if "land_use" in blocks else {}
            services = _service_columns(blocks)
            lines = [
                f"Кварталы загружены: {blocks.shape[0]} строк, {blocks.shape[1]} столбцов.",
                f"CRS: {blocks.crs}",
                f"Землепользование: {{ {', '.join(f'{str(k)}: {v}' for k, v in land_use_counts.items())} }}",
            ]
            if "population" in blocks.columns:
                population = pd.to_numeric(blocks["population"], errors="coerce").fillna(0)
                lines.append(
                    f"Население - всего: {int(population.sum())}, среднее: {population.mean():.1f}, медиана: {population.median():.1f}"
                )
            lines.append(f"Типов сервисов: {len(services)}: {', '.join(services[:10])}{' ...' if len(services) > 10 else ''}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка при загрузке кварталов: {exc}"

    @tool
    def load_accessibility_matrix() -> str:
        """Загружает предвычисленную матрицу доступности из data/acc_mx.pickle."""
        try:
            original = pd.read_pickle(data_dir / "acc_mx.pickle")
            original_dtype = original.dtypes.iloc[0]
            acc_mx = ensure_acc_mx(state, data_dir)
            values = acc_mx.to_numpy()
            flat = values[np.isfinite(values) & (values > 0)]
            return (
                f"Матрица доступности загружена: {acc_mx.shape[0]}x{acc_mx.shape[1]}, dtype={original_dtype}.\n"
                f"Время в пути (мин) - мин: {flat.min():.1f}, макс: {flat.max():.1f}, "
                f"среднее: {flat.mean():.1f}, медиана: {np.median(flat):.1f}."
            )
        except Exception as exc:
            return f"Ошибка при загрузке матрицы: {exc}"

    @tool
    def list_cached_data() -> str:
        """Возвращает список всех наборов данных в кэше агента с их размерами."""
        if not state:
            return "Кэш пуст. Загрузи данные с помощью load_blocks() и load_accessibility_matrix()."
        return "Данные в кэше:\n" + "\n".join(f"{key}: {_shape(value)}" for key, value in state.items())

    @tool
    def list_service_types() -> str:
        """Возвращает список типов сервисов из загруженных кварталов с флагом доступности provision."""
        try:
            services = _service_columns(ensure_blocks(state, data_dir))
            metadata = _metadata_by_name(data_dir)
            lines = [f"Доступные типы сервисов ({len(services)} шт.):"]
            for name in services:
                item = metadata.get(name)
                if _provision_available(item):
                    lines.append(
                        f"- {name}: provision_available=True, demand={item.get('demand')}, "
                        f"accessibility={item.get('accessibility')} мин"
                    )
                else:
                    lines.append(
                        f"- {name}: provision_available=False "
                        "(нет demand/accessibility; используй capacity напрямую или близкий нормируемый сервис)"
                    )
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def list_key_services() -> str:
        """Возвращает доступные сервисы с нормативами demand и accessibility (service_type.json / blocksnet)."""
        try:
            available = set(_service_columns(ensure_blocks(state, data_dir)))
            rows = []
            for item in _read_service_type_metadata(data_dir):
                name = str(item.get("name", "")).strip()
                if not name or name not in available:
                    continue
                demand = item.get("demand")
                accessibility = item.get("accessibility")
                name_ru = item.get("name_ru") or name
                rows.append((name, name_ru, demand, accessibility))
            rows.sort(key=lambda row: row[0])
            if not rows:
                return "Сервисы с нормативами не найдены. Проверь service_type.json и capacity_* в кварталах."
            lines = ["Ключевые сервисы с нормативами (только доступные в модели):"]
            for name, name_ru, demand, accessibility in rows:
                available_provision = pd.notna(demand) and pd.notna(accessibility)
                suffix = (
                    "provision_available=True"
                    if available_provision
                    else "provision_available=False (нет demand/accessibility; используй capacity напрямую или другой сервис)"
                )
                lines.append(
                    f"- {name} ({name_ru}): demand={demand}, accessibility={accessibility} мин, {suffix}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def get_block_info(block_id: int) -> str:
        """Возвращает атрибуты квартала И поквартальные значения всех уже вычисленных метрик из кэша.

        Когда выбирать: когда в задаче назван конкретный block_id или нужно заземлить вывод по кварталу.
        Не путать с: get_analysis_results — он показывает городскую сводку и первые строки, а не
        обязательную детализацию для одного block_id.
        """
        try:
            blocks = ensure_blocks(state, data_dir)
            row = blocks[blocks.index == block_id]
            if row.empty and "block_id" in blocks.columns:
                row = blocks[blocks["block_id"] == block_id]
            if row.empty:
                return f"Квартал с ID={block_id} не найден. Диапазон индекса: {blocks.index.min()}-{blocks.index.max()}."
            series = row.iloc[0].drop(labels=["geometry"], errors="ignore")
            lines = [f"Квартал ID={block_id}:"]
            for column, value in series.items():
                if pd.notna(value) and value != 0:
                    lines.append(f"  {column}: {value}")
            # Поквартальные значения ранее вычисленных метрик, чтобы заземлять выводы о квартале
            # на его собственные числа, а не на общегородские агрегаты.
            metric_lines = _block_metric_values(state, block_id)
            if metric_lines:
                lines.append("Значения вычисленных метрик для этого квартала (поквартально):")
                lines.extend(f"  {line}" for line in metric_lines)
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def get_metric_for_block(result_key: str, block_id: int) -> str:
        """Возвращает поквартальное значение метрики result_key для block_id с честной позицией в распределении.

        Когда выбирать: после compute_* или батча, если нужно процитировать значение именно для block_id.
        Не путать с: агрегатами strong/weak по городу — они не описывают конкретный квартал.
        """
        try:
            available = sorted(k for k in state if k not in ("blocks", "acc_mx"))
            if result_key not in state:
                return f"Ключ '{result_key}' не найден. Доступные: {available}"
            series = _metric_series_for_blocks(state[result_key])
            if series is None or series.empty:
                return f"Результат '{result_key}' не содержит поквартальной числовой метрики."
            if block_id not in series.index:
                return f"block_id={block_id} отсутствует в '{result_key}' (индекс {series.index.min()}–{series.index.max()})."
            value = float(series.loc[block_id])
            return (
                f"'{result_key}' для block_id {block_id}: {value:.4f} "
                f"({_value_context(series, value)}; город: мин {series.min():.4f}, макс {series.max():.4f})."
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def get_weakest_services(block_id: int, k: int = 5) -> str:
        """Возвращает K слабейших сервисов квартала по уже вычисленной competitive_provision_*.

        Сначала вычисли нужные сервисы через compute_service_provision(service_type или preset).
        Если кэша ещё нет, инструмент сам считает компактный preset 'key' без артефактов и затем
        ранжирует поквартальные значения для block_id с контекстом распределения по городу.

        Когда выбирать: когда вопрос спрашивает, чего не хватает в конкретном квартале.
        Не путать с: get_analysis_results по городскому батчу — он не даёт дефициты именно block_id.
        """
        try:
            k = max(1, min(int(k), 20))
            computed_note = _ensure_default_provision_cache(state, data_dir, output_dir)
            rows: list[tuple[str, float, str]] = []
            for key, value in state.items():
                if not str(key).startswith("competitive_provision_"):
                    continue
                service = str(key).removeprefix("competitive_provision_")
                series = _metric_series_for_blocks(value)
                if series is None or series.empty or block_id not in series.index:
                    continue
                provision = float(series.loc[block_id])
                rows.append((service, provision, _value_context(series, provision)))
            if not rows:
                cached = sorted(key for key in state if str(key).startswith("competitive_provision_"))
                return (
                    f"Для block_id {block_id} нет кэшированных competitive_provision_* значений. "
                    "Сначала вызови compute_service_provision для нужных сервисов или пресета. "
                    f"Сейчас в кэше: {cached}"
                )
            rows.sort(key=lambda row: row[1])
            lines = [f"Слабейшие сервисы block_id {block_id} по поквартальной обеспеченности:"]
            if computed_note:
                lines.append(computed_note)
            for service, provision, context in rows[:k]:
                deficit_note = "0.0 = дефицит" if provision <= 0 else "ниже лучше проверять по контексту города"
                lines.append(f"- {service}: {provision:.4f} ({context}; {deficit_note})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def get_analysis_results(result_key: str) -> str:
        """Извлекает из кэша краткую сводку ранее вычисленного результата."""
        try:
            if result_key not in state:
                return f"Ключ '{result_key}' не найден. Доступные: {list(state.keys())}"
            value = state[result_key]
            if isinstance(value, (pd.DataFrame, gpd.GeoDataFrame)):
                return (
                    f"Результат '{result_key}' ({value.shape}):\n"
                    f"Статистика:\n{value.describe().to_string()}\n\n"
                    f"Первые строки:\n{value.head(5).to_string()}"
                )
            if isinstance(value, pd.Series):
                return f"Результат '{result_key}' ({value.shape}):\n{value.describe().to_string()}\n\n{value.head(5).to_string()}"
            return f"'{result_key}': {str(value)[:500]}"
        except Exception as exc:
            return f"Ошибка: {exc}"

    return [
        load_blocks,
        load_accessibility_matrix,
        list_cached_data,
        list_service_types,
        list_key_services,
        get_block_info,
        get_metric_for_block,
        get_weakest_services,
        get_analysis_results,
    ]


def _metric_series_for_blocks(value) -> pd.Series | None:
    """Извлекает поквартальную числовую серию (индекс = block_id) из кэшированного результата."""
    if isinstance(value, pd.Series):
        return pd.to_numeric(value, errors="coerce").dropna()
    if isinstance(value, (pd.DataFrame, gpd.GeoDataFrame)):
        preferred = [
            "provision_strong", "provision", "provision_weak",
            "mean_accessibility", "median_accessibility", "max_accessibility", "accessibility",
            "services_centrality", "population_centrality", "shannon_diversity",
            "fsi", "gsi", "mxi", "osr",
        ]
        for column in preferred:
            if column in value.columns:
                return pd.to_numeric(value[column], errors="coerce").dropna()
        numeric = value.select_dtypes(include="number")
        if not numeric.empty:
            return pd.to_numeric(numeric.iloc[:, -1], errors="coerce").dropna()
    return None


def _value_context(series: pd.Series, value: float) -> str:
    """Честная позиция значения в городском распределении — без обманчивого «перцентиля снизу».

    На zero-inflated метриках (обеспеченность, где у многих кварталов 0) перцентиль «снизу» для 0.0
    давал высокое число и читался как «много», хотя 0 — это дефицит. Здесь показываем долю кварталов
    строго ниже / равных / выше и явно помечаем минимум, не навязывая направление «хорошо/плохо».
    """
    below = 100.0 * float((series < value).mean())
    equal = 100.0 * float((series == value).mean())
    above = 100.0 * float((series > value).mean())
    parts = (
        f"медиана города {series.median():.4f}; "
        f"ниже {below:.0f}%, столько же {equal:.0f}%, выше {above:.0f}% кварталов"
    )
    if value <= float(series.min()):
        parts += " — это минимум по городу"
    return parts


def _block_metric_values(state: dict, block_id: int) -> list[str]:
    """Собирает поквартальные значения всех кэшированных метрик для одного квартала."""
    lines: list[str] = []
    for key, value in state.items():
        if key in ("blocks", "acc_mx") or not isinstance(value, (pd.Series, pd.DataFrame)):
            continue
        series = _metric_series_for_blocks(value)
        if series is None or series.empty or block_id not in series.index:
            continue
        try:
            cell = float(series.loc[block_id])
            lines.append(f"{key}: {cell:.4f} ({_value_context(series, cell)})")
        except Exception:
            continue
    return lines


def _ensure_default_provision_cache(state: dict, data_dir, output_dir) -> str:
    if any(str(key).startswith("competitive_provision_") for key in state):
        return ""
    try:
        from blocksnet_agent.tools.provision import _compute_service_batch, _preset_services

        blocks = ensure_blocks(state, data_dir)
        services = _preset_services("key", blocks)[:8]
        if not services:
            return ""
        _compute_service_batch(
            state,
            data_dir,
            output_dir,
            services,
            "key",
            accessibility_minutes=15,
            max_depth=1,
        )
        return "Кэша competitive_provision_* не было; автоматически посчитан preset 'key' без карт по сервисам."
    except Exception:
        return ""
