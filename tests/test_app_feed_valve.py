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
import distillation.cloud_bridge as cloud_bridge_module
import distillation.config as config_module
from distillation.tags import TAG_DICTIONARY


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def button_with_label(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def process_overview_markup(app: AppTest) -> str:
    return next(markdown.value for markdown in app.markdown if "dt101-board" in markdown.value)


def route_classes(markup: str, route_name: str) -> str:
    match = re.search(
        rf'<g data-route="{re.escape(route_name)}" class="([^"]+)">',
        markup,
    )
    assert match is not None, f"missing process route: {route_name}"
    return match.group(1)


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
    assert "min-width: 1520px;" in markup
    assert "height: 600px;" in markup


def test_process_overview_uses_svg_routes_with_directional_flow_markers():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    assert '<svg class="process-routes" viewBox="0 0 1520 600"' in markup
    assert 'id="flow-arrow-cold"' in markup
    assert 'id="flow-arrow-hot"' in markup
    assert "@keyframes process-flow" in markup
    assert "stroke-dashoffset" in markup
    assert "@media (prefers-reduced-motion: reduce)" in markup

    expected_routes = (
        "feed-supply",
        "feed-to-column",
        "column-overhead",
        "condenser-to-distillate",
        "distillate-export",
        "column-to-bottoms",
        "bottoms-export",
    )
    assert all("process-route" in route_classes(markup, route) for route in expected_routes)


def test_column_bottoms_route_connects_to_column_and_layout_is_compact():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    assert '<path class="route-base" d="M699 420 V435 H900" />' in markup
    assert '<path class="route-flow" d="M699 420 V435 H900"' in markup
    assert "width: 1520px;" in markup
    assert "width: 1760px;" not in markup


def test_column_removes_heating_badge_and_expands_all_seven_layers():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    assert markup.count('<div class="layer-band">') == 7
    assert 'class="column-heater' not in markup
    assert "Column heating duty" not in markup
    assert "top: 15px; bottom: 15px;" in markup


def test_process_route_animation_classes_follow_flow_and_interlock_state():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    stopped_markup = process_overview_markup(app)

    assert "flow-stopped" in route_classes(stopped_markup, "feed-supply")
    assert "flow-stopped" in route_classes(stopped_markup, "feed-to-column")
    assert "flow-stopped" in route_classes(stopped_markup, "distillate-export")
    assert "flow-stopped" in route_classes(stopped_markup, "bottoms-export")

    button_with_label(app, "Single PLC scan + process tick").click().run()
    assert "flow-active" in route_classes(process_overview_markup(app), "feed-supply")

    app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] = True
    app.session_state.state = replace(
        app.session_state.state,
        feed_flow=10.0,
        distillate_flow=4.5,
        bottoms_flow=4.5,
    )
    app.run()
    running_markup = process_overview_markup(app)

    assert "flow-active" in route_classes(running_markup, "feed-to-column")
    assert "flow-active" in route_classes(running_markup, "column-overhead")
    assert "flow-active" in route_classes(running_markup, "condenser-to-distillate")
    assert "flow-active" in route_classes(running_markup, "column-to-bottoms")


def test_process_overview_and_related_tables_use_liters_per_second():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    markup = process_overview_markup(app)

    assert markup.count("L/s") == 6
    assert "L/min" not in markup

    profile_values = []
    for equipment in ("Feed system", "Condenser", "Products"):
        equipment_app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        equipment_app.radio[0].set_value(equipment).run()
        profile_values.extend(equipment_app.dataframe[0].value["Live value"].tolist())
    assert sum(str(value).endswith(" L/s") for value in profile_values) == 7
    assert all("L/min" not in str(value) for value in profile_values)

    live_variables = app.dataframe[1].value["Variable"].tolist()
    assert sum(str(variable).endswith("(L/s)") for variable in live_variables) == 4
    assert all("L/min" not in str(variable) for variable in live_variables)


def test_process_overview_selector_and_live_control_values_are_synchronized():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.bus.tags["DT101.CMD.REBOILER_DUTY"] = 73.0
    app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] = 0.0
    app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] = False

    app.run()
    markup = process_overview_markup(app)

    assert app.radio[0].options == ["Feed system", "Column", "Condenser", "Products"]
    assert "Column heating duty" not in markup
    assert "73.0%" not in markup
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
    assert "process-route" in route_classes(markup, "column-to-bottoms")
    assert "process-line" not in markup


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
    assert set(app.session_state.device_overrides.values()) == {"AUTO"}
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

    app.session_state.state = replace(app.session_state.state, feed_tank_level=80.0)
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


