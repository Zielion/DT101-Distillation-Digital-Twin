from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import reload
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from distillation import config as config_module


# Refresh shared constants before importing modules that depend on them.
if not all(
    hasattr(config_module, name)
    for name in (
        "FEED_TANK_MAX_CAPACITY_L",
        "DISTILLATE_TANK_MAX_CAPACITY_L",
        "BOTTOMS_TANK_MAX_CAPACITY_L",
        "FEED_SUPPLY_FLOW_LPM",
        "FEED_TANK_HIGH_HIGH",
        "PRODUCT_EXPORT_FLOW_LPM",
        "PRODUCT_EXPORT_START_LEVEL",
        "PRODUCT_EXPORT_STOP_LEVEL",
    )
):
    config_module = reload(config_module)

from distillation.ai_assistant import AIAssistant
from distillation.faults import FaultManager
from distillation import historian as historian_module
from distillation import plc as plc_module
from distillation import process as process_module
from distillation import tags as tags_module
from distillation import visualization as visualization_module


# Streamlit can preserve older imported modules while hot-reloading app.py.
if getattr(plc_module, "PLC_CONTROL_REVISION", None) != 3 or not all(
    name in getattr(plc_module.PLCController, "__dataclass_fields__", {})
    for name in ("feed_cycle_phase", "distillate_export_running", "bottoms_export_running")
):
    plc_module = reload(plc_module)
if getattr(process_module, "PROCESS_MODEL_REVISION", None) != 3 or not all(
    name in getattr(process_module.ProcessState, "__dataclass_fields__", {})
    for name in ("feed_inlet_flow", "distillate_outlet_flow", "bottoms_outlet_flow")
):
    process_module = reload(process_module)
if not all(
    name in tags_module.TAG_DICTIONARY
    for name in ("DT101.PV.FEED_INLET_FLOW", "DT101.PV.DISTILLATE_OUTLET_FLOW", "DT101.PV.BOTTOMS_OUTLET_FLOW")
):
    tags_module = reload(tags_module)
if not hasattr(historian_module.Historian, "latest_tick"):
    historian_module = reload(historian_module)
if getattr(visualization_module, "LAYER_CHART_X_FIELD", None) != "tick":
    visualization_module = reload(visualization_module)

Historian = historian_module.Historian
TagBus = historian_module.TagBus
PLCController = plc_module.PLCController
ProcessState = process_module.ProcessState
derive_column_layer_temperatures = process_module.derive_column_layer_temperatures
normalize_process_state = process_module.normalize_process_state
TAG_DICTIONARY = tags_module.TAG_DICTIONARY
BOTTOM_TEMP_SETPOINT = config_module.BOTTOM_TEMP_SETPOINT
TOP_TEMP_SETPOINT = config_module.TOP_TEMP_SETPOINT
HISTORIAN_DB = config_module.HISTORIAN_DB
FEED_TANK_MAX_CAPACITY_L = config_module.FEED_TANK_MAX_CAPACITY_L
DISTILLATE_TANK_MAX_CAPACITY_L = config_module.DISTILLATE_TANK_MAX_CAPACITY_L
BOTTOMS_TANK_MAX_CAPACITY_L = config_module.BOTTOMS_TANK_MAX_CAPACITY_L
LAYER_TEMPERATURE_TAGS = visualization_module.LAYER_TEMPERATURE_TAGS
build_layer_temperature_figure = visualization_module.build_layer_temperature_figure


st.set_page_config(page_title="DT101 Distillation Digital Twin", layout="wide")


def init_session() -> None:
    if "state" not in st.session_state:
        st.session_state.state = ProcessState()
    else:
        st.session_state.state = normalize_process_state(st.session_state.state)
    if "plc" not in st.session_state:
        st.session_state.plc = PLCController(mode="IDLE")
    elif not all(
        hasattr(st.session_state.plc, name)
        for name in (
            "feed_cycle_phase",
            "distillate_export_running",
            "bottoms_export_running",
            "_product_export_state",
        )
    ):
        stale_plc = st.session_state.plc
        st.session_state.plc = PLCController(
            mode=str(getattr(stale_plc, "mode", "IDLE")),
            top_temp_setpoint=float(getattr(stale_plc, "top_temp_setpoint", TOP_TEMP_SETPOINT)),
            bottom_temp_setpoint=float(getattr(stale_plc, "bottom_temp_setpoint", BOTTOM_TEMP_SETPOINT)),
            stable_seconds=float(getattr(stale_plc, "stable_seconds", 0.0)),
            feed_cycle_phase=(
                "FILLING_FEED_TANK"
                if float(st.session_state.state.feed_tank_level) <= 10.0
                else "FEEDING_COLUMN"
            ),
        )
    if "faults" not in st.session_state:
        st.session_state.faults = FaultManager()
    if "bus" not in st.session_state:
        st.session_state.bus = TagBus()
    if "historian" not in st.session_state or not hasattr(st.session_state.historian, "latest_tick"):
        st.session_state.historian = Historian(Path(HISTORIAN_DB))
    if "last_heartbeat" not in st.session_state:
        st.session_state.last_heartbeat = datetime.now(timezone.utc)
    if "active_alarms" not in st.session_state:
        st.session_state.active_alarms = []
    if "last_ai_response" not in st.session_state:
        st.session_state.last_ai_response = "No recommendation requested yet."
    if "selected_equipment" not in st.session_state:
        st.session_state.selected_equipment = "Column"
    if "top_temp_setpoint" not in st.session_state:
        st.session_state.top_temp_setpoint = TOP_TEMP_SETPOINT
    if "bottom_temp_setpoint" not in st.session_state:
        st.session_state.bottom_temp_setpoint = BOTTOM_TEMP_SETPOINT
    if "feed_valve_open" not in st.session_state:
        st.session_state.feed_valve_open = True
    if "feed_supply_run_request" not in st.session_state:
        st.session_state.feed_supply_run_request = True
    if "continuous_run" not in st.session_state:
        st.session_state.continuous_run = False
    if "continuous_run_skip_next" not in st.session_state:
        st.session_state.continuous_run_skip_next = False
    if "tick_count" not in st.session_state:
        latest_tick = st.session_state.historian.latest_tick()
        if latest_tick is None:
            st.session_state.historian.write(
                datetime.now(timezone.utc),
                st.session_state.state.to_tags(),
                tick=0,
            )
            latest_tick = 0
        st.session_state.tick_count = latest_tick


