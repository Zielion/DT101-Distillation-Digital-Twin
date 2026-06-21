import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import plotly.graph_objects as go
import streamlit as st
from streamlit.testing.v1 import AppTest

import distillation.historian as historian_module
import distillation.process as process_module
import distillation.visualization as visualization_module
import distillation.config as config_module
from distillation.tags import TAG_DICTIONARY


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def button_with_label(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def process_overview_markup(app: AppTest) -> str:
    return next(markdown.value for markdown in app.markdown if "dt101-board" in markdown.value)


def test_continuous_run_controls_toggle_without_advancing_on_start():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    assert app.session_state.continuous_run is False
    assert app.session_state.tick_count == 0

    button_with_label(app, "Start continuous run").click().run()

    assert app.session_state.continuous_run is True
    assert app.session_state.tick_count == 0
    assert button_with_label(app, "Stop continuous run")


def test_continuous_run_advances_selected_tick_batch_per_fragment_execution_and_stops():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    next(slider for slider in app.slider if slider.label == "Advance ticks").set_value(3).run()
    button_with_label(app, "Start continuous run").click().run()

    app.run()
    assert app.session_state.tick_count == 3

    button_with_label(app, "Stop continuous run").click().run()
    stopped_tick = app.session_state.tick_count
    app.run()

    assert app.session_state.continuous_run is False
    assert app.session_state.tick_count == stopped_tick


def test_continuous_tick_requests_full_app_rerun_after_advancing(monkeypatch):
    rerun_scopes = []
    monkeypatch.setattr(st, "rerun", lambda *, scope="app": rerun_scopes.append(scope))
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Start continuous run").click().run()

    app.run()

    assert app.session_state.tick_count == 5
    assert app.session_state.continuous_run_skip_next is True
    assert rerun_scopes == ["app"]


def test_reset_simulation_stops_continuous_run():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Start continuous run").click().run()

    button_with_label(app, "Reset simulation").click().run()

    assert app.session_state.continuous_run is False
    assert app.session_state.tick_count == 0


def test_process_overview_contains_only_the_simplified_process_equipment():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    required_labels = (
        "Feed tank",
        "V-100",
        "Column",
        "Column heating duty",
        "Condenser",
        "Distillate product",
        "Bottom product",
    )
    removed_labels = (
        "Reboiler",
        "Steam",
        "Condensate",
        "Feed preheater",
        "Reflux pump",
        "Storage tank",
        "Rectifying",
        "Stripping",
        "Feed tray",
    )

    assert all(label in markup for label in required_labels)
    assert all(label not in markup for label in removed_labels)
    assert "min-width: 920px;" in markup


def test_process_overview_selector_and_live_control_values_are_synchronized():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.bus.tags["DT101.CMD.REBOILER_DUTY"] = 73.0
    app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] = 0.0
    app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] = False

    app.run()
    markup = process_overview_markup(app)

    assert app.radio[0].options == ["Feed system", "Column", "Condenser", "Products"]
    assert "Column heating duty" in markup
    assert "73.0%" in markup
    assert "V-100 CLOSED" in markup
    assert "Command 0.0%" not in markup


def test_process_overview_places_tank_percentages_inside_and_inventory_details_below():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    assert getattr(config_module, "FEED_TANK_MAX_CAPACITY_L", None) == 1000.0
    assert getattr(config_module, "DISTILLATE_TANK_MAX_CAPACITY_L", None) == 500.0
    assert getattr(config_module, "BOTTOMS_TANK_MAX_CAPACITY_L", None) == 500.0
    assert 'class="tank-percent">10.0%</div>' in markup
    assert markup.count('class="tank-percent">20.0%</div>') == 2
    assert "Maximum capacity 1000 L" in markup
    assert "Current capacity 100.0 L" in markup
    assert markup.count("Maximum capacity 500 L") == 2
    assert markup.count("Current capacity 100.0 L") == 3
    assert "Level 10.0%" not in markup
    assert "Tank 20.0%" not in markup
    assert re.search(r">Feed tank</div>\s*<div class=\"dt101-equipment feed-tank", markup)
    assert re.search(r">Distillate product</div>\s*<div class=\"product-card distillate-product", markup)
    assert re.search(r">Bottom product</div>\s*<div class=\"product-card bottom-product", markup)
    assert 'class="process-line horizontal right bottom-product-route' in markup
    assert ".bottom-product-route" in markup


def test_process_overview_clamps_tank_inventory_liters_to_physical_capacity():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.state = replace(
        app.session_state.state,
        feed_tank_level=120.0,
        distillate_tank_level=-10.0,
        bottoms_tank_level=150.0,
    )

    app.run()
    markup = process_overview_markup(app)

    assert markup.count('class="tank-percent">100.0%</div>') == 2
    assert 'class="tank-percent">0.0%</div>' in markup
    assert "Current capacity 1000.0 L" in markup
    assert "Current capacity 0.0 L" in markup
    assert "Current capacity 500.0 L" in markup