def test_manual_v100_toggle_opens_at_low_level_and_return_to_auto_restores_protocol():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    button_with_label(app, "Open V-100 manually").click().run()

    assert app.session_state.device_overrides["V100"] == "FORCE_ON"
    assert app.session_state.bus.tags["DT101.HMI.V100_OVERRIDE"] == "FORCE_ON"
    assert app.session_state.bus.tags["DT101.CMD.FEED_VALVE"] == 100.0
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is True
    assert app.session_state.state.feed_flow == 20.0

    button_with_label(app, "Return V-100 to Auto").click().run()

    assert app.session_state.device_overrides["V100"] == "AUTO"
    assert app.session_state.bus.tags["DT101.HMI.V100_OVERRIDE"] == "AUTO"
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False


def test_closed_input_and_feed_valves_keep_product_tank_levels_constant_across_ticks():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Open V-100 manually").click().run()
    button_with_label(app, "Close V-100 manually").click().run()
    initial_levels = (
        app.session_state.state.distillate_tank_level,
        app.session_state.state.bottoms_tank_level,
    )

    for _ in range(3):
        button_with_label(app, "Run selected ticks").click().run()

    assert app.session_state.device_overrides["V100"] == "FORCE_OFF"
    assert app.session_state.state.feed_flow == 0.0
    assert app.session_state.bus.tags["DT101.CMD.CONDENSER_VALVE"] == 0.0
    assert app.session_state.state.cooling_water_flow == 0.0
    assert app.session_state.state.distillate_flow == 0.0
    assert app.session_state.state.bottoms_flow == 0.0
    assert (
        app.session_state.state.distillate_tank_level,
        app.session_state.state.bottoms_tank_level,
    ) == initial_levels


def test_independent_p100_and_v099_toggles_require_both_devices_for_inlet_flow():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Single PLC scan + process tick").click().run()
    button_with_label(app, "Close V-099 manually").click().run()
    button_with_label(app, "Stop P-100 manually").click().run()

    button_with_label(app, "Start P-100 manually").click().run()
    assert app.session_state.device_overrides["P100"] == "FORCE_ON"
    assert app.session_state.device_overrides["V099"] == "FORCE_OFF"
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False
    assert app.session_state.state.feed_inlet_flow == 0.0

    button_with_label(app, "Open V-099 manually").click().run()
    assert app.session_state.device_overrides["P100"] == "FORCE_ON"
    assert app.session_state.device_overrides["V099"] == "FORCE_ON"
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is True
    assert app.session_state.state.feed_inlet_flow == 10.0
    assert app.session_state.bus.tags["DT101.HMI.P100_OVERRIDE"] == "FORCE_ON"
    assert app.session_state.bus.tags["DT101.HMI.V099_OVERRIDE"] == "FORCE_ON"


def test_manual_control_strip_replaces_legacy_controls_and_reset_clears_overrides():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()

    expected_actions = (
        "Start P-100 manually",
        "Open V-099 manually",
        "Open V-100 manually",
        "Start P-201 manually",
        "Open V-201 manually",
        "Start P-202 manually",
        "Open V-202 manually",
    )
    labels = {button.label for button in app.button}
    assert all(label in labels for label in expected_actions)
    assert "Disable input supply auto P-100 / V-099" not in labels
    assert "Disable feed valve V-100 auto" not in labels

    button_with_label(app, "Start P-201 manually").click().run()
    assert app.session_state.device_overrides["P201"] == "FORCE_ON"
    assert app.session_state.device_overrides["V201"] == "AUTO"
    assert app.session_state.bus.tags["DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN"] is False

    button_with_label(app, "Reset simulation").click().run()
    assert set(app.session_state.device_overrides.values()) == {"AUTO"}


