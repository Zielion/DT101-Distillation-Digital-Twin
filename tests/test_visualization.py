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