def test_app_recovers_when_streamlit_holds_a_stale_config_module(monkeypatch):
    monkeypatch.delattr(config_module, "FEED_TANK_MAX_CAPACITY_L")
    monkeypatch.delattr(config_module, "DISTILLATE_TANK_MAX_CAPACITY_L")
    monkeypatch.delattr(config_module, "BOTTOMS_TANK_MAX_CAPACITY_L")
    monkeypatch.delattr(config_module, "FEED_SUPPLY_FLOW_LPM")
    monkeypatch.delattr(config_module, "FEED_TANK_HIGH_HIGH")
    monkeypatch.delattr(config_module, "PRODUCT_EXPORT_FLOW_LPM")
    monkeypatch.delattr(config_module, "PRODUCT_EXPORT_START_LEVEL")
    monkeypatch.delattr(config_module, "PRODUCT_EXPORT_STOP_LEVEL")

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert "Maximum capacity 1000 L" in process_overview_markup(app)


def test_app_recovers_when_streamlit_holds_a_stale_process_module(monkeypatch):
    class StaleProcessState:
        __dataclass_fields__ = {}

    monkeypatch.setattr(process_module, "ProcessState", StaleProcessState)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.session_state.state.feed_inlet_flow == 0.0
    assert app.session_state.state.distillate_outlet_flow == 0.0
    assert app.session_state.state.bottoms_outlet_flow == 0.0


def test_app_reloads_process_module_when_model_revision_is_stale(monkeypatch):
    original_process_state = process_module.ProcessState
    monkeypatch.setattr(process_module, "PROCESS_MODEL_REVISION", 1, raising=False)

    try:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

        assert not app.exception
        assert process_module.PROCESS_MODEL_REVISION == 3
    finally:
        process_module.ProcessState = original_process_state


def test_app_upgrades_a_stale_plc_session_object():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.plc = SimpleNamespace(
        mode="NORMAL_OPERATION",
        top_temp_setpoint=79.0,
        bottom_temp_setpoint=100.0,
        stable_seconds=3.0,
    )

    app.run()

    assert not app.exception
    assert app.session_state.plc.mode == "NORMAL_OPERATION"
    assert app.session_state.plc.feed_cycle_phase == "FILLING_FEED_TANK"
    assert app.session_state.plc.distillate_export_running is False
    assert app.session_state.plc.bottoms_export_running is False


def test_reset_enables_automatic_feed_cycle_at_ten_percent_with_all_devices_closed():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    button_with_label(app, "Reset simulation").click().run()

    assert app.session_state.state.feed_tank_level == 10.0
    assert app.session_state.feed_supply_run_request is True
    assert app.session_state.feed_valve_open is True
    assert app.session_state.plc.feed_cycle_phase == "FILLING_FEED_TANK"
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False


def test_app_feed_cycle_uses_previous_feedback_for_break_before_make_transitions():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False

    app.session_state.state = replace(app.session_state.state, feed_tank_level=95.0)
    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False

    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is True

    app.session_state.state = replace(app.session_state.state, feed_tank_level=10.0)
    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False

    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False


def test_feed_valve_auto_allow_immediately_disables_and_reenables_permitted_feed():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=95.0)
    button_with_label(app, "Single PLC scan + process tick").click().run()

    button_with_label(app, "Disable feed valve V-100 auto").click().run()

    assert app.session_state.feed_valve_open is False
    assert app.session_state.bus.tags["DT101.HMI.FEED_VALVE_OPEN_REQUEST"] is False
    assert app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] == 0.0
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False
    assert app.session_state.state.feed_flow == 0.0

    button_with_label(app, "Enable feed valve V-100 auto").click().run()

    assert app.session_state.feed_valve_open is True
    assert app.session_state.bus.tags["DT101.HMI.FEED_VALVE_OPEN_REQUEST"] is True
    assert app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] == 100.0
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is True
    assert app.session_state.state.feed_flow == 20.0


def test_closed_input_and_feed_valves_keep_product_tank_levels_constant_across_ticks():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Disable input supply auto P-100 / V-099").click().run()
    button_with_label(app, "Disable feed valve V-100 auto").click().run()
    initial_levels = (
        app.session_state.state.distillate_tank_level,
        app.session_state.state.bottoms_tank_level,
    )

    for _ in range(3):
        button_with_label(app, "Run selected ticks").click().run()

    assert app.session_state.feed_supply_run_request is False
    assert app.session_state.feed_valve_open is False
    assert app.session_state.state.feed_flow == 0.0
    assert app.session_state.bus.tags["DT101.CMD.CONDENSER_VALVE"] == 0.0
    assert app.session_state.state.cooling_water_flow == 0.0
    assert app.session_state.state.distillate_flow == 0.0
    assert app.session_state.state.bottoms_flow == 0.0
    assert (
        app.session_state.state.distillate_tank_level,
        app.session_state.state.bottoms_tank_level,
    ) == initial_levels


