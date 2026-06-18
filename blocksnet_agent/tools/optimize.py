from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from blocksnet.enums import LandUse

from blocksnet_agent.runtime import record_file
from blocksnet_agent.tools.data import ensure_acc_mx, ensure_blocks
from blocksnet_agent.tools.viz import save_metric_map


_SERVICE_PRESETS: dict[str, dict[str, float]] = {
    "basic": {
        "school": 1.0,
        "kindergarten": 1.0,
        "polyclinic": 0.9,
        "convenience": 0.7,
        "pharmacy": 0.7,
        "pitch": 0.5,
        "bus_stop": 0.5,
    },
    "advanced": {
        "school": 1.0,
        "kindergarten": 1.0,
        "polyclinic": 0.9,
        "convenience": 0.7,
        "pharmacy": 0.7,
        "pitch": 0.6,
        "swimming_pool": 0.5,
        "extra_education": 0.5,
        "cafe": 0.4,
        "bus_stop": 0.5,
    },
    "comfort": {
        "school": 0.9,
        "kindergarten": 0.9,
        "polyclinic": 0.8,
        "convenience": 0.7,
        "pharmacy": 0.7,
        "pitch": 0.7,
        "sports_centre": 0.6,
        "extra_education": 0.6,
        "cafe": 0.5,
        "park": 0.5,
        "dog_park": 0.4,
    },
}