def reset_simulation() -> None:
    db = Path(HISTORIAN_DB)
    st.session_state.state = ProcessState()
    st.session_state.plc = PLCController(mode="IDLE")
    st.session_state.faults = FaultManager()
    st.session_state.bus = TagBus()
    st.session_state.historian = Historian(db)
    st.session_state.historian.clear()
    st.session_state.last_heartbeat = datetime.now(timezone.utc)
    st.session_state.active_alarms = []
    st.session_state.last_ai_response = "No recommendation requested yet."
    st.session_state.feed_valve_open = True
    st.session_state.feed_supply_run_request = True
    st.session_state.continuous_run = False
    st.session_state.continuous_run_skip_next = False
    st.session_state.tick_count = 0
    initial_tags = {
        **st.session_state.state.to_tags(),
        "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": True,
        "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True,
        "DT101.CMD.FEED_SUPPLY_PUMP": False,
        "DT101.CMD.FEED_SUPPLY_VALVE": False,
        "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": False,
        "DT101.FB.FEED_SUPPLY_VALVE_OPEN": False,
        "DT101.CMD.FEED_VALVE": 0.0,
        "DT101.FB.FEED_VALVE_OPEN": False,
    }
    st.session_state.bus.publish(initial_tags)
    st.session_state.historian.write(
        st.session_state.last_heartbeat,
        initial_tags,
        tick=0,
    )


def state_with_manual_temperatures(state: ProcessState, top_temperature: float, bottom_temperature: float) -> ProcessState:
    state = normalize_process_state(state)
    top_temperature = float(top_temperature)
    bottom_temperature = float(bottom_temperature)
    return replace(
        state,
        top_temperature=top_temperature,
        bottom_temperature=bottom_temperature,
    )


def simulation_tick() -> None:
    now = datetime.now(timezone.utc)
    next_tick = int(st.session_state.tick_count) + 1
    faults = st.session_state.faults.apply()
    if "data_stale" not in faults:
        st.session_state.last_heartbeat = now

    state = state_with_manual_temperatures(
        st.session_state.state,
        st.session_state.top_temp_setpoint,
        st.session_state.bottom_temp_setpoint,
    )
    snapshot = state.to_tags()
    snapshot["DT101.SP.TOP_TEMP"] = float(st.session_state.top_temp_setpoint)
    snapshot["DT101.SP.BOTTOM_TEMP"] = float(st.session_state.bottom_temp_setpoint)
    snapshot["DT101.HMI.FEED_VALVE_OPEN_REQUEST"] = bool(st.session_state.feed_valve_open)
    snapshot["DT101.HMI.FEED_SUPPLY_RUN_REQUEST"] = bool(st.session_state.feed_supply_run_request)
    snapshot["DT101.FB.FEED_VALVE_OPEN"] = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_VALVE_OPEN", False)
    )
    snapshot["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_PUMP_RUNNING", False)
    )
    snapshot["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_VALVE_OPEN", False)
    )
    st.session_state.plc.top_temp_setpoint = float(st.session_state.top_temp_setpoint)
    st.session_state.plc.bottom_temp_setpoint = float(st.session_state.bottom_temp_setpoint)
    plc_output = st.session_state.plc.scan(snapshot, 1.0)
    controls = dict(plc_output.commands)
    controls["feed_supply_pump_feedback"] = bool(plc_output.commands["feed_supply_pump"])
    controls["feed_supply_valve_feedback"] = bool(plc_output.commands["feed_supply_valve"])
    controls["distillate_export_pump_feedback"] = bool(plc_output.commands["distillate_export_pump"])
    controls["distillate_export_valve_feedback"] = bool(plc_output.commands["distillate_export_valve"])
    controls["bottoms_export_pump_feedback"] = bool(plc_output.commands["bottoms_export_pump"])
    controls["bottoms_export_valve_feedback"] = bool(plc_output.commands["bottoms_export_valve"])
    controls["feed_valve_feedback"] = float(plc_output.commands["feed_valve"]) > 0.0
    controls["reflux_valve_feedback"] = faults.get("reflux_valve_stuck_position", plc_output.commands["reflux_valve"])
    controls["last_heartbeat"] = st.session_state.last_heartbeat

    process_commands = dict(plc_output.commands)
    process_commands["top_temperature"] = float(st.session_state.top_temp_setpoint)
    process_commands["bottom_temperature"] = float(st.session_state.bottom_temp_setpoint)
    state = state.step(process_commands, faults, 1.0)
    tags = state.to_tags()
    if "top_temp_drift" in faults:
        tags["DT101.PV.TOP_TEMP"] = tags["DT101.PV.TOP_TEMP"] + float(faults["top_temp_drift"])

    fault_alarms = st.session_state.faults.detect(tags, controls, now)
    all_alarms = sorted(set(plc_output.alarms + fault_alarms))

    output_tags = {
        **tags,
        "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": st.session_state.feed_supply_run_request,
        "DT101.CMD.FEED_SUPPLY_PUMP": plc_output.commands["feed_supply_pump"],
        "DT101.CMD.FEED_SUPPLY_VALVE": plc_output.commands["feed_supply_valve"],
        "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": controls["feed_supply_pump_feedback"],
        "DT101.FB.FEED_SUPPLY_VALVE_OPEN": controls["feed_supply_valve_feedback"],
        "DT101.CMD.DISTILLATE_EXPORT_PUMP": plc_output.commands["distillate_export_pump"],
        "DT101.CMD.DISTILLATE_EXPORT_VALVE": plc_output.commands["distillate_export_valve"],
        "DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING": controls["distillate_export_pump_feedback"],
        "DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN": controls["distillate_export_valve_feedback"],
        "DT101.CMD.BOTTOMS_EXPORT_PUMP": plc_output.commands["bottoms_export_pump"],
        "DT101.CMD.BOTTOMS_EXPORT_VALVE": plc_output.commands["bottoms_export_valve"],
        "DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING": controls["bottoms_export_pump_feedback"],
        "DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN": controls["bottoms_export_valve_feedback"],
        "DT101.CMD.REBOILER_DUTY": plc_output.commands["reboiler_duty"],
        "DT101.HMI.FEED_VALVE_OPEN_REQUEST": st.session_state.feed_valve_open,
        "DT101.CMD.FEED_VALVE": plc_output.commands["feed_valve"],
        "DT101.FB.FEED_VALVE_OPEN": controls["feed_valve_feedback"],
        "DT101.SP.TOP_TEMP": st.session_state.top_temp_setpoint,
        "DT101.SP.BOTTOM_TEMP": st.session_state.bottom_temp_setpoint,
        "DT101.CMD.CONDENSER_VALVE": plc_output.commands["condenser_valve"],
        "DT101.CMD.REFLUX_VALVE": plc_output.commands["reflux_valve"],
        "DT101.FB.REFLUX_VALVE_POSITION": controls["reflux_valve_feedback"],
        "DT101.STATE.MODE": plc_output.mode,
        "DT101.HEARTBEAT.PLC": st.session_state.last_heartbeat.isoformat(),
    }
    for alarm in all_alarms:
        output_tags[alarm] = True

    if "data_stale" not in faults:
        st.session_state.bus.publish(output_tags)
        st.session_state.historian.write(now, output_tags, tick=next_tick)

    st.session_state.state = state
    st.session_state.active_alarms = all_alarms
    st.session_state.tick_count = next_tick