def test_input_supply_toggle_updates_linked_devices_flow_and_tags():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    assert app.session_state.feed_supply_run_request is True
    assert app.session_state.state.feed_inlet_flow == 0.0

    button_with_label(app, "Disable input supply auto P-100 / V-099").click().run()
    assert app.session_state.feed_supply_run_request is False
    assert app.session_state.state.feed_inlet_flow == 0.0

    button_with_label(app, "Enable input supply auto P-100 / V-099").click().run()

    expected = {
        "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": True,
        "DT101.CMD.FEED_SUPPLY_PUMP": True,
        "DT101.CMD.FEED_SUPPLY_VALVE": True,
        "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": True,
        "DT101.FB.FEED_SUPPLY_VALVE_OPEN": True,
        "DT101.PV.FEED_INLET_FLOW": 10.0,
    }
    assert app.session_state.feed_supply_run_request is True
    assert app.session_state.tick_count == 2
    assert all(app.session_state.bus.tags[tag] == value for tag, value in expected.items())
    assert app.session_state.state.feed_inlet_flow == 10.0
    assert all(tag in TAG_DICTIONARY for tag in (*expected, "DT101.ALARM.FEED_TANK_HIGH_HIGH"))

    button_with_label(app, "Disable input supply auto P-100 / V-099").click().run()

    assert app.session_state.feed_supply_run_request is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.PV.FEED_INLET_FLOW"] == 0.0


def test_input_supply_overview_uses_actual_pump_valve_and_flow_feedback():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    stopped_markup = process_overview_markup(app)

    assert "Input" in stopped_markup
    assert "P-100 STOPPED" in stopped_markup
    assert "V-099 CLOSED" in stopped_markup
    assert "Inlet flow 00.0 L/min" in stopped_markup
    assert 'class="feed-supply-pump stopped' in stopped_markup
    assert 'class="pump-rotor"' in stopped_markup
    assert 'class="feed-supply-valve closed' in stopped_markup

    button_with_label(app, "Single PLC scan + process tick").click().run()
    running_markup = process_overview_markup(app)

    assert "P-100 RUNNING" in running_markup
    assert "V-099 OPEN" in running_markup
    assert "Inlet flow 10.0 L/min" in running_markup
    assert 'class="feed-supply-pump running' in running_markup
    assert 'class="feed-supply-valve open' in running_markup
    assert "@keyframes pump-rotation" in running_markup


def test_product_export_units_are_automatic_and_render_actual_feedback():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.state = replace(
        app.session_state.state,
        distillate_tank_level=61.0,
        bottoms_tank_level=61.0,
    )

    button_with_label(app, "Single PLC scan + process tick").click().run()

    expected_running = {
        "DT101.CMD.DISTILLATE_EXPORT_PUMP": True,
        "DT101.CMD.DISTILLATE_EXPORT_VALVE": True,
        "DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING": True,
        "DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN": True,
        "DT101.PV.DISTILLATE_OUTLET_FLOW": 15.0,
        "DT101.CMD.BOTTOMS_EXPORT_PUMP": True,
        "DT101.CMD.BOTTOMS_EXPORT_VALVE": True,
        "DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING": True,
        "DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN": True,
        "DT101.PV.BOTTOMS_OUTLET_FLOW": 15.0,
    }
    assert all(app.session_state.bus.tags[tag] == value for tag, value in expected_running.items())
    assert all(tag in TAG_DICTIONARY for tag in expected_running)
    running_markup = process_overview_markup(app)
    assert "P-201 RUNNING" in running_markup
    assert "V-201 OPEN" in running_markup
    assert "P-202 RUNNING" in running_markup
    assert "V-202 OPEN" in running_markup
    assert running_markup.count("Outlet flow 15.0 L/min") == 2
    assert running_markup.count('class="product-export-pump running') == 2

    app.session_state.state = replace(
        app.session_state.state,
        distillate_tank_level=9.0,
        bottoms_tank_level=9.0,
    )
    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert app.session_state.bus.tags["DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN"] is False
    assert app.session_state.bus.tags["DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN"] is False
    assert app.session_state.state.distillate_outlet_flow == 0.0
    assert app.session_state.state.bottoms_outlet_flow == 0.0
    stopped_markup = process_overview_markup(app)
    assert "P-201 STOPPED" in stopped_markup
    assert "V-201 CLOSED" in stopped_markup
    assert "P-202 STOPPED" in stopped_markup
    assert "V-202 CLOSED" in stopped_markup

    assert not any("P-201" in button.label or "P-202" in button.label for button in app.button)


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
