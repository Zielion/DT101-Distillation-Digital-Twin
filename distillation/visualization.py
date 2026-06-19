from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


LAYER_CHART_X_FIELD = "tick"

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