def toggle_feed_valve() -> None:
    st.session_state.feed_valve_open = not st.session_state.feed_valve_open
    simulation_tick()


def toggle_feed_supply() -> None:
    st.session_state.feed_supply_run_request = not st.session_state.feed_supply_run_request
    simulation_tick()


def toggle_continuous_run() -> None:
    st.session_state.continuous_run = not st.session_state.continuous_run
    st.session_state.continuous_run_skip_next = st.session_state.continuous_run


def inject_button(label: str, fault_name: str) -> None:
    active = fault_name in st.session_state.faults.active_faults
    if st.button(("Clear " if active else "Inject ") + label, use_container_width=True):
        if active:
            st.session_state.faults.clear(fault_name)
        else:
            st.session_state.faults.inject(fault_name)


def recent_dataframe(tags: list[str], ticks: int = 300) -> pd.DataFrame:
    rows = st.session_state.historian.query(tags, ticks=ticks)
    if not rows:
        return pd.DataFrame(columns=["tick", "tag", "value"])
    df = pd.DataFrame(rows)
    df["numeric_value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def column_layer_watch_values(state: ProcessState) -> dict[str, str]:
    layers = get_column_layer_temperatures(state)
    rows: dict[str, str] = {}
    for layer_number in range(7, 0, -1):
        if layer_number == 7:
            label = "Layer 7 - Top"
        elif layer_number == 1:
            label = "Layer 1 - Bottom"
        else:
            label = f"Layer {layer_number} - Middle {layer_number - 1}"
        rows[label] = f"{layers[layer_number - 1]:.1f} degC"
    rows["Pressure"] = f"{state.column_pressure:.1f} kPa"
    return rows


def get_column_layer_temperatures(state: ProcessState) -> tuple[float, ...]:
    return derive_column_layer_temperatures(state)


def process_overview_svg(state: ProcessState, mode: str, alarms: list[str], selected_equipment: str) -> str:
    pressure_color = "#ff5f63" if state.column_pressure > 125 else "#bff6ff"
    purity_color = "#ffc26b" if state.purity_proxy < 90 else "#bff6ff"
    heating_duty = float(st.session_state.bus.tags.get("DT101.CMD.REBOILER_DUTY", 0.0))
    condenser_command = float(st.session_state.bus.tags.get("DT101.CMD.CONDENSER_VALVE", 0.0))
    feed_valve_command = float(
        st.session_state.bus.tags.get(
            "DT101.CMD.FEED_VALVE",
            0.0,
        )
    )
    feed_valve_open = bool(
        st.session_state.bus.tags.get(
            "DT101.FB.FEED_VALVE_OPEN",
            False,
        )
    )
    feed_valve_class = " open" if feed_valve_open else " closed"
    feed_valve_status = "OPEN" if feed_valve_open else "CLOSED"
    feed_supply_pump_running = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_PUMP_RUNNING", False)
    )
    feed_supply_valve_open = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_VALVE_OPEN", False)
    )
    feed_supply_pump_class = "running" if feed_supply_pump_running else "stopped"
    feed_supply_valve_class = "open" if feed_supply_valve_open else "closed"
    feed_supply_pump_status = "RUNNING" if feed_supply_pump_running else "STOPPED"
    feed_supply_valve_status = "OPEN" if feed_supply_valve_open else "CLOSED"
    feed_supply_active = feed_supply_pump_running and feed_supply_valve_open
    distillate_export_pump_running = bool(
        st.session_state.bus.tags.get("DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING", False)
    )
    distillate_export_valve_open = bool(
        st.session_state.bus.tags.get("DT101.FB.DISTILLATE_EXPORT_VALVE_OPEN", False)
    )
    bottoms_export_pump_running = bool(
        st.session_state.bus.tags.get("DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING", False)
    )
    bottoms_export_valve_open = bool(
        st.session_state.bus.tags.get("DT101.FB.BOTTOMS_EXPORT_VALVE_OPEN", False)
    )

    def device_state(active: bool, running_label: str, stopped_label: str) -> tuple[str, str]:
        return ("running" if active else "stopped", running_label if active else stopped_label)

    distillate_pump_class, distillate_pump_status = device_state(
        distillate_export_pump_running, "RUNNING", "STOPPED"
    )
    distillate_valve_class, distillate_valve_status = device_state(
        distillate_export_valve_open, "OPEN", "CLOSED"
    )
    bottoms_pump_class, bottoms_pump_status = device_state(
        bottoms_export_pump_running, "RUNNING", "STOPPED"
    )
    bottoms_valve_class, bottoms_valve_status = device_state(
        bottoms_export_valve_open, "OPEN", "CLOSED"
    )
    distillate_export_active = distillate_export_pump_running and distillate_export_valve_open
    bottoms_export_active = bottoms_export_pump_running and bottoms_export_valve_open
    feed_level = max(0.0, min(100.0, state.feed_tank_level))
    distillate_level = max(0.0, min(100.0, state.distillate_tank_level))
    bottoms_level = max(0.0, min(100.0, state.bottoms_tank_level))
    feed_inventory_liters = feed_level / 100.0 * FEED_TANK_MAX_CAPACITY_L
    distillate_inventory_liters = distillate_level / 100.0 * DISTILLATE_TANK_MAX_CAPACITY_L
    bottoms_inventory_liters = bottoms_level / 100.0 * BOTTOMS_TANK_MAX_CAPACITY_L

    def selected(name: str) -> str:
        return " selected" if selected_equipment == name else ""

    layers = get_column_layer_temperatures(state)
    layer_band_html = "\n".join(
        f"""    <div class="layer-band">
      <span>L{7 - index}</span>
      <b>{temperature:04.1f} degC</b>
    </div>"""
        for index, temperature in enumerate(reversed(layers))
    )

    return f"""
<div class="scada-wrap">
<style>
.dt101-board {{
  position: relative;
  min-height: 425px;
  overflow-x: auto;
  overflow-y: hidden;
  border-radius: 20px;
  border: 1px solid rgba(124, 226, 255, 0.28);
  background:
    linear-gradient(rgba(83, 214, 236, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(83, 214, 236, 0.06) 1px, transparent 1px),
    radial-gradient(circle at 42% 42%, rgba(15, 103, 118, 0.68), rgba(5, 26, 37, 0.95) 62%, #020811 100%);
  background-size: 64px 64px, 64px 64px, 100% 100%;
  color: #d9fbff;
  font-family: "Segoe UI", Arial, sans-serif;
}}
.dt101-board * {{ box-sizing: border-box; }}
.dt101-stage {{
  position: relative;
  width: min(1040px, calc(100% - 32px));
  min-width: 920px;
  height: 425px;
  margin: 0 auto;
}}
.dt101-equipment {{
  position: absolute;
  border: 2px solid rgba(144, 237, 255, 0.62);
  background: rgba(8, 43, 56, 0.72);
  box-shadow: inset 0 0 22px rgba(126, 236, 255, 0.09), 0 0 18px rgba(69, 214, 255, 0.10);
}}
.dt101-equipment.selected {{
  border-color: #ffe681;
  box-shadow: 0 0 24px rgba(255, 230, 129, 0.34), inset 0 0 20px rgba(255, 230, 129, 0.08);
}}
.equipment-name {{
  position: absolute;
  color: #dffcff;
  font-size: clamp(11px, 1.25vw, 14px);
  font-weight: 800;
  letter-spacing: 0.35px;
  text-transform: uppercase;
}}
.equipment-data {{
  position: absolute;
  color: #b7edf5;
  font: 700 clamp(9px, 1vw, 11px) Consolas, monospace;
  line-height: 1.45;
}}
.process-line {{
  position: absolute;
  border-radius: 999px;
  z-index: 1;
  background: linear-gradient(90deg, rgba(69,214,255,0.28), #45d6ff);
  box-shadow: 0 0 14px rgba(69,214,255,0.55);
  color: #45d6ff;
}}
.process-line.hot {{
  background: linear-gradient(90deg, rgba(255,173,120,0.24), #ffad78);
  box-shadow: 0 0 14px rgba(255,173,120,0.52);
  color: #ffad78;
}}
.process-line.closed {{
  opacity: 0.26;
  filter: grayscale(0.7);
  box-shadow: none;
}}
.process-line.selected {{
  box-shadow: 0 0 20px rgba(255, 230, 129, 0.72);
}}
.process-line.horizontal {{ height: 6px; }}
.process-line.vertical {{ width: 6px; }}
.process-line.right::after {{
  content: "";
  position: absolute; right: -10px; top: -5px;
  width: 0; height: 0;
  border-top: 8px solid transparent;
  border-bottom: 8px solid transparent;
  border-left: 12px solid currentColor;
}}
.process-line.down::after {{
  content: "";
  position: absolute; left: -5px; bottom: -10px;
  width: 0; height: 0;
  border-left: 8px solid transparent;
  border-right: 8px solid transparent;
  border-top: 12px solid currentColor;
}}
.column {{
  left: 42%; top: 48px; width: 17%; height: 300px;
  border-radius: 56px;
  overflow: hidden;
  background: linear-gradient(135deg, rgba(217,251,255,0.20), rgba(69,214,255,0.08), rgba(3,24,36,0.62));
}}
.column-layers {{
  position: absolute;
  left: 14px; right: 14px; top: 15px; bottom: 58px;
  display: grid;
  grid-template-rows: repeat(7, 1fr);
  gap: 4px;
}}
.layer-band {{
  min-height: 24px;
  border-top: 1px solid rgba(191,246,255,0.58);
  border-bottom: 1px solid rgba(191,246,255,0.22);
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(69,214,255,0.10), rgba(255,173,120,0.08));
  color: #dffcff;
  font: 700 10px Consolas, monospace;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 9px;
}}
.layer-band span {{ color: #86d7e5; }}
.layer-band b {{ color: #ffffff; font-size: 10px; }}
.column-heater {{
  position: absolute;
  left: 17px; right: 17px; bottom: 13px; height: 34px;
  border: 1px solid rgba(255,173,120,0.72);
  border-radius: 17px;
  overflow: hidden;
  background: rgba(37, 24, 24, 0.75);
}}
.column-heater-fill {{
  height: 100%;
  width: {heating_duty:.1f}%;
  background: linear-gradient(90deg, #45d6ff, #ff7e5f 50%, #ffe29a);
  box-shadow: 0 0 16px rgba(255,126,95,0.55);
  opacity: 0.88;
}}
.column-heater-label {{
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: white; font: 800 9px Consolas, monospace;
  text-shadow: 0 1px 3px #000;
}}
.feed-tank {{
  left: 20%; top: 78px; width: 15%; height: 240px;
  border-radius: 38px;
  overflow: hidden;
}}
.feed-valve {{
  position: absolute;
  left: 37%;
  top: 192px;
  width: 46px;
  height: 34px;
  z-index: 4;
  color: #e8ffff;
}}
.feed-valve .valve-line {{
  position: absolute;
  left: 0;
  right: 0;
  top: 16px;
  height: 2px;
  background: rgba(232,255,255,0.90);
}}
.feed-valve .valve-left,
.feed-valve .valve-right {{
  position: absolute;
  top: 8px;
  width: 0;
  height: 0;
  border-top: 9px solid transparent;
  border-bottom: 9px solid transparent;
  filter: drop-shadow(0 0 4px rgba(69,214,255,0.65));
}}
.feed-valve .valve-left {{
  left: 8px;
  border-left: 15px solid #f7ffff;
}}
.feed-valve .valve-right {{
  right: 8px;
  border-right: 15px solid #f7ffff;
}}
.feed-valve .valve-stem {{
  position: absolute;
  left: 22px;
  top: 0;
  width: 2px;
  height: 10px;
  background: rgba(232,255,255,0.90);
}}
.feed-valve.open .valve-left,
.feed-valve.open .valve-right {{
  filter: drop-shadow(0 0 8px rgba(37,230,165,0.95));
}}
.feed-valve.closed {{
  opacity: 0.55;
  color: #ffad78;
}}
.feed-valve.closed .valve-line,
.feed-valve.closed .valve-stem {{
  background: rgba(255,173,120,0.70);
}}
.feed-valve.closed .valve-left {{
  border-left-color: #ffad78;
}}
.feed-valve.closed .valve-right {{
  border-right-color: #ffad78;
}}
.feed-supply-pump {{
  position: absolute;
  left: 3.2%;
  top: 181px;
  width: 54px;
  height: 54px;
  z-index: 4;
  border: 3px solid rgba(174,247,255,0.72);
  border-radius: 50%;
  background: rgba(8,43,56,0.94);
  box-shadow: 0 0 14px rgba(69,214,255,0.25), inset 0 0 12px rgba(69,214,255,0.15);
}}
.feed-supply-pump.running {{
  border-color: #25e6a5;
  box-shadow: 0 0 18px rgba(37,230,165,0.72), inset 0 0 14px rgba(69,214,255,0.24);
}}
.feed-supply-pump.stopped {{ opacity: 0.62; }}
.pump-rotor {{
  position: absolute;
  inset: 5px;
  border: 2px solid rgba(191,246,255,0.72);
  border-radius: 50%;
}}
.pump-hub {{
  position: absolute;
  left: 50%; top: 50%;
  width: 12px; height: 12px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: #dffcff;
  box-shadow: 0 0 7px rgba(69,214,255,0.85);
}}
.pump-blade {{
  position: absolute;
  left: 17px; top: 2px;
  width: 6px; height: 18px;
  transform-origin: 3px 18px;
  border-radius: 5px;
  background: linear-gradient(#ffffff, #45d6ff);
}}
.pump-blade:nth-child(2) {{ transform: rotate(120deg); }}
.pump-blade:nth-child(3) {{ transform: rotate(240deg); }}
@keyframes pump-rotation {{
  to {{ transform: rotate(360deg); }}
}}
.feed-supply-pump.running .pump-rotor {{
  animation: pump-rotation 1.1s linear infinite;
}}
.product-export-pump {{
  position: absolute;
  width: 54px;
  height: 54px;
  z-index: 4;
  border: 3px solid rgba(174,247,255,0.72);
  border-radius: 50%;
  background: rgba(8,43,56,0.94);
  box-shadow: 0 0 14px rgba(69,214,255,0.25), inset 0 0 12px rgba(69,214,255,0.15);
}}
.product-export-pump.running {{
  border-color: #25e6a5;
  box-shadow: 0 0 18px rgba(37,230,165,0.72), inset 0 0 14px rgba(69,214,255,0.24);
}}
.product-export-pump.stopped {{ opacity: 0.62; }}
.product-export-pump.running .pump-rotor {{
  animation: pump-rotation 1.1s linear infinite;
}}
.distillate-export-pump {{ left: 87.5%; top: 79px; }}
.bottoms-export-pump {{ left: 78%; top: 278px; }}
.product-export-valve {{
  position: absolute;
  width: 46px;
  height: 34px;
  z-index: 4;
}}
.distillate-export-valve {{ left: 94%; top: 89px; }}
.bottoms-export-valve {{ left: 85%; top: 288px; }}
.product-export-valve .valve-line {{
  position: absolute; left: 0; right: 0; top: 16px; height: 2px;
  background: rgba(232,255,255,0.90);
}}
.product-export-valve .valve-left,
.product-export-valve .valve-right {{
  position: absolute; top: 8px; width: 0; height: 0;
  border-top: 9px solid transparent;
  border-bottom: 9px solid transparent;
  filter: drop-shadow(0 0 4px rgba(69,214,255,0.65));
}}
.product-export-valve .valve-left {{ left: 8px; border-left: 15px solid #f7ffff; }}
.product-export-valve .valve-right {{ right: 8px; border-right: 15px solid #f7ffff; }}
.product-export-valve .valve-stem {{
  position: absolute; left: 22px; top: 0; width: 2px; height: 10px;
  background: rgba(232,255,255,0.90);
}}
.product-export-valve.running .valve-left,
.product-export-valve.running .valve-right {{
  filter: drop-shadow(0 0 8px rgba(37,230,165,0.95));
}}
.product-export-valve.stopped {{ opacity: 0.55; }}
.product-export-valve.stopped .valve-line,
.product-export-valve.stopped .valve-stem {{ background: rgba(255,173,120,0.70); }}
.product-export-valve.stopped .valve-left {{ border-left-color: #ffad78; }}
.product-export-valve.stopped .valve-right {{ border-right-color: #ffad78; }}
.product-export-pump.selected,
.product-export-valve.selected {{
  filter: drop-shadow(0 0 9px rgba(255,230,129,0.85));
}}
.feed-supply-valve {{
  position: absolute;
  left: 12.2%;
  top: 192px;
  width: 46px;
  height: 34px;
  z-index: 4;
}}
.feed-supply-valve .valve-line {{
  position: absolute; left: 0; right: 0; top: 16px; height: 2px;
  background: rgba(232,255,255,0.90);
}}
.feed-supply-valve .valve-left,
.feed-supply-valve .valve-right {{
  position: absolute; top: 8px; width: 0; height: 0;
  border-top: 9px solid transparent;
  border-bottom: 9px solid transparent;
  filter: drop-shadow(0 0 4px rgba(69,214,255,0.65));
}}
.feed-supply-valve .valve-left {{ left: 8px; border-left: 15px solid #f7ffff; }}
.feed-supply-valve .valve-right {{ right: 8px; border-right: 15px solid #f7ffff; }}
.feed-supply-valve .valve-stem {{
  position: absolute; left: 22px; top: 0; width: 2px; height: 10px;
  background: rgba(232,255,255,0.90);
}}
.feed-supply-valve.open .valve-left,
.feed-supply-valve.open .valve-right {{
  filter: drop-shadow(0 0 8px rgba(37,230,165,0.95));
}}
.feed-supply-valve.closed {{ opacity: 0.55; }}
.feed-supply-valve.closed .valve-line,
.feed-supply-valve.closed .valve-stem {{ background: rgba(255,173,120,0.70); }}
.feed-supply-valve.closed .valve-left {{ border-left-color: #ffad78; }}
.feed-supply-valve.closed .valve-right {{ border-right-color: #ffad78; }}
.feed-supply-pump.selected,
.feed-supply-valve.selected {{
  filter: drop-shadow(0 0 9px rgba(255,230,129,0.85));
}}
.tank-fill {{
  position: absolute; left: 12px; right: 12px; bottom: 12px;
  height: {feed_level:.1f}%;
  max-height: 210px;
  border-radius: 28px;
  background: linear-gradient(#45d6ff, #2479c8);
  opacity: 0.38;
}}
.tank-percent {{
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  font: 800 16px Consolas, monospace;
  text-shadow: 0 2px 5px #00141e;
}}
.tank-details {{
  position: absolute;
  color: #b7edf5;
  font: 700 10px/1.45 Consolas, monospace;
}}
.condenser {{
  left: 61%; top: 67px; width: 13%; height: 72px;
  border-radius: 34px;
}}
.condenser::before, .condenser::after {{
  content: ""; position: absolute; top: -2px; width: 18px; height: 72px;
  border-radius: 999px; border: 1px solid rgba(174,247,255,0.72);
  background: rgba(217,251,255,0.07);
}}
.condenser::before {{ left: -8px; }}
.condenser::after {{ right: -8px; }}
.coil {{
  position: absolute; top: 12px; bottom: 12px; width: 2px;
  background: rgba(174,247,255,0.55);
}}
.product-card {{
  position: absolute;
  border: 2px solid rgba(174,247,255,0.62);
  border-radius: 18px;
  background: rgba(8,43,56,0.66);
  overflow: hidden;
}}
.product-card.selected {{
  border-color: #ffe681;
  box-shadow: 0 0 24px rgba(255, 230, 129, 0.34);
}}
.distillate-product {{ left: 75%; top: 48px; width: 11%; height: 118px; }}
.bottom-product {{ left: 62%; top: 260px; width: 14%; height: 90px; }}
.bottom-product-route {{ left: 50%; top: 344px; width: 12%; }}
.product-fill {{
  position: absolute; left: 0; right: 0; bottom: 0;
  border-radius: 0 0 16px 16px;
  background: linear-gradient(rgba(69,214,255,0.20), rgba(36,121,200,0.38));
  z-index: 0;
}}
</style>

<div class="dt101-board">
  <div class="dt101-stage">
  <div class="equipment-name" style="left:1%; top:150px;">Input</div>
  <div class="process-line horizontal right{selected('Feed system')}{'' if feed_supply_active else ' closed'}" style="left:0; top:208px; width:20%;"></div>
  <div class="feed-supply-pump {feed_supply_pump_class}{selected('Feed system')}" title="P-100 {feed_supply_pump_status}">
    <div class="pump-rotor">
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-hub"></span>
    </div>
  </div>
  <div class="feed-supply-valve {feed_supply_valve_class}{selected('Feed system')}" title="V-099 {feed_supply_valve_status}">
    <div class="valve-line"></div>
    <div class="valve-stem"></div>
    <div class="valve-left"></div>
    <div class="valve-right"></div>
  </div>
  <div class="equipment-data" style="left:1%; top:245px;">
    P-100 {feed_supply_pump_status}<br/>
    V-099 {feed_supply_valve_status}<br/>
    Inlet flow {state.feed_inlet_flow:04.1f} L/min
  </div>

  <div class="equipment-name" style="left:22%; top:48px;">Feed tank</div>
  <div class="dt101-equipment feed-tank{selected('Feed system')}">
    <div class="tank-fill"></div>
    <div class="tank-percent">{feed_level:.1f}%</div>
  </div>
  <div class="tank-details" style="left:20%; top:326px;">
    Maximum capacity {FEED_TANK_MAX_CAPACITY_L:.0f} L<br/>
    Current capacity {feed_inventory_liters:.1f} L<br/>
    Light fraction {state.feed_composition_light:.2f}<br/>
    Feed flow {state.feed_flow:04.1f} L/min
  </div>

  <div class="dt101-equipment column{selected('Column')}">
    <div class="column-layers">
{layer_band_html}
    </div>
    <div class="column-heater{selected('Column')}">
      <div class="column-heater-fill"></div>
      <div class="column-heater-label">Column heating duty {heating_duty:.1f}%</div>
    </div>
  </div>
  <div class="equipment-name" style="left:46%; top:18px;">Column</div>
  <div class="equipment-data" style="left:42%; top:360px; color:{pressure_color};">Pressure {state.column_pressure:05.1f} kPa</div>

  <div class="process-line horizontal right{selected('Feed system')}{'' if feed_valve_open else ' closed'}" style="left:35%; top:208px; width:7%;"></div>
  <div class="feed-valve{feed_valve_class}{selected('Feed system')}" title="V-100 {feed_valve_status}">
    <div class="valve-line"></div>
    <div class="valve-stem"></div>
    <div class="valve-left"></div>
    <div class="valve-right"></div>
  </div>
  <div class="equipment-data" style="left:35.5%; top:232px;">V-100 {feed_valve_status}</div>

  <div class="process-line horizontal hot right{selected('Column')}" style="left:50%; top:86px; width:15%;"></div>
  <div class="process-line vertical hot{selected('Column')}" style="left:50%; top:86px; height:18px;"></div>

  <div class="dt101-equipment condenser{selected('Condenser')}">
    <div class="coil" style="left:25%;"></div>
    <div class="coil" style="left:42%;"></div>
    <div class="coil" style="left:59%;"></div>
    <div class="coil" style="left:76%;"></div>
  </div>
  <div class="equipment-name" style="left:63%; top:38px;">Condenser</div>
  <div class="equipment-data" style="left:61%; top:150px;">Valve {condenser_command:.1f}%<br/>Cooling water {state.cooling_water_flow:.1f} L/min</div>
  <div class="process-line horizontal right{selected('Condenser')}" style="left:74%; top:101px; width:1%;"></div>

  <div class="equipment-name" style="left:75%; top:20px;">Distillate product</div>
  <div class="product-card distillate-product{selected('Products')}">
    <div class="product-fill" style="height:{distillate_level:.1f}%;"></div>
    <div class="tank-percent">{distillate_level:.1f}%</div>
  </div>
  <div class="tank-details" style="left:75%; top:176px;">
    Maximum capacity {DISTILLATE_TANK_MAX_CAPACITY_L:.0f} L<br/>
    Current capacity {distillate_inventory_liters:.1f} L<br/>
    Inflow {state.distillate_flow:.2f} L/min<br/>
    <span style="color:{purity_color};">Purity {state.purity_proxy:.1f}%</span>
  </div>
  <div class="process-line horizontal right{selected('Products')}{'' if distillate_export_active else ' closed'}" style="left:86%; top:104px; width:14%;"></div>
  <div class="product-export-pump {distillate_pump_class} distillate-export-pump{selected('Products')}" title="P-201 {distillate_pump_status}">
    <div class="pump-rotor">
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-hub"></span>
    </div>
  </div>
  <div class="product-export-valve {distillate_valve_class} distillate-export-valve{selected('Products')}" title="V-201 {distillate_valve_status}">
    <div class="valve-line"></div>
    <div class="valve-stem"></div>
    <div class="valve-left"></div>
    <div class="valve-right"></div>
  </div>
  <div class="equipment-data" style="left:87%; top:142px;">
    P-201 {distillate_pump_status}<br/>
    V-201 {distillate_valve_status}<br/>
    Outlet flow {state.distillate_outlet_flow:.1f} L/min<br/>
    Auto: &gt;60% ON / &lt;10% OFF
  </div>

  <div class="process-line horizontal right bottom-product-route{selected('Products')}"></div>
  <div class="equipment-name" style="left:62%; top:232px;">Bottom product</div>
  <div class="product-card bottom-product{selected('Products')}">
    <div class="product-fill" style="height:{bottoms_level:.1f}%;"></div>
    <div class="tank-percent">{bottoms_level:.1f}%</div>
  </div>
  <div class="tank-details" style="left:62%; top:358px;">
    Maximum capacity {BOTTOMS_TANK_MAX_CAPACITY_L:.0f} L<br/>
    Current capacity {bottoms_inventory_liters:.1f} L<br/>
    Inflow {state.bottoms_flow:.2f} L/min
  </div>
  <div class="process-line horizontal right{selected('Products')}{'' if bottoms_export_active else ' closed'}" style="left:76%; top:302px; width:24%;"></div>
  <div class="product-export-pump {bottoms_pump_class} bottoms-export-pump{selected('Products')}" title="P-202 {bottoms_pump_status}">
    <div class="pump-rotor">
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-blade"></span>
      <span class="pump-hub"></span>
    </div>
  </div>
  <div class="product-export-valve {bottoms_valve_class} bottoms-export-valve{selected('Products')}" title="V-202 {bottoms_valve_status}">
    <div class="valve-line"></div>
    <div class="valve-stem"></div>
    <div class="valve-left"></div>
    <div class="valve-right"></div>
  </div>
  <div class="equipment-data" style="left:78%; top:342px;">
    P-202 {bottoms_pump_status}<br/>
    V-202 {bottoms_valve_status}<br/>
    Outlet flow {state.bottoms_outlet_flow:.1f} L/min<br/>
    Auto: &gt;60% ON / &lt;10% OFF
  </div>
  </div>
</div>
</div>
"""