def test_manual_equipment_is_marked_in_process_overview():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Start P-100 manually").click().run()

    markup = process_overview_markup(app)
    assert re.search(r'class="feed-supply-pump running[^\"]* manual', markup)
    assert "Manual control highlight" in markup
    assert ".dt101-board .feed-supply-pump.manual" in markup
    assert any(
        'class="manual-mode-badge"' in markdown.value and "MANUAL" in markdown.value
        for markdown in app.markdown
    )


def test_input_supply_overview_uses_actual_pump_valve_and_flow_feedback():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    stopped_markup = process_overview_markup(app)

    assert "Input" in stopped_markup
    assert "P-100 STOPPED" in stopped_markup
    assert "V-099 CLOSED" in stopped_markup
    assert "Inlet flow 00.0 L/s" in stopped_markup
    assert 'class="feed-supply-pump stopped' in stopped_markup
    assert 'class="pump-rotor"' in stopped_markup
    assert 'class="feed-supply-valve closed' in stopped_markup

    button_with_label(app, "Single PLC scan + process tick").click().run()
    running_markup = process_overview_markup(app)

    assert "P-100 RUNNING" in running_markup
    assert "V-099 OPEN" in running_markup
    assert "Inlet flow 10.0 L/s" in running_markup
    assert 'class="feed-supply-pump running' in running_markup
    assert 'class="feed-supply-valve open' in running_markup
    assert "@keyframes pump-rotation" in running_markup


def test_feed_capacity_trip_forces_all_devices_off_but_allows_v100_drainage():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=90.0)

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert set(app.session_state.device_overrides.values()) == {"FORCE_OFF"}
    assert app.session_state.capacity_trip_alarms == ["DT101.ALARM.FEED_TANK_OVERFILL"]
    assert button_with_label(app, "Start P-100 manually").disabled is True
    assert button_with_label(app, "Open V-099 manually").disabled is True
    assert button_with_label(app, "Open V-100 manually").disabled is False

    button_with_label(app, "Open V-100 manually").click().run()

    assert app.session_state.state.feed_flow == 20.0
    assert app.session_state.state.feed_tank_level < 90.0
    assert app.session_state.device_overrides["V100"] == "FORCE_OFF"
    assert app.session_state.bus.tags["DT101.FB.FEED_VALVE_OPEN"] is False


def test_product_capacity_trip_locks_v100_but_keeps_product_drain_controls_available():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, distillate_tank_level=90.0)

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert button_with_label(app, "Open V-100 manually").disabled is True
    assert button_with_label(app, "Start P-201 manually").disabled is False
    assert button_with_label(app, "Open V-201 manually").disabled is False

    button_with_label(app, "Start P-201 manually").click().run()
    button_with_label(app, "Open V-201 manually").click().run()

    assert app.session_state.state.distillate_outlet_flow == 20.0
    assert app.session_state.state.distillate_tank_level < 90.0
    assert app.session_state.capacity_trip_alarms == []
    assert set(app.session_state.device_overrides.values()) == {"FORCE_OFF"}


def test_post_step_capacity_crossing_trips_without_waiting_for_another_scan():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=89.5)
    app.session_state.device_overrides.update({"P100": "FORCE_ON", "V099": "FORCE_ON", "V100": "FORCE_OFF"})

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert app.session_state.state.feed_tank_level >= 90.0
    assert app.session_state.capacity_trip_alarms == ["DT101.ALARM.FEED_TANK_OVERFILL"]
    assert set(app.session_state.device_overrides.values()) == {"FORCE_OFF"}
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is False
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is False


def test_capacity_alarm_text_clock_resets_with_simulation():
    alarm = "DT101.ALARM.FEED_TANK_OVERFILL"
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=90.0)
    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert alarm in app.session_state.active_alarms
    assert alarm in app.session_state.alarm_text_first_triggered_at

    button_with_label(app, "Reset simulation").click().run()
    assert app.session_state.alarm_text_first_triggered_at == {}


def test_ai_recommendation_updates_automatically_when_alarm_triggers():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=90.0)

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert "DT101.ALARM.FEED_TANK_OVERFILL" in app.session_state.active_alarms
    assert "manually open V-100" in app.session_state.last_ai_response
    assert "90%" in app.session_state.last_ai_response


