import importlib

import pandas as pd


EXPECTED_TAGS = tuple(f"DT101.PV.LAYER_{layer:02d}_TEMP" for layer in range(7, 0, -1))
EXPECTED_NAMES = (
    "Layer 7 - Top",
    "Layer 6 - Middle 5",
    "Layer 5 - Middle 4",
    "Layer 4 - Middle 3",
    "Layer 3 - Middle 2",
    "Layer 2 - Middle 1",
    "Layer 1 - Bottom",
)

EXPECTED_PROCESS_TREND_TAGS = (
    "DT101.PV.TOP_TEMP",
    "DT101.PV.BOTTOM_TEMP",
    "DT101.CMD.FEED_VALVE",
    "DT101.PV.COLUMN_PRESSURE",
    "DT101.PV.PURITY_PROXY",
)
EXPECTED_EQUIPMENT_STATE_TAGS = (
    "DT101.FB.FEED_SUPPLY_PUMP_RUNNING",
    "DT101.FB.FEED_SUPPLY_VALVE_OPEN",
    "DT101.FB.FEED_VALVE_OPEN",
    "DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING",
    "DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN",
    "DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING",
    "DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN",
)
EXPECTED_TANK_LEVEL_TAGS = (
    "DT101.PV.FEED_TANK_LEVEL",
    "DT101.PV.DISTILLATE_TANK_LEVEL",
    "DT101.PV.BOTTOMS_TANK_LEVEL",
)


def test_layer_temperature_figure_uses_seven_historian_series():
    visualization = importlib.import_module("distillation.visualization")
    rows = []
    expected_values = {}
    for offset, tag in enumerate(EXPECTED_TAGS):
        values = (40.0 + offset * 10.0, 41.0 + offset * 10.0)
        expected_values[tag] = values
        rows.extend(
            [
                {"tick": 0, "tag": tag, "numeric_value": values[0]},
                {"tick": 1, "tag": tag, "numeric_value": values[1]},
            ]
        )

    figure = visualization.build_layer_temperature_figure(pd.DataFrame(rows))

    assert visualization.LAYER_TEMPERATURE_TAGS == EXPECTED_TAGS
    assert tuple(trace.name for trace in figure.data) == EXPECTED_NAMES
    assert len({trace.line.color for trace in figure.data}) == 7
    for trace, tag in zip(figure.data, EXPECTED_TAGS):
        assert tuple(trace.y) == expected_values[tag]
    assert tuple(figure.layout.yaxis.range) == (-20, 150)
    assert figure.layout.xaxis.title.text == "Second"
    assert figure.layout.yaxis.title.text == "Temperature (degC)"


def trend_dataframe(tags, values):
    return pd.DataFrame(
        [
            {"tick": tick, "tag": tag, "numeric_value": value}
            for tag in tags
            for tick, value in enumerate(values)
        ]
    )


def test_process_historian_figure_contains_only_process_overview_series():
    visualization = importlib.import_module("distillation.visualization")
    dataframe = trend_dataframe(EXPECTED_PROCESS_TREND_TAGS, (10.0, 20.0))

    figure = visualization.build_process_historian_figure(dataframe)

    assert visualization.PROCESS_TREND_TAGS == EXPECTED_PROCESS_TREND_TAGS
    assert tuple(trace.name for trace in figure.data) == EXPECTED_PROCESS_TREND_TAGS
    assert figure.layout.xaxis.title.text == "Second"


def test_equipment_state_figure_converts_feedback_to_zero_one_steps():
    visualization = importlib.import_module("distillation.visualization")
    dataframe = trend_dataframe(EXPECTED_EQUIPMENT_STATE_TAGS, (False, True))

    figure = visualization.build_equipment_state_figure(dataframe)

    assert visualization.EQUIPMENT_STATE_TREND_TAGS == EXPECTED_EQUIPMENT_STATE_TAGS
    assert tuple(trace.name for trace in figure.data) == EXPECTED_EQUIPMENT_STATE_TAGS
    assert all(tuple(trace.y) == (0, 1) for trace in figure.data)
    assert all(trace.line.shape == "hv" for trace in figure.data)
    assert tuple(figure.layout.yaxis.tickvals) == (0, 1)
    assert tuple(figure.layout.yaxis.range) == (-0.1, 1.1)
    assert figure.layout.yaxis.title.text == "State (0 = OFF/CLOSED, 1 = ON/OPEN)"


def test_tank_level_figure_uses_three_visible_tanks_and_percent_volume_axis():
    visualization = importlib.import_module("distillation.visualization")
    dataframe = trend_dataframe(EXPECTED_TANK_LEVEL_TAGS, (25.0, 75.0))

    figure = visualization.build_tank_level_figure(dataframe)

    assert visualization.TANK_LEVEL_TREND_TAGS == EXPECTED_TANK_LEVEL_TAGS
    assert tuple(trace.name for trace in figure.data) == EXPECTED_TANK_LEVEL_TAGS
    assert tuple(figure.layout.yaxis.range) == (0, 100)
    assert figure.layout.yaxis.title.text == "Tank level (% vol)"