def equipment_profile(state: ProcessState, selected: str) -> dict[str, object]:
    feed_supply_pump_running = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_PUMP_RUNNING", False)
    )
    feed_supply_valve_open = bool(
        st.session_state.bus.tags.get("DT101.FB.FEED_SUPPLY_VALVE_OPEN", False)
    )
    feed_valve_command = float(
        st.session_state.bus.tags.get(
            "DT101.CMD.FEED_VALVE",
            0.0,
        )
    )
    feed_valve_feedback = bool(
        st.session_state.bus.tags.get(
            "DT101.FB.FEED_VALVE_OPEN",
            False,
        )
    )
    heating_duty = float(st.session_state.bus.tags.get("DT101.CMD.REBOILER_DUTY", 0.0))
    condenser_command = float(st.session_state.bus.tags.get("DT101.CMD.CONDENSER_VALVE", 0.0))
    distillate_export_running = bool(
        st.session_state.bus.tags.get("DT101.FB.DISTILLATE_EXPORT_PUMP_RUNNING", False)
    )
    bottoms_export_running = bool(
        st.session_state.bus.tags.get("DT101.FB.BOTTOMS_EXPORT_PUMP_RUNNING", False)
    )
    profiles: dict[str, dict[str, object]] = {
        "Feed system": {
            "role": "Supplies the binary mixture into the column and creates the main process load.",
            "watch": {
                "Input pump P-100": "RUNNING" if feed_supply_pump_running else "STOPPED",
                "Input valve V-099": "OPEN" if feed_supply_valve_open else "CLOSED",
                "Feed inlet flow": f"{state.feed_inlet_flow:.2f} L/min",
                "Feed tank level": f"{state.feed_tank_level:.1f} %",
                "Feed valve V-100": "OPEN" if feed_valve_feedback else "CLOSED",
                "Feed valve command": f"{feed_valve_command:.1f} %",
                "Feed flow": f"{state.feed_flow:.2f} L/min",
                "Feed light fraction": f"{state.feed_composition_light:.2f}",
            },
            "control": "Feed pump and feed valve define column throughput.",
            "fault_link": "Feed composition disturbance changes the column temperature profile and purity proxy.",
        },
        "Column": {
            "role": "Performs vapor-liquid contacting so light material enriches overhead and heavy material enriches bottoms.",
            "watch": {
                **column_layer_watch_values(state),
                "Pressure": f"{state.column_pressure:.1f} kPa",
                "Column heating duty": f"{heating_duty:.1f} %",
            },
            "control": "Pressure, reflux, and column heating duty shape the temperature profile.",
            "fault_link": "Top temperature sensor drift is detected by inconsistency with pressure, reflux flow, and purity proxy.",
        },
        "Condenser": {
            "role": "Removes overhead heat, condenses vapor, and helps control column pressure.",
            "watch": {
                "Condenser valve command": f"{condenser_command:.1f} %",
                "Cooling water flow": f"{state.cooling_water_flow:.2f} L/min",
                "Column pressure": f"{state.column_pressure:.1f} kPa",
            },
            "control": "PIC101 adjusts condenser valve opening to stabilize pressure.",
            "fault_link": "Insufficient cooling would raise pressure and can lead to safety interlock action.",
        },
        "Products": {
            "role": "Collects distillate overhead product and bottoms heavy product.",
            "watch": {
                "Distillate inflow": f"{state.distillate_flow:.2f} L/min",
                "Distillate tank level": f"{state.distillate_tank_level:.1f} %",
                "Distillate export P-201 / V-201": "RUNNING / OPEN" if distillate_export_running else "STOPPED / CLOSED",
                "Distillate outlet flow": f"{state.distillate_outlet_flow:.2f} L/min",
                "Bottoms inflow": f"{state.bottoms_flow:.2f} L/min",
                "Bottoms tank level": f"{state.bottoms_tank_level:.1f} %",
                "Bottoms export P-202 / V-202": "RUNNING / OPEN" if bottoms_export_running else "STOPPED / CLOSED",
                "Bottoms outlet flow": f"{state.bottoms_outlet_flow:.2f} L/min",
                "Purity proxy": f"{state.purity_proxy:.1f} %",
            },
            "control": "Automatic export units start above 60% tank level and stop below 10% using PLC hysteresis.",
            "fault_link": "Off-spec product can be inferred from purity proxy and temperature profile deviation.",
        },
    }
    return profiles[selected]