def test_ai_recommendation_does_not_auto_update_for_automatic_process_alarm():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.state = replace(app.session_state.state, feed_tank_level=80.0)

    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert "DT101.ALARM.FEED_TANK_HIGH_HIGH" in app.session_state.active_alarms
    assert "DT101.ALARM.FEED_TANK_OVERFILL" not in app.session_state.active_alarms
    assert app.session_state.last_ai_response == "No recommendation requested yet."
    assert app.session_state.last_ai_alarm_signature == ()


def test_condenser_inline_data_text_is_removed_without_removing_profile_data():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    markup = process_overview_markup(app)

    assert "Cooling water " not in markup
    assert not re.search(r"Valve [0-9.]+%<br/>Cooling water", markup)
    app.radio[0].set_value("Condenser").run()
    assert "Cooling water flow" in app.dataframe[0].value["Variable"].tolist()


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
        "DT101.PV.DISTILLATE_OUTLET_FLOW": 20.0,
        "DT101.CMD.BOTTOMS_EXPORT_PUMP": True,
        "DT101.CMD.BOTTOMS_EXPORT_VALVE": True,
        "DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING": True,
        "DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN": True,
        "DT101.PV.BOTTOMS_OUTLET_FLOW": 20.0,
    }
    assert all(app.session_state.bus.tags[tag] == value for tag, value in expected_running.items())
    assert all(tag in TAG_DICTIONARY for tag in expected_running)
    running_markup = process_overview_markup(app)
    assert "P-201 RUNNING" in running_markup
    assert "V-201 OPEN" in running_markup
    assert "P-202 RUNNING" in running_markup
    assert "V-202 OPEN" in running_markup
    assert running_markup.count("Outlet flow 20.0 L/s") == 2
    assert running_markup.count('class="product-export-pump running') == 2
    assert "flow-active" in route_classes(running_markup, "distillate-export")
    assert "flow-active" in route_classes(running_markup, "bottoms-export")

    app.session_state.bus.tags["DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN"] = False
    app.session_state.bus.tags["DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING"] = False
    app.run()
    broken_interlock_markup = process_overview_markup(app)
    assert "flow-stopped" in route_classes(broken_interlock_markup, "distillate-export")
    assert "flow-stopped" in route_classes(broken_interlock_markup, "bottoms-export")

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

    assert button_with_label(app, "Start P-201 manually")
    assert button_with_label(app, "Start P-202 manually")


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
    assert len(app.get("plotly_chart")) == 4

    button_with_label(app, "Single PLC scan + process tick").click().run()

    rows = app.session_state.historian.query(ticks=10)
    assert {row["tick"] for row in rows} == {0, 1}
    assert app.session_state.tick_count == 1
    assert len(app.get("plotly_chart")) == 4


def test_historian_trends_are_grouped_by_overview_process_equipment_and_tanks():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Single PLC scan + process tick").click().run()

    charts = [json.loads(chart.proto.spec) for chart in app.get("plotly_chart")]
    process_chart, equipment_chart, tank_chart = charts[:3]

    assert tuple(trace["name"] for trace in process_chart["data"]) == visualization_module.PROCESS_TREND_TAGS
    assert tuple(trace["name"] for trace in equipment_chart["data"]) == visualization_module.EQUIPMENT_STATE_TREND_TAGS
    assert tuple(trace["name"] for trace in tank_chart["data"]) == visualization_module.TANK_LEVEL_TREND_TAGS
    assert all(trace["line"]["shape"] == "hv" for trace in equipment_chart["data"])
    assert all(trace["y"]["dtype"] == "i1" for trace in equipment_chart["data"])
    assert tank_chart["layout"]["yaxis"]["title"]["text"] == "Tank level (% vol)"
    assert tank_chart["layout"]["yaxis"]["range"] == [0, 100]


def test_ai_history_keeps_the_full_diagnostic_tag_set(monkeypatch, tmp_path):
    expected_tags = (
        "DT101.PV.TOP_TEMP",
        "DT101.SP.TOP_TEMP",
        "DT101.PV.BOTTOM_TEMP",
        "DT101.SP.BOTTOM_TEMP",
        "DT101.CMD.FEED_VALVE",
        "DT101.PV.COLUMN_PRESSURE",
        "DT101.PV.PURITY_PROXY",
        "DT101.CMD.REBOILER_DUTY",
        "DT101.CMD.CONDENSER_VALVE",
        "DT101.CMD.REFLUX_VALVE",
        "DT101.FB.REFLUX_VALVE_POSITION",
    )
    query_calls = []

    class RecordingHistorian(historian_module.Historian):
        def __init__(self, _db_path):
            super().__init__(tmp_path / "ai-history.sqlite")

        def query(self, tag_names=None, seconds=120, ticks=None):
            query_calls.append((tuple(tag_names or ()), ticks))
            return super().query(tag_names, seconds, ticks)

    monkeypatch.setattr(historian_module, "Historian", RecordingHistorian)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert (expected_tags, 120) in query_calls


