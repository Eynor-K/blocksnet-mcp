from __future__ import annotations

from difflib import SequenceMatcher, get_close_matches

import pandas as pd
from langchain_core.tools import tool
from blocksnet.analysis.provision import competitive_provision, provision_strong_total, provision_weak_total, shared_provision
from blocksnet.config.service_types.config import SERVICE_TYPES
from blocksnet.enums import LandUse

from blocksnet_agent.runtime import record_file
from blocksnet_agent.tools.data import ensure_acc_mx, ensure_blocks
from blocksnet_agent.tools.optimize import _SERVICE_PRESETS
from blocksnet_agent.tools.viz import save_metric_map


def _service_df(state: dict, data_dir, service_type: str) -> pd.DataFrame | str:
    blocks = ensure_blocks(state, data_dir)
    ensure_acc_mx(state, data_dir)
    cap_col = f"capacity_{service_type}"
    if cap_col not in blocks.columns:
        return _unknown_service_message(blocks, service_type)
    service_df = blocks[["population", cap_col]].copy()
    service_df = service_df.rename(columns={cap_col: "capacity"}).fillna(0)
    service_df["capacity"] = service_df["capacity"].astype(int)
    service_df["population"] = service_df["population"].astype(int)
    return service_df


def _resolve_single_service_type(service_type: str, blocks: pd.DataFrame, data_dir) -> str:
    available = _available_services(blocks)
    requested = str(service_type).strip()
    if requested in available:
        return requested
    try:
        from blocksnet_agent.tools.data import resolve_service_name

        resolved, _ranked = resolve_service_name(requested, data_dir, available)
        if resolved:
            return resolved
    except Exception:
        pass
    return requested


def _available_services(blocks: pd.DataFrame) -> list[str]:
    return sorted(c.replace("capacity_", "", 1) for c in blocks.columns if c.startswith("capacity_"))


def _unknown_service_message(blocks: pd.DataFrame, service_type: str) -> str:
    available = _available_services(blocks)
    lowered = str(service_type).strip().lower()
    presets = {"basic", "advanced", "comfort", "key"}
    land_uses = {item.name.lower() for item in LandUse}
    land_uses.update(str(item.value).lower() for item in LandUse)
    ranked = _rank_service_matches(str(service_type), available)
    close = [name for name, _score in ranked[:3]]
    examples = ", ".join(available[:20]) + (" ..." if len(available) > 20 else "")

    if lowered in presets:
        hint = (
            f"'{service_type}' похоже на пресет набора сервисов, а не на service_type. "
            "Для ключевых сервисов вызови list_key_services() и передай конкретное имя сервиса."
        )
    elif lowered in land_uses:
        hint = (
            f"'{service_type}' похоже на тип землепользования, а не на service_type. "
            "Для provision используй сервис из list_key_services() или list_service_types()."
        )
    else:
        hint = "Используй точное имя сервиса из list_service_types() или list_key_services()."

    suggestions = ""
    if ranked and ranked[0][1] >= 0.55:
        suggestions = (
            f" Ближайшее валидное имя: '{ranked[0][0]}' "
            f"(similarity={ranked[0][1]:.2f}). Повтори вызов с service_type='{ranked[0][0]}'."
        )
    elif close:
        suggestions = f" Ближайшие допустимые сервисы: {', '.join(close)}."
    return (
        f"Ошибка: тип сервиса '{service_type}' не найден. {hint}{suggestions}\n"
        f"Допустимые сервисы: {examples}"
    )


def _rank_service_matches(service_type: str, available: list[str]) -> list[tuple[str, float]]:
    query = str(service_type).strip().lower()
    if not query:
        return []
    # Единый data-driven резолвер: имена и синонимы берутся из каталога service_type.json
    # (name + name_ru + keywords) и data/service_aliases.json — без хардкода алиасов в коде.
    try:
        from blocksnet_agent.config import get_settings
        from blocksnet_agent.tools.data import rank_service_candidates

        ranked = rank_service_candidates(service_type, get_settings().data_dir, available)
        if ranked:
            return ranked
    except Exception:
        pass
    # Fallback (каталог недоступен): сопоставление только по каноническим именам.
    close = get_close_matches(query, available, n=5, cutoff=0.0)
    ranked = [
        (name, SequenceMatcher(None, query, name.lower()).ratio())
        for name in (close or available)
    ]
    return sorted(ranked, key=lambda item: item[1], reverse=True)