init_session()


@st.fragment(run_every=0.5 if st.session_state.continuous_run else None)
def continuous_simulation_fragment(tick_batch: int) -> None:
    if not st.session_state.continuous_run:
        return
    if st.session_state.continuous_run_skip_next:
        st.session_state.continuous_run_skip_next = False
        return
    for _ in range(int(tick_batch)):
        simulation_tick()
    # A fragment rerun cannot redraw the process overview outside this fragment.
    # Skip the immediate fragment call during the requested full-app rerun.
    st.session_state.continuous_run_skip_next = True
    st.rerun(scope="app")

st.title("DT101 Chemical Distillation Column Digital Twin")
st.caption("Simplified binary distillation column with PLC-style control, local historian, fault injection, and DeepSeek operator assistance.")


def deepseek_api_key() -> str | None:
    try:
        return st.secrets.get("DEEPSEEK_API_KEY")
    except Exception:
        return None

with st.sidebar:
    st.header("Simulation")
    ticks = st.slider("Advance ticks", 1, 30, 5)
    st.slider(
        "Bottom temperature TIC101 (degC)",
        min_value=30.0,
        max_value=150.0,
        step=0.5,
        key="bottom_temp_setpoint",
        help="Operator-adjustable bottom process temperature used as the live bottom PV.",
    )
    st.slider(
        "Top temperature TIC102 (degC)",
        min_value=-20.0,
        max_value=80.0,
        step=0.5,
        key="top_temp_setpoint",
        help="Operator-adjustable top-column process temperature used as the live top PV.",
    )
    feed_supply_status = "ENABLED" if st.session_state.feed_supply_run_request else "DISABLED"
    st.caption(f"Input supply auto P-100 / V-099: {feed_supply_status}")
    st.button(
        "Disable input supply auto P-100 / V-099"
        if st.session_state.feed_supply_run_request
        else "Enable input supply auto P-100 / V-099",
        use_container_width=True,
        on_click=toggle_feed_supply,
    )
    feed_valve_status = "ENABLED" if st.session_state.feed_valve_open else "DISABLED"
    st.caption(f"Feed valve V-100 auto: {feed_valve_status}")
    st.button(
        "Disable feed valve V-100 auto" if st.session_state.feed_valve_open else "Enable feed valve V-100 auto",
        use_container_width=True,
        on_click=toggle_feed_valve,
    )
    if st.button("Run selected ticks", type="primary", use_container_width=True):
        for _ in range(ticks):
            simulation_tick()
    st.button(
        "Stop continuous run" if st.session_state.continuous_run else "Start continuous run",
        use_container_width=True,
        on_click=toggle_continuous_run,
    )
    if st.button("Single PLC scan + process tick", use_container_width=True):
        simulation_tick()
    if st.button("Reset simulation", use_container_width=True):
        reset_simulation()
    st.divider()
    st.header("Fault injection")
    inject_button("top temperature sensor drift", "top_temp_drift")
    inject_button("reflux valve stuck", "reflux_valve_stuck")
    inject_button("feed composition disturbance", "feed_composition_disturbance")
    inject_button("data staleness", "data_stale")
    if st.button("Clear all faults", use_container_width=True):
        st.session_state.faults.clear_all()
    continuous_simulation_fragment(ticks)

