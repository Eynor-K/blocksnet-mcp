from __future__ import annotations

import pandas as pd
from langchain_core.tools import tool
from blocksnet.analysis.centrality import population_centrality, services_centrality
from blocksnet.analysis.diversity import shannon_diversity
from blocksnet.analysis.services import services_collocation, services_count, services_density

from blocksnet_agent.runtime import record_file
from blocksnet_agent.tools.data import ensure_acc_mx, ensure_blocks
from blocksnet_agent.tools.viz import save_metric_map

# T2: метка, отличающая общегородской агрегат от поквартального значения.
_AGG_NOTE = (
    "\n[это агрегат по городу, НЕ значение отдельного квартала; "
    "поквартально — get_block_info(block_id) или get_metric_for_block(result_key, block_id)]"
)


def _save(result, path) -> None:
    if isinstance(result, (pd.DataFrame, pd.Series)):
        result.to_csv(path)
    else:
        pd.Series(result).to_csv(path)
    record_file(path, "csv")


def _series(result, name: str, col: str | None = None) -> pd.Series:
    if isinstance(result, pd.Series):
        return pd.to_numeric(result, errors="coerce").rename(name)
    if isinstance(result, pd.DataFrame):
        if col is None and name in result.columns:
            col = name
        if col is None:
            numeric_cols = result.select_dtypes(include="number").columns
            if len(numeric_cols) == 0:
                raise ValueError("result contains no numeric columns")
            col = numeric_cols[-1]
        return pd.to_numeric(result[col], errors="coerce").rename(name)
    return pd.Series(result, name=name)


def _summary(result, key: str, filename: str, output_dir, top_label: str = "Топ значений", col: str | None = None) -> str:
    path = output_dir / filename
    _save(result, path)
    if isinstance(result, pd.DataFrame) and col is None:
        numeric_cols = result.select_dtypes(include="number").columns
        if len(numeric_cols) == 0:
            return f"{key}: размер {result.shape}. Сохранено: {path}"
    values = _series(result, key, col=col).dropna()
    return (
        f"{key}: мин: {values.min():.4f}, макс: {values.max():.4f}, среднее: {values.mean():.4f}, "
        f"медиана: {values.median():.4f}.\n"
        f"{top_label}:\n{values.nlargest(10).to_string()}\nСохранено: {path}"
        + _AGG_NOTE
    )


def make_services_tools(ctx: dict) -> list:
    state = ctx["state"]
    data_dir = ctx["data_dir"]
    output_dir = ctx["output_dir"]

    @tool
    def compute_services_density() -> str:
        """Вычисляет плотность сервисов для каждого квартала."""
        try:
            df = services_density(ensure_blocks(state, data_dir))
            state["services_density"] = df
            _save(df, output_dir / "services_density.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "services_density", output_dir, "Плотность сервисов")
            return f"Плотность сервисов вычислена.\n{df.describe().to_string()}" + _AGG_NOTE
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_services_count() -> str:
        """Подсчитывает количество объектов каждого типа сервиса по кварталам.

        Это КОНТЕКСТ (сколько объектов стоит), а НЕ показатель покрытия населения. Для обеспеченности
        спроса используй compute_service_provision; количество ≠ обеспеченность.
        """
        try:
            df = services_count(ensure_blocks(state, data_dir))
            state["services_count"] = df
            _save(df, output_dir / "services_count.csv")
            save_metric_map(ensure_blocks(state, data_dir), df, "services_count", output_dir, "Количество сервисов")
            return f"Количество сервисов вычислено.\n{df.describe().to_string()}" + _AGG_NOTE
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_services_collocation() -> str:
        """Анализирует совместное расположение типов сервисов в кварталах."""
        try:
            df = services_collocation(ensure_blocks(state, data_dir))
            state["services_collocation"] = df
            _save(df, output_dir / "services_collocation.csv")
            return f"Колокация сервисов вычислена.\n{df.to_string()[:1000]}"
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_shannon_diversity() -> str:
        """Вычисляет индекс разнообразия Шеннона для распределения сервисов по кварталам.

        Высокие плотность и Shannon diversity указывают на многофункциональные узлы; низкие — на зоны
        дефицита/моно-функции. Количество сервисов ≠ обеспеченность населения.
        """
        try:
            result = shannon_diversity(ensure_blocks(state, data_dir))
            state["shannon_diversity"] = result
            save_metric_map(ensure_blocks(state, data_dir), result, "shannon_diversity", output_dir, "Разнообразие Шеннона")
            return _summary(result, "shannon_diversity", "shannon_diversity.csv", output_dir, "Топ-10 кварталов по разнообразию", col="shannon_diversity")
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_services_centrality() -> str:
        """Вычисляет составной индекс центральности кварталов на основе сервисов и доступности.

        Композит из связности + разнообразия + плотности сервисов. Высокая центральность — узлы,
        где целесообразно концентрировать общественные функции и пересадки. Это ОТНОСИТЕЛЬНЫЙ индекс,
        не абсолютная мощность.
        """
        try:
            result = services_centrality(ensure_acc_mx(state, data_dir), ensure_blocks(state, data_dir))
            state["services_centrality"] = result
            save_metric_map(ensure_blocks(state, data_dir), result, "services_centrality", output_dir, "Центральность сервисов")
            return _summary(result, "services_centrality", "services_centrality.csv", output_dir, "Топ-10 наиболее центральных кварталов")
        except Exception as exc:
            return f"Ошибка: {exc}"

    @tool
    def compute_population_centrality() -> str:
        """Вычисляет центральность кварталов на основе населения и графа смежности.

        Граф смежности строится автоматически, если его нет в кэше. Это относительный индекс.
        """
        try:
            blocks = ensure_blocks(state, data_dir)
            if "adjacency_graph" not in state:
                from blocksnet.relations import generate_adjacency_graph
                state["adjacency_graph"] = generate_adjacency_graph(blocks, buffer_size=0)
            result = population_centrality(blocks, state["adjacency_graph"])
            state["population_centrality"] = result
            save_metric_map(blocks, result, "population_centrality", output_dir, "Центральность населения")
            return _summary(result, "population_centrality", "population_centrality.csv", output_dir, "Топ-10", col="population_centrality")
        except Exception as exc:
            return f"Ошибка: {exc}"

    return [
        compute_services_density,
        compute_services_count,
        compute_services_collocation,
        compute_shannon_diversity,
        compute_services_centrality,
        compute_population_centrality,
    ]