# T2: единая метка, отличающая общегородской агрегат от поквартального значения.
_AGG_NOTE = (
    "\n[это агрегат по городу, НЕ значение отдельного квартала; "
    "поквартально — get_block_info(block_id) или get_metric_for_block(result_key, block_id)]"
)


def _robust_summary(series: pd.Series) -> str:
    """D3: устойчивая сводка (медиана/перцентили/доля нулей), без опоры на неинформативное среднее."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return "нет числовых значений"
    zero_share = 100.0 * float((values <= 0).mean())
    return (
        f"кварталов: {len(values)}; медиана: {values.median():.3f}; "
        f"p25: {values.quantile(0.25):.3f}; p75: {values.quantile(0.75):.3f}; "
        f"макс: {values.max():.3f}; доля кварталов без обеспеченности: {zero_share:.0f}%"
    )


def _skew_note(series: pd.Series) -> str:
    """T2.3: предупреждает, когда среднее неинформативно из-за сильного перекоса/выбросов."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < 3:
        return ""
    mean = float(values.mean())
    median = float(values.median())
    std = float(values.std())
    skewed = (std > 3 * abs(mean) and std > 0) or (median == 0 and mean != 0) or (abs(mean) > 3 * abs(median) and median != 0)
    if skewed:
        return (
            f"\n⚠ Распределение сильно скошено (mean={mean:.2f}, median={median:.2f}, std={std:.2f}): "
            "опирайся на медиану/перцентили и долю кварталов, а не на среднее — среднее здесь неинформативно."
        )
    return ""


def _provision_column(df: pd.DataFrame) -> str:
    for col in ("provision_strong", "provision", "provision_weak"):
        if col in df.columns:
            return col
    numeric_cols = df.select_dtypes(include="number").columns
    return numeric_cols[-1]


def _service_demand(service_type: str) -> int | None:
    try:
        if service_type in SERVICE_TYPES.index:
            demand = SERVICE_TYPES.loc[service_type, "demand"]
            return int(demand) if pd.notna(demand) else None
    except Exception:
        return None
    return None


def _service_accessibility(service_type: str, fallback: int) -> int:
    try:
        if service_type in SERVICE_TYPES.index:
            value = SERVICE_TYPES.loc[service_type, "accessibility"]
            return int(value) if pd.notna(value) else int(fallback)
    except Exception:
        return int(fallback)
    return int(fallback)


def _preset_services(preset: str, blocks: pd.DataFrame) -> list[str]:
    available = set(_available_services(blocks))
    lowered = str(preset).strip().lower()
    if lowered == "key":
        return sorted(name for name in available if name in set(SERVICE_TYPES.index))
    if lowered in _SERVICE_PRESETS:
        return [name for name in _SERVICE_PRESETS[lowered] if name in available]
    return []