state = state_with_manual_temperatures(
    st.session_state.state,
    st.session_state.top_temp_setpoint,
    st.session_state.bottom_temp_setpoint,
)
st.session_state.state = state
mode = st.session_state.plc.mode
alarms = st.session_state.active_alarms

status_cols = st.columns(6)
status_cols[0].metric("Mode", mode)
status_cols[1].metric("Top temp", f"{state.top_temperature:.1f} degC")
status_cols[2].metric("Bottom temp", f"{state.bottom_temperature:.1f} degC")
status_cols[3].metric("Pressure", f"{state.column_pressure:.1f} kPa")
status_cols[4].metric("Purity proxy", f"{state.purity_proxy:.1f}%")
status_cols[5].metric("Active alarms", len(alarms))

if alarms:
    st.error("Active alarms: " + ", ".join(alarms))
else:
    st.success("No active alarms.")

st.markdown(
    """
<style>
.scada-wrap {
  border-radius: 22px;
  overflow: hidden;
  border: 1px solid #334155;
  box-shadow: 0 18px 45px rgba(0,0,0,0.28);
  background: #0b1220;
}
.equipment-card {
  border: 1px solid #334155;
  border-radius: 18px;
  padding: 18px 20px;
  background: linear-gradient(135deg, rgba(15,23,42,0.98), rgba(30,41,59,0.82));
}
.equipment-card h4 {
  margin: 0 0 6px 0;
  color: #facc15;
}
.equipment-card p {
  margin: 8px 0;
}
</style>
""",
    unsafe_allow_html=True,
)