def make_optimize_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def suggest_target_blocks(
        criterion: str = "low_provision",
        service_type: str = "",
        land_use: str = "",
        count: int = 10,
    ) -> str:
        """Подбирает кандидаты block_id для оптимизации развития из кэша метрик или атрибутов модели.

        criterion='low_provision' требует ПРЕДВАРИТЕЛЬНО вычисленной обеспеченности по service_type
        (сначала вызови compute_service_provision(service_type)); иначе вернёт просьбу её вычислить.
        criterion='high_population' — кварталы по убыванию населения. land_use — фильтр по типу зоны.
        Если целевой квартал уже известен из задачи, передавай его напрямую в propose_zone_development;
        suggest_target_blocks нужен для поиска кандидатов, когда block_id неизвестны.
        """
        try:
            blocks = ensure_blocks(state, data_dir)
            count = max(1, min(int(count), 30))
            ids: list[int]

            # D2: честный контракт. low_provision использует ТОЛЬКО реальную обеспеченность; если её
            # нет в кэше — явно просим вычислить, а не подменяем населением под видом low_provision.
            if criterion == "low_provision":
                if not service_type:
                    return "Для criterion='low_provision' укажи service_type (например 'school')."
                resolved_service = _resolve_service_key(service_type, blocks, data_dir)
                result_key = f"competitive_provision_{resolved_service}"
                if result_key in state:
                    values = _numeric_metric(state[result_key])
                    ids = [int(i) for i in values.nsmallest(count).index]
                    return _format_target_suggestion(ids, f"lowest cached provision for {resolved_service}", values)
                return (
                    f"Обеспеченность по '{resolved_service}' ещё не вычислена — сначала вызови "
                    f"compute_service_provision('{resolved_service}'), затем повтори suggest_target_blocks."
                )

            filtered = blocks
            if land_use:
                lu = _parse_land_use(land_use)
                filtered = blocks[blocks.get("land_use").apply(lambda value: _land_use_name(value) == lu.name)]
            if filtered.empty:
                filtered = blocks

            if criterion == "high_population" and "population" in filtered.columns:
                values = pd.to_numeric(filtered["population"], errors="coerce").fillna(0)
                ids = [int(i) for i in values.nlargest(count).index]
                return _format_target_suggestion(ids, "highest population candidate blocks", values)

            ids = [int(i) for i in filtered.index[:count]]
            return _format_target_suggestion(ids, f"first {count} blocks after filters", None)
        except Exception as exc:
            return f"Ошибка подбора целевых кварталов: {exc}"

    @tool
    def propose_zone_development(
        block_ids: list[int],
        target_land_use: str = "RESIDENTIAL",
        service_set: str = "basic",
        max_time_sec: int = 300,
        max_runs: int = 1000,
        max_prov_evals: int = 100,
    ) -> str:
        """Запускает area-based TPE-оптимизацию набора сервисов зоны и возвращает гипотезу развития.

        service_set: пресет ('basic'/'advanced'/'comfort'/'key') ИЛИ конкретный сервис ('school', 'pitch', …).
        Передай КОНКРЕТНЫЙ сервис, чтобы сфокусировать оптимизацию на нём; пресет оптимизирует весь набор
        зоны и добавит не только целевой сервис. Пример фокуса: service_set='school', затем проверь
        эффект через compute_scenario_provision(service_set='school'). RESULT должен цитировать block_id,
        service_type и предложенную capacity.

        Когда выбирать: когда нужен вариант развития/добавления сервисов в уже выбранных block_id.
        Не путать с: suggest_target_blocks — он только ищет кандидатов, но не генерирует решение.
        """
        try:
            from blocksnet.optimization.services import (
                AreaSolution,
                Facade,
                GradientChooser,
                RandomOrder,
                TPEOptimizer,
                WeightedConstraints,
                WeightedObjective,
            )
        except Exception as exc:
            return f"Ошибка: blocksnet.optimization.services недоступен: {exc}"

        try:
            blocks = _ensure_site_area(ensure_blocks(state, data_dir).copy())
            acc_mx = ensure_acc_mx(state, data_dir)
            ids = _valid_block_ids(block_ids, blocks)
            if not ids:
                return "Ошибка: не найдено валидных block_ids для оптимизации."

            lu = _parse_land_use(target_land_use)
            blocks_lu = {bid: lu for bid in ids}
            try:
                weights = _available_service_weights(service_set, blocks, lu, data_dir)
            except UnknownServiceSet as exc:
                return exc.message
            if not weights:
                return f"Ошибка: для service_set='{service_set}' и land_use={lu.name} нет доступных capacity_* сервисов."

            max_time_sec = max(10, min(int(max_time_sec), 300))
            max_runs = max(1, min(int(max_runs), 1000))
            max_prov_evals = max(1, min(int(max_prov_evals), 1000))

            facade = Facade(
                var_adapter=AreaSolution(blocks_lu=blocks_lu),
                accessibility_matrix=acc_mx,
                blocks_df=blocks,
                blocks_lu=blocks_lu,
            )
            added_services: list[str] = []
            for service_type, weight in weights.items():
                service_df = _capacity_df(blocks, service_type)
                before = facade.num_params
                facade.add_service_type(service_type, float(weight), service_df)
                if facade.num_params > before:
                    added_services.append(service_type)

            if facade.num_params <= 0 or not added_services:
                return "Ошибка: оптимизатор не создал переменные. Проверь target_land_use и выбранные сервисы."

            objective = WeightedObjective(
                num_params=facade.num_params,
                facade=facade,
                max_evals=max_prov_evals,
                weights={service: weights[service] for service in added_services},
            )
            constraints = WeightedConstraints(
                facade=facade,
                num_params=facade.num_params,
                priority={service: weights[service] for service in added_services},
            )
            optimizer = TPEOptimizer(
                objective=objective,
                constraints=constraints,
                vars_order=RandomOrder(),
                vars_chooser=GradientChooser(facade=facade, num_params=facade.num_params, num_top=5),
            )
            try:
                import optuna

                optuna.logging.set_verbosity(optuna.logging.WARNING)
            except Exception:
                pass
            best_x, best_val, success_rate, evals = optimizer.run(
                max_runs=max_runs,
                timeout=max_time_sec,
                initial_runs_num=1,
                verbose=False,
            )

            services_df = facade.solution_to_services_df(best_x)
            area_df = facade.get_solution_area_df(best_x)
            delta_df = _delta_demand_df(facade, best_x)
            provisions = _safe_provisions(facade, best_x)

            paths = _save_solution_outputs(output_dir, blocks, ids, services_df, area_df, delta_df)
            state["zone_development_solution_services"] = services_df
            state["zone_development_solution_area"] = area_df
            state["zone_development_delta_demand"] = delta_df

            return _format_development_hypothesis(
                ids=ids,
                target_land_use=lu.name,
                service_set=service_set,
                added_services=added_services,
                services_df=services_df,
                area_df=area_df,
                delta_df=delta_df,
                best_val=float(best_val),
                success_rate=float(success_rate),
                evals=int(evals),
                max_time_sec=max_time_sec,
                max_prov_evals=max_prov_evals,
                provisions=provisions,
                paths=paths,
            )
        except Exception as exc:
            return f"Ошибка оптимизации зоны: {exc}"

    @tool
    def optimize_zone_services(
        block_ids: list[int],
        target_land_use: str = "RESIDENTIAL",
        service_set: str = "basic",
        max_time_sec: int = 300,
        max_runs: int = 1000,
        max_prov_evals: int = 100,
    ) -> str:
        """Алиас propose_zone_development. service_set: пресет ИЛИ конкретный сервис (для фокуса на нём)."""
        return propose_zone_development.invoke({
            "block_ids": block_ids,
            "target_land_use": target_land_use,
            "service_set": service_set,
            "max_time_sec": max_time_sec,
            "max_runs": max_runs,
            "max_prov_evals": max_prov_evals,
        })

    @tool
    def compute_scenario_provision(
        scenario: dict[str, dict[str, float]] | None = None,
        use_cached_solution: bool = True,
        service_set: str = "basic",
        accessibility_minutes: int = 15,
        max_depth: int = 1,
    ) -> str:
        """Независимо пересчитывает обеспеченность после добавления ёмкостей сценария по block_id (before→after).

        Главный инструмент проверки ЭФФЕКТА развития: даёт измеренные before→after по сервисам.
        scenario=None + use_cached_solution=True берёт последнее предложение propose_zone_development;
        либо задай scenario вручную ({block_id: {service: capacity}}). Для проверки целевого сервиса передай
        его имя в service_set (например 'school'), а не пресет; пресет пересчитывает весь набор.

        Когда выбирать: после любого предложения добавить capacity, чтобы проверить измеренный эффект.
        Не путать с: propose_zone_development — он генерирует сценарий, а этот инструмент проверяет before→after.
        """
        try:
            base_blocks = ensure_blocks(state, data_dir).copy()
            acc_mx = ensure_acc_mx(state, data_dir)
            additions = _scenario_additions(state, scenario, use_cached_solution)
            if not additions:
                return "Ошибка: сценарий пуст. Передай {block_id: {service_type: added_capacity}} или сначала запусти propose_zone_development."

            additions = _normalize_addition_services(additions, base_blocks, data_dir)
            scenario_services = sorted({service for service_map in additions.values() for service in service_map})
            services = scenario_services
            if service_set:
                try:
                    requested_services = list(_available_service_weights(service_set, base_blocks, LandUse.RESIDENTIAL, data_dir))
                except UnknownServiceSet as exc:
                    return exc.message
                if requested_services:
                    services = [service for service in scenario_services if service in requested_services]
                    if not services:
                        return (
                            f"Ошибка: service_set='{service_set}' распознан как {requested_services}, "
                            f"но сценарий содержит только {scenario_services}. "
                            "Проверь, что scenario/propose_zone_development добавляет именно целевой сервис."
                        )
            rows: list[dict[str, Any]] = []
            errors: list[str] = []
            for service in services:
                cap_col = f"capacity_{service}"
                if cap_col not in base_blocks.columns:
                    errors.append(f"{service}: нет колонки {cap_col}")
                    continue

                before_state = {"blocks": base_blocks.copy(), "acc_mx": acc_mx}
                after_blocks = base_blocks.copy()
                for block_id, service_map in additions.items():
                    if service not in service_map or block_id not in after_blocks.index:
                        continue
                    after_blocks.loc[block_id, cap_col] = (
                        pd.to_numeric(pd.Series([after_blocks.loc[block_id, cap_col]]), errors="coerce").fillna(0).iloc[0]
                        + float(service_map[service])
                    )
                after_state = {"blocks": after_blocks, "acc_mx": acc_mx}
                try:
                    from blocksnet_agent.tools.provision import _compute_single_service_provision

                    before = _compute_single_service_provision(
                        before_state, data_dir, output_dir, service,
                        _service_accessibility(service, accessibility_minutes), max_depth,
                    )
                    after = _compute_single_service_provision(
                        after_state, data_dir, output_dir, service,
                        _service_accessibility(service, accessibility_minutes), max_depth,
                    )
                    rows.append({"service_type": service, "before": before, "after": after})
                except Exception as exc:
                    errors.append(f"{service}: {exc}")

            if not rows:
                return "Ошибка: не удалось пересчитать сценарную обеспеченность. " + "; ".join(errors)

            affected_ids = sorted(additions)
            lines = [
                f"Сценарная обеспеченность для block_id {affected_ids}:",
                "| service | strong before->after | weak before->after | full before->after | partial before->after | missing before->after |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            verdict_parts = []
            for row in rows:
                before = row["before"]
                after = row["after"]
                service = row["service_type"]
                lines.append(
                    f"| {service} | {before['strong']:.3f}->{after['strong']:.3f} | "
                    f"{before['weak']:.3f}->{after['weak']:.3f} | {before['full']}->{after['full']} | "
                    f"{before['partial']}->{after['partial']} | {before['missing']}->{after['missing']} |"
                )
                verdict_parts.append(
                    f"{service} strong {before['strong']:.3f}->{after['strong']:.3f}, "
                    f"missing {before['missing']}->{after['missing']}"
                )
            lines.append(
                "HYPOTHESES: claim: сценарий добавления ёмкости в "
                f"block_id {affected_ids} улучшает обеспеченность; test: compute_scenario_provision; "
                f"verdict: supported for block_id {affected_ids}: " + "; ".join(verdict_parts) + "."
            )
            if errors:
                lines.append("Ошибки отдельных сервисов: " + "; ".join(errors))
            state["scenario_provision_last"] = rows
            return "\n".join(lines)
        except Exception as exc:
            return f"Ошибка сценарной обеспеченности: {exc}"

    return [suggest_target_blocks, propose_zone_development, optimize_zone_services, compute_scenario_provision]


def _parse_land_use(value: str) -> LandUse:
    key = str(value or "RESIDENTIAL").split(".")[-1].upper()
    return LandUse[key]


def _land_use_name(value: Any) -> str:
    if isinstance(value, LandUse):
        return value.name
    return str(value).split(".")[-1].upper()


def _ensure_site_area(blocks):
    if "geometry" not in blocks.columns:
        return blocks
    try:
        projected = blocks.to_crs(blocks.estimate_utm_crs())
        blocks["site_area"] = projected.geometry.area
    except Exception:
        blocks["site_area"] = blocks.geometry.area
    return blocks


def _valid_block_ids(block_ids: list[int], blocks) -> list[int]:
    valid = []
    for raw in block_ids or []:
        try:
            bid = int(raw)
        except (TypeError, ValueError):
            continue
        if bid in blocks.index and bid not in valid:
            valid.append(bid)
    return valid[:30]


class UnknownServiceSet(Exception):
    """service_set не распознан ни как пресет, ни как сервис (вариант A: честная ошибка вместо basic)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _service_set_error(service_set: str, available: set[str], ranked: list[tuple[str, float]]) -> str:
    presets = ", ".join(sorted(_SERVICE_PRESETS))
    if ranked and ranked[0][1] >= 0.55:
        suggestion = (
            f" Ближайшее валидное имя: '{ranked[0][0]}' (similarity={ranked[0][1]:.2f}). "
            f"Повтори вызов с service_set='{ranked[0][0]}'."
        )
    elif ranked:
        suggestion = " Ближайшие сервисы: " + ", ".join(name for name, _ in ranked[:5]) + "."
    else:
        suggestion = ""
    return (
        f"Ошибка: service_set='{service_set}' не распознан как пресет или сервис.{suggestion} "
        f"Передай каноническое имя сервиса (см. list_key_services / list_service_types) или пресет ({presets})."
    )


def _available_service_weights(service_set: str, blocks, land_use: LandUse, data_dir=None) -> dict[str, float]:
    try:
        from blocksnet.config.service_types.config import SERVICE_TYPES
    except Exception:
        SERVICE_TYPES = pd.DataFrame()

    available = {column.replace("capacity_", "") for column in blocks.columns if column.startswith("capacity_")}
    key = str(service_set).strip().lower()
    # Честный контракт (вариант A): service_set — пресет ИЛИ конкретный сервис. Раньше любой
    # нераспознанный ввод (например алиас 'sports' вместо 'pitch') молча падал в пресет 'basic',
    # и оптимизатор измерял не те сервисы. Теперь имя резолвится по данным каталога, а при неудаче
    # выбрасывается UnknownServiceSet — вызывающий инструмент вернёт честную ошибку с подсказкой.
    if key in _SERVICE_PRESETS:
        preset = _SERVICE_PRESETS[key]
    elif key in available:
        preset = {key: 1.0}
    else:
        resolved, ranked = (None, [])
        if data_dir is not None:
            from blocksnet_agent.tools.data import resolve_service_name

            resolved, ranked = resolve_service_name(service_set, data_dir, available)
        if resolved:
            preset = {resolved: 1.0}
        else:
            raise UnknownServiceSet(_service_set_error(service_set, available, ranked))
    weights: dict[str, float] = {}
    for service_type, weight in preset.items():
        if service_type not in available:
            continue
        if not SERVICE_TYPES.empty and service_type in SERVICE_TYPES.index:
            allowed = {str(item).upper() for item in SERVICE_TYPES.loc[service_type, "land_use"]}
            if land_use.name.lower() not in {item.lower() for item in allowed}:
                continue
        weights[service_type] = weight
    return weights


def _resolve_service_key(service_type: str, blocks, data_dir=None) -> str:
    available = {column.replace("capacity_", "") for column in blocks.columns if column.startswith("capacity_")}
    key = str(service_type).strip().lower()
    if key in available:
        return key
    if data_dir is not None:
        try:
            from blocksnet_agent.tools.data import resolve_service_name

            resolved, _ranked = resolve_service_name(service_type, data_dir, available)
            if resolved:
                return resolved
        except Exception:
            pass
    return key


def _normalize_addition_services(additions: dict[int, dict[str, float]], blocks, data_dir=None) -> dict[int, dict[str, float]]:
    normalized: dict[int, dict[str, float]] = {}
    for block_id, service_map in additions.items():
        for service, capacity in service_map.items():
            resolved = _resolve_service_key(service, blocks, data_dir)
            normalized.setdefault(block_id, {})
            normalized[block_id][resolved] = normalized[block_id].get(resolved, 0.0) + float(capacity)
    return {block_id: service_map for block_id, service_map in normalized.items() if service_map}


def _capacity_df(blocks, service_type: str) -> pd.DataFrame:
    cap_col = f"capacity_{service_type}"
    df = blocks[[cap_col]].rename(columns={cap_col: "capacity"}).copy()
    df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce").fillna(0).astype(int)
    return df


def _service_accessibility(service_type: str, fallback: int) -> int:
    try:
        from blocksnet.config.service_types.config import SERVICE_TYPES

        if service_type in SERVICE_TYPES.index:
            value = SERVICE_TYPES.loc[service_type, "accessibility"]
            return int(value) if pd.notna(value) else int(fallback)
    except Exception:
        pass
    return int(fallback)


def _scenario_additions(
    state: dict,
    scenario: dict[str, dict[str, float]] | None,
    use_cached_solution: bool,
) -> dict[int, dict[str, float]]:
    additions: dict[int, dict[str, float]] = {}
    if scenario:
        for raw_block_id, service_map in scenario.items():
            try:
                block_id = int(raw_block_id)
            except (TypeError, ValueError):
                continue
            additions.setdefault(block_id, {})
            for service, raw_capacity in (service_map or {}).items():
                try:
                    capacity = float(raw_capacity)
                except (TypeError, ValueError):
                    continue
                if capacity > 0:
                    additions[block_id][str(service)] = additions[block_id].get(str(service), 0.0) + capacity
    if additions or not use_cached_solution:
        return {block_id: values for block_id, values in additions.items() if values}

    services_df = state.get("zone_development_solution_services")
    if not isinstance(services_df, pd.DataFrame) or services_df.empty:
        return {}
    for _idx, row in services_df.iterrows():
        try:
            block_id = int(row["block_id"])
            service = str(row["service_type"])
            capacity = float(row.get("capacity", row.get("count", 0)))
        except (KeyError, TypeError, ValueError):
            continue
        if capacity <= 0:
            continue
        additions.setdefault(block_id, {})
        additions[block_id][service] = additions[block_id].get(service, 0.0) + capacity
    return additions


def _numeric_metric(metric) -> pd.Series:
    if isinstance(metric, pd.Series):
        return pd.to_numeric(metric, errors="coerce").dropna()
    if isinstance(metric, pd.DataFrame):
        preferred = ["provision", "provision_strong", "provision_weak"]
        for column in preferred:
            if column in metric.columns:
                return pd.to_numeric(metric[column], errors="coerce").dropna()
        numeric = metric.select_dtypes(include="number")
        if not numeric.empty:
            return pd.to_numeric(numeric.iloc[:, -1], errors="coerce").dropna()
    return pd.Series(dtype="float64")


def _solution_vector(facade, solution: dict[str, int]) -> np.ndarray:
    x = np.zeros(facade.num_params)
    for var_name, var_val in solution.items():
        x[int(str(var_name)[2:])] = var_val
    return x


def _delta_demand_df(facade, solution: dict[str, int]) -> pd.DataFrame:
    try:
        delta = facade.get_delta_demand(_solution_vector(facade, solution))
    except Exception:
        return pd.DataFrame(columns=["block_id", "service_type", "demand"])
    rows = [
        {"block_id": block_id, "service_type": service_type, "demand": demand}
        for block_id, services in delta.items()
        for service_type, demand in services.items()
    ]
    return pd.DataFrame(rows)


def _safe_provisions(facade, solution: dict[str, int]) -> dict[str, Any]:
    try:
        provisions, _changed = facade.get_all_provisions(_solution_vector(facade, solution))
        return {
            "start": dict(getattr(facade, "start_provisions", {}) or {}),
            "solution": dict(provisions or {}),
        }
    except Exception:
        return {"start": dict(getattr(facade, "start_provisions", {}) or {}), "solution": {}}


def _save_solution_outputs(output_dir, blocks, ids: list[int], services_df, area_df, delta_df) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    files = {
        "solution_services": output_path / "solution_services.csv",
        "solution_area": output_path / "solution_area.csv",
        "delta_demand": output_path / "delta_demand.csv",
    }
    services_df.to_csv(files["solution_services"], index=False)
    area_df.to_csv(files["solution_area"])
    delta_df.to_csv(files["delta_demand"], index=False)
    for key, path in files.items():
        record_file(path, "csv", meta={"tool": "propose_zone_development", "artifact": key})
        paths[key] = str(path)

    service_counts = pd.Series(0.0, index=blocks.index, name="proposed_services_count")
    if not services_df.empty and "block_id" in services_df.columns:
        counts = services_df.groupby("block_id")["count"].sum()
        service_counts.loc[counts.index] = counts.astype(float)
    for bid in ids:
        if service_counts.loc[bid] == 0:
            service_counts.loc[bid] = 0.1
    map_path = save_metric_map(
        blocks,
        service_counts,
        "proposed_services_count",
        output_path,
        "Предложенные сервисы и зона оптимизации",
    )
    if map_path:
        paths["map"] = str(map_path)
    return paths


def _format_target_suggestion(ids: list[int], reason: str, values: pd.Series | None) -> str:
    lines = [f"Кандидаты для оптимизации ({reason}): {ids}"]
    if values is not None and ids:
        lines.append("Значения по выбранному критерию:")
        lines.extend(f"- block_id {bid}: {float(values.loc[bid]):.4f}" for bid in ids if bid in values.index)
    lines.append(
        "Используй эти IDs в propose_zone_development(block_ids=[...]) при явном запросе гипотез развития. "
        "Если block_id уже задан в вопросе, этот поиск кандидатов не нужен."
    )
    return "\n".join(lines)


def _format_development_hypothesis(
    ids: list[int],
    target_land_use: str,
    service_set: str,
    added_services: list[str],
    services_df: pd.DataFrame,
    area_df: pd.DataFrame,
    delta_df: pd.DataFrame,
    best_val: float,
    success_rate: float,
    evals: int,
    max_time_sec: int,
    max_prov_evals: int,
    provisions: dict[str, Any],
    paths: dict[str, str],
) -> str:
    if services_df.empty:
        services_summary = "новые сервисы не выбраны в найденном решении"
    else:
        grouped = (
            services_df.groupby("service_type")
            .agg(count=("count", "sum"), capacity=("capacity", "sum"))
            .sort_values("count", ascending=False)
        )
        services_summary = "; ".join(
            f"{service}: {int(row['count'])} ед., capacity={int(row['capacity'])}"
            for service, row in grouped.iterrows()
        )
    block_summary = ""
    if not services_df.empty:
        per_block_services = (
            services_df.groupby(["block_id", "service_type"])
            .agg(count=("count", "sum"), capacity=("capacity", "sum"))
            .sort_values("capacity", ascending=False)
            .head(20)
        )
        block_summary = "; ".join(
            f"block_id {int(bid)}: {service} {int(row['count'])} ед., capacity={int(row['capacity'])}"
            for (bid, service), row in per_block_services.iterrows()
        )
    provision_summary = _format_provision_delta(provisions)
    artifact_summary = ", ".join(f"{key}={path}" for key, path in paths.items())
    considered = ", ".join(added_services) if added_services else "—"
    service_set_key = str(service_set).strip().lower()
    if service_set_key in _SERVICE_PRESETS:
        focus_note = (
            f"ВНИМАНИЕ: service_set='{service_set}' — пресет; оптимизировался набор сервисов ({considered}). "
            "Если цель — конкретный сервис, перезапусти с service_set='<имя сервиса>' и сравни эффект по нему. "
        )
    else:
        focus_note = (
            f"Оптимизация сфокусирована на service_set='{service_set}' (рассмотрены: {considered}). "
        )
    return (
        "HYPOTHESES: Гипотеза развития зоны: для кварталов "
        f"{ids} рассмотреть целевое землепользование {target_land_use} и набор сервисов '{service_set}'. "
        f"{focus_note}"
        f"Оптимизатор предложил: {services_summary}. "
        + (f"Распределение по кварталам: {block_summary}. " if block_summary else "")
        + (f"Оценка обеспеченности: {provision_summary}. " if provision_summary else "")
        + f"verdict: TPE-hypothesis for block_id {ids} objective={best_val:.4f}, successful_trials={success_rate:.2f}, "
        f"optimizer_runs={evals}, provision_eval_budget={max_prov_evals}, timeout={max_time_sec}s. "
        "Это эвристическая рекомендация TPE, а не нормативное решение.\n"
        "RESULT: Сохранены solution_services.csv, solution_area.csv, delta_demand.csv и карта предложений. "
        f"Артефакты: {artifact_summary}\n"
        "FOLLOW_UPS: проверить нормативную допустимость выбранного землепользования и размещения предложенных сервисов; "
        "независимо пересчитать сценарий через compute_scenario_provision для проверки before/after provision.\n"
        "LIMITATIONS: Стохастический TPE; результат зависит от выбранных block_ids, весов сервисов, текущей матрицы доступности и ограничений BlocksNet."
    )


def _format_provision_delta(provisions: dict[str, Any]) -> str:
    start = provisions.get("start", {}) or {}
    solution = provisions.get("solution", {}) or {}
    parts = []
    for service, sol_value in solution.items():
        start_value = start.get(service)
        if start_value is None:
            parts.append(f"{service}={float(sol_value):.3f}")
        else:
            parts.append(f"{service}: {float(start_value):.3f}->{float(sol_value):.3f}")
    return "; ".join(parts)