def _compute_single_service_provision(
    state: dict,
    data_dir,
    output_dir,
    service_type: str,
    accessibility_minutes: int,
    max_depth: int,
    save_artifacts: bool = True,
) -> dict:
    service_df = _service_df(state, data_dir, service_type)
    if isinstance(service_df, str):
        raise ValueError(service_df)
    demand = _service_demand(service_type)
    result = competitive_provision(
        service_df,
        state["acc_mx"],
        accessibility_minutes,
        demand=demand,
        max_depth=max_depth,
    )
    blocks_prov = result[0] if isinstance(result, tuple) else result
    # D1: результат ВСЕГДА кэшируем в state — иначе downstream-инструменты (suggest_target_blocks,
    # get_metric_for_block, сценарий) не найдут поквартальную обеспеченность по сервису.
    state[f"competitive_provision_{service_type}"] = blocks_prov
    # T1.3: на диск (CSV/links/карты) пишем только вне батча — батч не должен плодить десятки файлов.
    if save_artifacts:
        if isinstance(result, tuple):
            for index, item in enumerate(result[1:], start=1):
                if isinstance(item, (pd.DataFrame, pd.Series)):
                    item.to_csv(output_dir / f"competitive_provision_{service_type}_links_{index}.csv")
        csv_path = output_dir / f"competitive_provision_{service_type}.csv"
        blocks_prov.to_csv(csv_path)
        record_file(csv_path, "csv", meta={"tool": "compute_service_provision", "service_type": service_type})
        save_metric_map(
            ensure_blocks(state, data_dir),
            blocks_prov,
            f"competitive_provision_{service_type}",
            output_dir,
            f"Обеспеченность {service_type}",
        )
    strong = provision_strong_total(blocks_prov)
    weak = provision_weak_total(blocks_prov)
    col = _provision_column(blocks_prov)
    values = pd.to_numeric(blocks_prov[col], errors="coerce").fillna(0)
    return {
        "service_type": service_type,
        "accessibility_minutes": accessibility_minutes,
        "strong": float(strong),
        "weak": float(weak),
        "full": int((values >= 1).sum()),
        "partial": int(((values > 0) & (values < 1)).sum()),
        "missing": int((values <= 0).sum()),
    }


