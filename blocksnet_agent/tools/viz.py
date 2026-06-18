from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import pandas as pd
from langchain_core.tools import tool

from blocksnet_agent.runtime import get_run_context, get_run_dir, record_file


def save_metric_map(blocks_gdf, metric, name: str, run_dir, title: str | None = None):
    """Сохраняет картограмму поквартальной метрики (PNG) в подкаталог maps текущего запуска."""
    try:
        import matplotlib.pyplot as plt

        ctx = get_run_context()
        maps_dir = ctx.maps_dir if ctx else get_run_dir(run_dir) / "maps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        series = _metric_series(metric, name)
        if series.empty:
            return None
        gdf = blocks_gdf[["geometry"]].copy()
        gdf[name] = series.reindex(gdf.index)
        if gdf[name].notna().sum() == 0 and "block_id" in blocks_gdf.columns:
            by_block = pd.Series(series.values, index=series.index)
            gdf[name] = blocks_gdf["block_id"].map(by_block)
        if gdf[name].notna().sum() == 0:
            return None
        # T1.3: уважаем потолок картограмм на запуск (батч не должен плодить десятки PNG).
        if ctx and not ctx.logger.reserve_map():
            return None
        fig, ax = plt.subplots(figsize=(8, 8))
        gdf.plot(
            column=name,
            legend=True,
            cmap="viridis",
            missing_kwds={"color": "lightgrey", "label": "No data"},
            ax=ax,
        )
        ax.set_axis_off()
        ax.set_title(title or name)
        path = maps_dir / f"{_safe_name(name)}.png"
        fig.savefig(path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        record_file(path, "map", meta={"metric": name})
        return path
    except Exception:
        return None


def make_viz_tools(ctx: dict) -> list:
    state = ctx["state"]
    output_dir = ctx["output_dir"]

    @tool
    def render_metric_map(result_key: str, title: str = "") -> str:
        """Рисует PNG-картограмму для сохранённого поквартального результата BlocksNet."""
        try:
            if "blocks" not in state:
                return "Ошибка: blocks не загружены."
            if result_key not in state:
                return f"Ошибка: result_key '{result_key}' не найден. Доступные: {list(state.keys())}"
            path = save_metric_map(
                state["blocks"],
                state[result_key],
                result_key,
                get_run_dir(output_dir),
                title=title or result_key,
            )
            return f"Карта сохранена: {path}" if path else "Карта не создана: нет подходящей поквартальной метрики."
        except Exception as exc:
            return f"Ошибка: {exc}"

    return [render_metric_map]


def _metric_series(metric, name: str) -> pd.Series:
    if isinstance(metric, pd.Series):
        return pd.to_numeric(metric, errors="coerce").rename(name)
    if isinstance(metric, pd.DataFrame):
        preferred = [
            name,
            "accessibility",
            "mean_accessibility",
            "median_accessibility",
            "max_accessibility",
            "provision_strong",
            "provision",
            "provision_weak",
            "services_centrality",
            "population_centrality",
            "shannon_diversity",
            "fsi",
            "gsi",
            "mxi",
            "osr",
        ]
        for col in preferred:
            if col in metric.columns:
                return pd.to_numeric(metric[col], errors="coerce").rename(name)
        numeric_cols = metric.select_dtypes(include="number").columns
        if len(numeric_cols):
            return pd.to_numeric(metric[numeric_cols[-1]], errors="coerce").rename(name)
    return pd.Series(dtype="float64", name=name)


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:120]
