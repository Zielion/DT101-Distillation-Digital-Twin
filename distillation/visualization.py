from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


LAYER_CHART_X_FIELD = "tick"

PROCESS_TREND_TAGS = (
    "DT101.PV.TOP_TEMP",
    "DT101.PV.BOTTOM_TEMP",
    "DT101.CMD.FEED_VALVE",
    "DT101.PV.COLUMN_PRESSURE",
    "DT101.PV.PURITY_PROXY",
)

EQUIPMENT_STATE_TREND_TAGS = (
    "DT101.FB.FEED_SUPPLY_PUMP_RUNNING",
    "DT101.FB.FEED_SUPPLY_VALVE_OPEN",
    "DT101.FB.FEED_VALVE_OPEN",
    "DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING",
    "DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN",
    "DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING",
    "DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN",
)

TANK_LEVEL_TREND_TAGS = (
    "DT101.PV.FEED_TANK_LEVEL",
    "DT101.PV.DISTILLATE_TANK_LEVEL",
    "DT101.PV.BOTTOMS_TANK_LEVEL",
)

LAYER_TEMPERATURE_SERIES = (
    ("DT101.PV.LAYER_07_TEMP", "Layer 7 - Top", "#22d3ee"),
    ("DT101.PV.LAYER_06_TEMP", "Layer 6 - Middle 5", "#3b82f6"),
    ("DT101.PV.LAYER_05_TEMP", "Layer 5 - Middle 4", "#14b8a6"),
    ("DT101.PV.LAYER_04_TEMP", "Layer 4 - Middle 3", "#facc15"),
    ("DT101.PV.LAYER_03_TEMP", "Layer 3 - Middle 2", "#fb923c"),
    ("DT101.PV.LAYER_02_TEMP", "Layer 2 - Middle 1", "#f87171"),
    ("DT101.PV.LAYER_01_TEMP", "Layer 1 - Bottom", "#dc2626"),
)

LAYER_TEMPERATURE_TAGS = tuple(tag for tag, _, _ in LAYER_TEMPERATURE_SERIES)


def _build_historian_figure(
    dataframe: pd.DataFrame,
    tags: tuple[str, ...],
    *,
    yaxis_title: str,
    line_shape: str = "linear",
) -> go.Figure:
    figure = go.Figure()
    for tag in tags:
        series = dataframe.loc[dataframe["tag"] == tag].sort_values("tick")
        figure.add_trace(
            go.Scatter(
                x=series["tick"],
                y=series["numeric_value"],
                mode="lines",
                name=tag,
                line={"shape": line_shape},
            )
        )
    figure.update_layout(
        xaxis_title="Second",
        yaxis_title=yaxis_title,
        legend_title_text="tag",
        hovermode="x unified",
    )
    return figure


def build_process_historian_figure(dataframe: pd.DataFrame) -> go.Figure:
    return _build_historian_figure(dataframe, PROCESS_TREND_TAGS, yaxis_title="Value")


def build_equipment_state_figure(dataframe: pd.DataFrame) -> go.Figure:
    normalized = dataframe.copy()
    normalized["numeric_value"] = normalized["numeric_value"].map(int)
    figure = _build_historian_figure(
        normalized,
        EQUIPMENT_STATE_TREND_TAGS,
        yaxis_title="State (0 = OFF/CLOSED, 1 = ON/OPEN)",
        line_shape="hv",
    )
    figure.update_yaxes(range=[-0.1, 1.1], tickmode="array", tickvals=[0, 1])
    return figure


def build_tank_level_figure(dataframe: pd.DataFrame) -> go.Figure:
    figure = _build_historian_figure(
        dataframe,
        TANK_LEVEL_TREND_TAGS,
        yaxis_title="Tank level (% vol)",
    )
    figure.update_yaxes(range=[0, 100])
    return figure


def build_layer_temperature_figure(dataframe: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for tag, label, color in LAYER_TEMPERATURE_SERIES:
        series = dataframe.loc[dataframe["tag"] == tag].sort_values("tick")
        figure.add_trace(
            go.Scatter(
                x=series["tick"],
                y=series["numeric_value"],
                mode="lines",
                name=label,
                line={"color": color, "width": 2},
                hovertemplate=f"Second %{{x}}<br>{label}: %{{y:.1f}} degC<extra></extra>",
            )
        )
    figure.update_layout(
        xaxis_title="Second",
        yaxis_title="Temperature (degC)",
        legend_title_text="Column layer",
        hovermode="x unified",
    )
    figure.update_yaxes(range=[-20, 150])
    return figure
