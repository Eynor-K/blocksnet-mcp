from __future__ import annotations

import pandas as pd
from langchain_core.tools import tool
from blocksnet.analysis.network import (
    area_accessibility,
    calculate_connectivity,
    land_use_accessibility,
    max_accessibility,
    mean_accessibility,
    median_accessibility,
)
from blocksnet.enums import LandUse

from blocksnet_agent.runtime import record_file
from blocksnet_agent.tools.data import ensure_acc_mx, ensure_blocks
from blocksnet_agent.tools.viz import save_metric_map

# T2: метка, отличающая общегородской агрегат от поквартального значения.
_AGG_NOTE = (
    "\n[это агрегат по городу, НЕ значение отдельного квартала; "
    "поквартально — get_block_info(block_id) или get_metric_for_block(result_key, block_id)]"
)


def _numeric_series(result, name: str, col: str | None = None) -> pd.Series:
    if isinstance(result, pd.Series):
        return pd.to_numeric(result, errors="coerce").rename(name)
    if isinstance(result, pd.DataFrame):
        if col is None and name in result.columns:
            col = name
        if col is None:
            numeric_cols = result.select_dtypes(include="number").columns
            if len(numeric_cols) == 0:
                raise ValueError("result contains no numeric columns")
            col = numeric_cols[0]
        return pd.to_numeric(result[col], errors="coerce").rename(name)
    return pd.Series(result, name=name)


def _save(result, path) -> None:
    if isinstance(result, (pd.DataFrame, pd.Series)):
        result.to_csv(path)
    else:
        pd.Series(result).to_csv(path)
    record_file(path, "csv")


def _acc_summary(df: pd.DataFrame | pd.Series, col: str | None = "accessibility") -> str:
    if isinstance(df, pd.Series):
        series = pd.to_numeric(df, errors="coerce").dropna()
        label = df.name or "accessibility"
    else:
        if col is None or col not in df.columns:
            numeric_cols = df.select_dtypes(include="number").columns
            col = numeric_cols[0] if len(numeric_cols) else df.columns[0]
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        label = col
    top5 = series.nsmallest(5)
    bot5 = series.nlargest(5)
    return (
        f"Мин: {series.min():.2f}, макс: {series.max():.2f}, среднее: {series.mean():.2f}, медиана: {series.median():.2f}.\n"
        f"Топ-5 наиболее доступных (наименьшее время), {label}:\n{top5.to_string()}\n"
        f"Топ-5 наименее доступных (наибольшее время), {label}:\n{bot5.to_string()}"
        + _AGG_NOTE
    )


def make_network_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def compute_mean_accessibility(out: bool = True) -> str:
        """Вычисляет среднее время доступности (мин) для каждого квартала по матрице доступности.

        Меньшее время — лучше; высокий максимум указывает на периферийность/разрывность сети.
        out=True — исходящая доступность (как из квартала доступны другие), out=False — входящая
        (как сам квартал доступен из других). Это доступность до ВСЕХ кварталов, не до конкретного сервиса.
        """
        try:
            df = mean_accessibility(ensure_acc_mx(state, data_dir), out=out)
            state["mean_accessibility"] = df
            _save(df, output_dir / "mean_accessibility.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "mean_accessibility", output_dir, "Средняя доступность")
            return f"Средняя доступность (out={out}) вычислена.\n" + _acc_summary(df, col=None)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_median_accessibility(out: bool = True) -> str:
        """Вычисляет медианное время доступности для каждого квартала."""
        try:
            df = median_accessibility(ensure_acc_mx(state, data_dir), out=out)
            state["median_accessibility"] = df
            _save(df, output_dir / "median_accessibility.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "median_accessibility", output_dir, "Медианная доступность")
            return f"Медианная доступность (out={out}) вычислена.\n" + _acc_summary(df, col=None)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_max_accessibility(out: bool = True) -> str:
        """Вычисляет максимальное время доступности для каждого квартала."""
        try:
            df = max_accessibility(ensure_acc_mx(state, data_dir), out=out)
            state["max_accessibility"] = df
            _save(df, output_dir / "max_accessibility.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "max_accessibility", output_dir, "Максимальная доступность")
            return f"Максимальная доступность (out={out}) вычислена.\n" + _acc_summary(df, col=None)
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_connectivity(accessibility_key: str = "mean_accessibility") -> str:
        """Вычисляет связность транспортной сети из сохранённого результата доступности (нормированная).

        Требует предварительно вычисленную доступность (mean/median/max); если её нет, mean считается
        автоматически. accessibility_key — какой результат доступности использовать.
        """
        try:
            if accessibility_key not in state:
                state[accessibility_key] = mean_accessibility(ensure_acc_mx(state, data_dir), out=True)
            result = calculate_connectivity(state[accessibility_key])
            state["connectivity"] = result
            _save(result, output_dir / "connectivity.csv")
            save_metric_map(ensure_blocks(state, data_dir), result, "connectivity", output_dir, "Связность")
            series = _numeric_series(result, "connectivity")
            return (
                f"Связность вычислена.\nМин: {series.min():.4f}, макс: {series.max():.4f}, среднее: {series.mean():.4f}.\n"
                f"Топ-5 наиболее связных кварталов:\n{series.nlargest(5).to_string()}"
                + _AGG_NOTE
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_land_use_accessibility(land_use: str, out: bool = True) -> str:
        """Вычисляет доступность (мин) до кварталов определённого типа землепользования.

        land_use из enum LandUse: RESIDENTIAL, BUSINESS, RECREATION, TRANSPORT, INDUSTRIAL, SPECIAL.
        Среднее/медиана — общая близость к зоне; худшие кварталы выявляют пространственные разрывы.
        """
        try:
            lu = LandUse[land_use.upper()]
            df = land_use_accessibility(ensure_acc_mx(state, data_dir), ensure_blocks(state, data_dir), land_use=lu, out=out)
            key = f"land_use_accessibility_{land_use.lower()}"
            state[key] = df
            _save(df, output_dir / f"{key}.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, key, output_dir, f"Доступность до зон {land_use}")
            return f"Доступность до зон {land_use} вычислена.\n" + _acc_summary(df, col=None)
        except KeyError:
            return f"Неверный тип: '{land_use}'. Допустимые: {[item.name for item in LandUse]}"
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_area_accessibility(out: bool = True) -> str:
        """Вычисляет площадно-взвешенную доступность."""
        try:
            df = area_accessibility(ensure_acc_mx(state, data_dir), ensure_blocks(state, data_dir), out=out)
            state["area_accessibility"] = df
            _save(df, output_dir / "area_accessibility.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "area_accessibility", output_dir, "Площадно-взвешенная доступность")
            return f"Площадно-взвешенная доступность (out={out}) вычислена.\n" + _acc_summary(df, col=None)
        except Exception as exc:
            return f"Ошибка: {exc}"

    return [
        compute_mean_accessibility,
        compute_median_accessibility,
        compute_max_accessibility,
        compute_connectivity,
        compute_land_use_accessibility,
        compute_area_accessibility,
    ]