st.subheader("Interactive process overview")
equipment_options = ["Feed system", "Column", "Condenser", "Products"]
if st.session_state.selected_equipment not in equipment_options:
    st.session_state.selected_equipment = "Column"
st.session_state.selected_equipment = st.radio(
    "Focus equipment",
    equipment_options,
    index=equipment_options.index(st.session_state.selected_equipment),
    horizontal=True,
    label_visibility="collapsed",
)
st.markdown(process_overview_svg(state, mode, alarms, st.session_state.selected_equipment), unsafe_allow_html=True)

profile = equipment_profile(state, st.session_state.selected_equipment)
detail_col, values_col = st.columns([1.2, 1.0])
with detail_col:
    st.markdown(
        f"""
<div class="equipment-card">
  <h4>{st.session_state.selected_equipment}</h4>
  <p><strong>Role:</strong> {profile["role"]}</p>
  <p><strong>Control meaning:</strong> {profile["control"]}</p>
  <p><strong>Fault link:</strong> {profile["fault_link"]}</p>
</div>
""",
        unsafe_allow_html=True,
    )
with values_col:
    watch_rows = [{"Variable": key, "Live value": value} for key, value in profile["watch"].items()]
    st.dataframe(pd.DataFrame(watch_rows), use_container_width=True, hide_index=True)