def test_thingsboard_cloud_upload_sends_only_the_four_visible_trend_groups(monkeypatch):
    upload_calls = []
    monkeypatch.setenv("THINGSBOARD_ACCESS_TOKEN", "device-token")

    class RecordingBridge:
        def __init__(self, *, host, access_token, timeout=5.0):
            self.host = host
            self.access_token = access_token
            self.timeout = timeout

        def upload_rows(self, rows):
            upload_calls.append(list(rows))
            return cloud_bridge_module.CloudUploadResult(sent=True, points=len(upload_calls[-1]), status_code=200)

    monkeypatch.setattr(cloud_bridge_module, "ThingsBoardCloudBridge", RecordingBridge)
    monkeypatch.setattr(cloud_bridge_module, "MqttThingsBoardCloudBridge", RecordingBridge)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state.thingsboard_cloud_enabled = True
    button_with_label(app, "Reset simulation").click().run()
    app.session_state.thingsboard_cloud_enabled = True
    button_with_label(app, "Single PLC scan + process tick").click().run()

    expected_tags = (
        *visualization_module.PROCESS_TREND_TAGS,
        *visualization_module.EQUIPMENT_STATE_TREND_TAGS,
        *visualization_module.TANK_LEVEL_TREND_TAGS,
        *visualization_module.LAYER_TEMPERATURE_TAGS,
    )
    assert upload_calls
    uploaded_tags = tuple(row["tag"] for row in upload_calls[-1])
    assert uploaded_tags == expected_tags
    assert app.session_state.last_cloud_upload_tick == app.session_state.tick_count
    assert "uploaded" in app.session_state.cloud_upload_status.lower()


def test_thingsboard_cloud_upload_uses_mqtt_bridge_when_configured(monkeypatch):
    upload_calls = []

    class RecordingMqttBridge:
        def __init__(self, *, host, access_token, timeout=5.0):
            self.host = host
            self.access_token = access_token
            self.timeout = timeout

        def upload_rows(self, rows):
            upload_calls.append((self.host, self.access_token, list(rows)))
            return cloud_bridge_module.CloudUploadResult(sent=True, points=len(upload_calls[-1][2]), message="MQTT")

    monkeypatch.setenv("THINGSBOARD_ACCESS_TOKEN", "device-token")
    monkeypatch.setenv("THINGSBOARD_TRANSPORT", "mqtt")
    monkeypatch.delenv("THINGSBOARD_MQTT_HOST", raising=False)
    monkeypatch.setattr(cloud_bridge_module, "MqttThingsBoardCloudBridge", RecordingMqttBridge)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Reset simulation").click().run()
    button_with_label(app, "Single PLC scan + process tick").click().run()

    assert upload_calls
    assert upload_calls[-1][0] == "mqtt.thingsboard.cloud"
    assert upload_calls[-1][1]
    assert "MQTT" in app.session_state.cloud_upload_status


def test_thingsboard_cloud_sidebar_exposes_status_and_manual_flush(monkeypatch):
    monkeypatch.delenv("THINGSBOARD_ACCESS_TOKEN", raising=False)

    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    labels = {button.label for button in app.button}
    markdown_text = "\n".join(markdown.value for markdown in app.markdown)
    captions = "\n".join(caption.value for caption in app.caption)

    assert "Upload last 600 ticks to ThingsBoard" in labels
    assert "ThingsBoard cloud" in markdown_text
    assert "THINGSBOARD_ACCESS_TOKEN" in captions or "Transport:" in captions


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
    layer_chart = json.loads(app.get("plotly_chart")[3].proto.spec)

    assert layer_chart["layout"]["xaxis"]["title"]["text"] == "Second"