def _compute_service_batch(
    state: dict,
    data_dir,
    output_dir,
    services: list[str],
    preset_name: str,
    accessibility_minutes: int,
    max_depth: int,
) -> str:
    rows: list[dict] = []
    errors: list[str] = []
    for service in services:
        threshold = _service_accessibility(service, accessibility_minutes)
        try:
            rows.append(
                _compute_single_service_provision(
                    state, data_dir, output_dir, service, threshold, max_depth, save_artifacts=False
                )
            )
        except Exception as exc:
            errors.append(f"{service}: {exc}")

    if not rows:
        return (
            f"Ошибка: для набора '{preset_name}' не найдено доступных capacity_* сервисов."
            + (f"\nОшибки: {'; '.join(errors)}" if errors else "")
        )

    summary_df = pd.DataFrame(rows)
    csv_path = output_dir / f"competitive_provision_batch_{preset_name}.csv"
    summary_df.to_csv(csv_path, index=False)
    record_file(csv_path, "csv", meta={"tool": "compute_service_provision", "service_type": preset_name, "batch": True})

    lines = [
        f"Батч-обеспеченность набора '{preset_name}' ({len(rows)} сервисов):",
        "| service | threshold_min | strong | weak | full_blocks | partial_blocks | missing_blocks | status |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(rows, key=lambda item: item["strong"]):
        status = "слабая" if row["strong"] < 0.5 else "средняя" if row["strong"] < 0.75 else "сильная"
        lines.append(
            f"| {row['service_type']} | {row['accessibility_minutes']} | {row['strong']:.3f} | "
            f"{row['weak']:.3f} | {row['full']} | {row['partial']} | {row['missing']} | {status} |"
        )
    if errors:
        lines.append("Ошибки отдельных сервисов: " + "; ".join(errors))
    lines.append(f"Сводный CSV: {csv_path}")
    return "\n".join(lines)


def make_provision_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def compute_service_provision(service_type: str | list[str], accessibility_minutes: int = 15, max_depth: int = 1) -> str:
        """Вычисляет конкурентную обеспеченность населения сервисом (или набором key/basic/advanced/comfort).

        Параметры: service_type — конкретный сервис из list_service_types() ИЛИ пресет
        'key'/'basic'/'advanced'/'comfort' (тогда считается батч по набору, сохраняется только сводка);
        accessibility_minutes — порог доступности (по умолчанию 15); max_depth — глубина конкуренции.
        Выход: strong/weak provision (доля удовлетворённого спроса: strong — консервативная оценка,
        weak — расширенная) и число кварталов с полной/частичной/нулевой обеспеченностью.
        Подводные камни: не подменяй обеспеченность на compute_services_count (количество ≠ покрытие);
        service_type должен существовать среди capacity_*; если в list_key_services/list_service_types
        service помечен provision_available=False, demand-норматива нет и обеспеченность надо трактовать
        осторожно: лучше использовать capacity напрямую или близкий нормируемый сервис.

        Когда выбирать: чтобы оценить покрытие населения сервисом или найти кварталы с дефицитом.
        Не путать с: compute_services_count — количество объектов не равно обеспеченности спроса.
        """
        try:
            blocks = ensure_blocks(state, data_dir)
            requested = str(service_type).strip().lower() if isinstance(service_type, str) else ""
            batch_services = _preset_services(requested, blocks) if requested else []
            if batch_services:
                return _compute_service_batch(
                    state, data_dir, output_dir, batch_services, requested, accessibility_minutes, max_depth
                )
            if isinstance(service_type, list):
                return _compute_service_batch(
                    state, data_dir, output_dir, [str(item) for item in service_type], "custom", accessibility_minutes, max_depth
                )
            service_type = _resolve_single_service_type(str(service_type), blocks, data_dir)
            service_df = _service_df(state, data_dir, service_type)
            if isinstance(service_df, str):
                return service_df
            summary = _compute_single_service_provision(
                state, data_dir, output_dir, str(service_type), int(accessibility_minutes), int(max_depth)
            )
            return (
                f"Обеспеченность сервисом '{summary['service_type']}' (порог {summary['accessibility_minutes']} мин):\n"
                f"Суммарная сильная обеспеченность: {summary['strong']:.3f}\n"
                f"Суммарная слабая обеспеченность: {summary['weak']:.3f}\n"
                f"Полная обеспеченность: {summary['full']} кварталов, "
                f"частичная: {summary['partial']}, отсутствует: {summary['missing']}."
                + _AGG_NOTE
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_shared_provision(service_type: str | list[str], accessibility_minutes: int = 15) -> str:
        """Вычисляет совместную обеспеченность населения сервисом в заданном пороге доступности.

        Распределение обычно сильно скошено (у многих кварталов 0): опирайся на медиану, перцентили
        и долю кварталов без обеспеченности, а не на среднее. service_type — конкретный сервис или 'key'.
        """
        try:
            if service_type == "key":
                blocks = ensure_blocks(state, data_dir)
                service_type = [name for name in _available_services(blocks) if name in set(SERVICE_TYPES.index)][:6]
            if isinstance(service_type, list):
                return "\n\n".join(
                    compute_shared_provision.invoke({"service_type": item, "accessibility_minutes": accessibility_minutes})
                    for item in service_type
                )
            service_df = _service_df(state, data_dir, service_type)
            if isinstance(service_df, str):
                return service_df
            result = shared_provision(service_df, state["acc_mx"], accessibility_minutes)
            result_df = result[0] if isinstance(result, tuple) else result
            csv_path = output_dir / f"shared_provision_{service_type}.csv"
            result_df.to_csv(csv_path)
            record_file(csv_path, "csv", meta={"tool": "compute_shared_provision", "service_type": service_type})
            state[f"shared_provision_{service_type}"] = result_df
            save_metric_map(ensure_blocks(state, data_dir), result_df, f"shared_provision_{service_type}", output_dir, f"Совместная обеспеченность {service_type}")
            cols = [col for col in result_df.columns if "provision" in col.lower()]
            # D3: ведём сводку от медианы/перцентилей/доли нулей, а не от неинформативного среднего.
            if cols:
                summary = _robust_summary(result_df[cols[0]])
                note = _skew_note(result_df[cols[0]])
            else:
                summary = _robust_summary(result_df.select_dtypes(include="number").iloc[:, -1])
                note = ""
            return (
                f"Совместная обеспеченность '{service_type}' (порог {accessibility_minutes} мин):\n"
                f"{summary}{note}{_AGG_NOTE}"
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    return [compute_service_provision, compute_shared_provision]