overview_tab, faults_tab, tags_tab = st.tabs(["Live variables", "Faults", "Tag dictionary"])
with overview_tab:
    live_tags = {
        "Feed flow (L/min)": state.feed_flow,
        "Reflux flow (L/min)": state.reflux_flow,
        "Distillate flow (L/min)": state.distillate_flow,
        "Bottoms flow (L/min)": state.bottoms_flow,
        "Reflux drum level (%)": state.reflux_drum_level,
        "Bottom sump level (%)": state.bottom_sump_level,
        "Feed composition light fraction": state.feed_composition_light,
    }
    st.dataframe(pd.DataFrame(live_tags.items(), columns=["Variable", "Value"]), use_container_width=True)
with faults_tab:
    st.write("Active faults:", sorted(st.session_state.faults.active_faults) or "none")
    st.write("Active alarms:", alarms or "none")
with tags_tab:
    tag_rows = [
        {
            "Tag": meta.name,
            "Description": meta.description,
            "Unit": meta.unit,
            "Normal": meta.normal_range,
            "Alarm": meta.alarm_limits,
        }
        for meta in TAG_DICTIONARY.values()
    ]
    st.dataframe(pd.DataFrame(tag_rows), use_container_width=True, hide_index=True)

st.subheader("Historian trends")
trend_tags = [
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
]
df = recent_dataframe(trend_tags, ticks=600)
if df.empty:
    st.info("Run a few ticks to populate historian trends.")
else:
    fig = px.line(
        df.dropna(subset=["numeric_value"]),
        x="tick",
        y="numeric_value",
        color="tag",
        labels={"tick": "Second"},
    )
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Layer temperature historian trends")
layer_temperature_df = recent_dataframe(list(LAYER_TEMPERATURE_TAGS), ticks=600)
if layer_temperature_df.empty:
    st.info("Run a few ticks to populate layer temperature trends.")
else:
    layer_temperature_fig = build_layer_temperature_figure(
        layer_temperature_df.dropna(subset=["numeric_value"])
    )
    st.plotly_chart(layer_temperature_fig, width="stretch")

st.subheader("AI operator assistant")
history = st.session_state.historian.query(trend_tags, ticks=120)
alarm_context = {
    "mode": mode,
    "active_alarms": alarms,
    "active_faults": sorted(st.session_state.faults.active_faults),
    "heartbeat_age_seconds": (datetime.now(timezone.utc) - st.session_state.last_heartbeat).total_seconds(),
}
if st.button("Ask DeepSeek assistant", use_container_width=True):
    st.session_state.last_ai_response = AIAssistant(api_key=deepseek_api_key()).recommend(alarm_context, history)
st.text_area("Recommendation", st.session_state.last_ai_response, height=260)

st.caption("Data staleness fault intentionally freezes broker/historian writes while the underlying process can continue locally.")
