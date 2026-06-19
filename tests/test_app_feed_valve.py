import json
from pathlib import Path
from types import SimpleNamespace

import plotly.graph_objects as go
from streamlit.testing.v1 import AppTest

import distillation.historian as historian_module
import distillation.visualization as visualization_module


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def button_with_label(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def test_feed_valve_toggle_immediately_updates_control_and_process_state():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    button_with_label(app, "Close feed valve V-100").click().run()

    assert app.session_state.feed_valve_open is False
    assert app.session_state.bus.tags["DT101.HMI.FEED_VALVE_OPEN_REQUEST"] is False
    assert app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] == 0.0
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False
    assert app.session_state.state.feed_flow == 0.0

    button_with_label(app, "Open feed valve V-100").click().run()

    assert app.session_state.feed_valve_open is True
    assert app.session_state.bus.tags["DT101.HMI.FEED_VALVE_OPEN_REQUEST"] is True
    assert app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] == 50.0
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is True
    assert app.session_state.state.feed_flow == 10.0


def test_temperature_slider_rerun_preserves_middle_layers_until_a_tick():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    initial_middle = app.session_state.state.middle_layer_temperatures

    app.slider("top_temp_setpoint").set_value(20.0).run()
    app.slider("bottom_temp_setpoint").set_value(130.0).run()

    assert app.session_state.state.top_temperature == 20.0
    assert app.session_state.state.bottom_temperature == 130.0
    assert app.session_state.state.middle_layer_temperatures == initial_middle

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert app.session_state.state.middle_layer_temperatures != initial_middle


def test_app_upgrades_legacy_process_state_before_rendering():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    legacy_values = vars(app.session_state.state).copy()
    legacy_values.pop("middle_layer_temperatures")
    app.session_state.state = SimpleNamespace(**legacy_values)

    app.run()

    assert not app.exception
    assert len(app.session_state.state.middle_layer_temperatures) == 5


def test_layer_temperature_historian_renders_after_a_tick():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    initial_rows = app.session_state.historian.query(ticks=10)
    assert {row["tick"] for row in initial_rows} == {0}
    assert len(app.get("plotly_chart")) == 2

    button_with_label(app, "Single PLC scan + process tick").click().run()

    rows = app.session_state.historian.query(ticks=10)
    assert {row["tick"] for row in rows} == {0, 1}
    assert app.session_state.tick_count == 1
    assert len(app.get("plotly_chart")) == 2


def test_app_reloads_a_legacy_historian_class(monkeypatch):
    class LegacyHistorian:
        def __init__(self, db_path):
            self.db_path = db_path

    monkeypatch.setattr(historian_module, "Historian", LegacyHistorian)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert hasattr(app.session_state.historian, "latest_tick")


def test_app_reloads_legacy_layer_chart_using_timestamp(monkeypatch):
    def legacy_layer_chart(dataframe):
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=dataframe["timestamp"],
                y=dataframe["numeric_value"],
            )
        )
        figure.update_layout(xaxis_title="Timestamp")
        return figure

    monkeypatch.setattr(visualization_module, "LAYER_CHART_X_FIELD", "timestamp", raising=False)
    monkeypatch.setattr(visualization_module, "build_layer_temperature_figure", legacy_layer_chart)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    layer_chart = json.loads(app.get("plotly_chart")[1].proto.spec)

    assert layer_chart["layout"]["xaxis"]["title"]["text"] == "Second"
