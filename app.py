from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import reload
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from distillation.ai_assistant import AIAssistant
from distillation.config import BOTTOM_TEMP_SETPOINT, HISTORIAN_DB, TOP_TEMP_SETPOINT
from distillation.faults import FaultManager
from distillation import historian as historian_module
from distillation.plc import PLCController
from distillation.process import ProcessState, derive_column_layer_temperatures, normalize_process_state
from distillation.tags import TAG_DICTIONARY
from distillation import visualization as visualization_module


# Streamlit can preserve an older imported module while hot-reloading app.py.
if not hasattr(historian_module.Historian, "latest_tick"):
    historian_module = reload(historian_module)
if getattr(visualization_module, "LAYER_CHART_X_FIELD", None) != "tick":
    visualization_module = reload(visualization_module)

Historian = historian_module.Historian
TagBus = historian_module.TagBus
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
    st.session_state.tick_count = 0
    initial_tags = st.session_state.state.to_tags()
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
    st.session_state.plc.top_temp_setpoint = float(st.session_state.top_temp_setpoint)
    st.session_state.plc.bottom_temp_setpoint = float(st.session_state.bottom_temp_setpoint)
    plc_output = st.session_state.plc.scan(snapshot, 1.0)
    controls = dict(plc_output.commands)
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
    alarm_active = bool(alarms)
    status_color = "#ff5f63" if alarm_active else "#25e6a5"
    pressure_color = "#ff5f63" if state.column_pressure > 125 else "#bff6ff"
    purity_color = "#ffc26b" if state.purity_proxy < 90 else "#bff6ff"
    active_faults = ", ".join(sorted(st.session_state.faults.active_faults)) or "none"
    alarms_text = ", ".join(alarms) if alarms else "none"
    reflux_feedback = st.session_state.bus.tags.get("DT101.FB.REFLUX_VALVE_POSITION", 50)
    reboiler_duty = st.session_state.bus.tags.get("DT101.CMD.REBOILER_DUTY", 0)
    feed_valve_command = float(
        st.session_state.bus.tags.get(
            "DT101.CMD.FEED_VALVE",
            50.0 if st.session_state.feed_valve_open else 0.0,
        )
    )
    feed_valve_open = bool(
        st.session_state.bus.tags.get(
            "DT101.FB.FEED_VALVE_OPEN",
            st.session_state.feed_valve_open,
        )
    )
    feed_valve_class = " open" if feed_valve_open else " closed"
    feed_valve_status = "OPEN" if feed_valve_open else "CLOSED"
    feed_level = max(0.0, min(100.0, state.feed_tank_level))
    reflux_level = max(0.0, min(100.0, state.reflux_drum_level))

    def selected(name: str) -> str:
        return " selected" if selected_equipment == name else ""

    layers = get_column_layer_temperatures(state)
    layer_band_html = "\n".join(
        f"""    <div class="layer-band" style="top:{38 + index * 48}px;">
      <span>L{7 - index}</span>
      <b>{temperature:04.1f} C</b>
    </div>"""
        for index, temperature in enumerate(reversed(layers))
    )

    return f"""
<div class="scada-wrap">
<style>
.dt101-board {{
  position: relative;
  min-height: 455px;
  overflow: hidden;
  border-radius: 22px;
  border: 1px solid rgba(124, 226, 255, 0.28);
  background:
    linear-gradient(rgba(83, 214, 236, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(83, 214, 236, 0.06) 1px, transparent 1px),
    radial-gradient(circle at 42% 35%, rgba(15, 103, 118, 0.74), rgba(5, 26, 37, 0.94) 58%, #020811 100%);
  background-size: 72px 72px, 72px 72px, 100% 100%;
  color: #d9fbff;
  font-family: "Segoe UI", Arial, sans-serif;
}}
.dt101-board * {{ box-sizing: border-box; }}
.dt101-stage {{
  position: absolute;
  left: 0;
  top: 0;
  width: 1180px;
  height: 620px;
  transform: scale(0.62);
  transform-origin: top left;
}}
.dt101-title {{
  position: absolute; left: 34px; top: 28px;
  font-size: 28px; font-weight: 800; letter-spacing: 1.2px;
}}
.dt101-status {{
  position: absolute; right: 34px; top: 38px;
  display: flex; gap: 12px; align-items: center;
  color: #c9f8ff; font-size: 15px; font-weight: 600;
}}
.dt101-dot {{
  width: 18px; height: 18px; border-radius: 999px;
  background: {status_color}; box-shadow: 0 0 18px {status_color};
}}
.dt101-chip {{
  position: absolute;
  padding: 7px 10px;
  border: 1px solid rgba(95, 224, 242, 0.36);
  border-radius: 10px;
  background: rgba(2, 18, 29, 0.76);
  color: #dffcff;
  font: 700 12px Consolas, monospace;
  box-shadow: 0 0 18px rgba(69, 214, 255, 0.12);
}}
.dt101-chip span {{
  display: block;
  color: #82c9d6;
  font-size: 10px;
  font-weight: 500;
  margin-bottom: 3px;
}}
.dt101-equipment {{
  position: absolute;
  border: 2px solid rgba(144, 237, 255, 0.62);
  background: rgba(8, 43, 56, 0.72);
  box-shadow: inset 0 0 22px rgba(126, 236, 255, 0.09), 0 0 18px rgba(69, 214, 255, 0.10);
}}
.dt101-equipment.selected {{
  border-color: #ffe681;
  box-shadow: 0 0 28px rgba(255, 230, 129, 0.38), inset 0 0 24px rgba(255, 230, 129, 0.08);
}}
.dt101-label {{
  position: absolute;
  color: #dffcff;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}}
.dt101-small {{
  position: absolute;
  color: #9edbe6;
  font: 700 11px Consolas, monospace;
}}
.pipe {{
  position: absolute;
  border-radius: 999px;
  z-index: 2;
}}
.pipe.liquid {{
  background: linear-gradient(90deg, rgba(69,214,255,0.28), #45d6ff);
  box-shadow: 0 0 14px rgba(69,214,255,0.55);
}}
.pipe.vapor {{
  background: linear-gradient(90deg, rgba(255,173,120,0.24), #ffad78);
  box-shadow: 0 0 14px rgba(255,173,120,0.52);
}}
.pipe.closed {{
  opacity: 0.26;
  filter: grayscale(0.7);
  box-shadow: none;
}}
.pipe.h {{ height: 7px; }}
.pipe.v {{ width: 7px; }}
.pipe.h.right::after {{
  content: "";
  position: absolute; right: -10px; top: -5px;
  width: 0; height: 0;
  border-top: 8px solid transparent;
  border-bottom: 8px solid transparent;
  border-left: 12px solid currentColor;
}}
.pipe.v.down::after {{
  content: "";
  position: absolute; left: -5px; bottom: -10px;
  width: 0; height: 0;
  border-left: 8px solid transparent;
  border-right: 8px solid transparent;
  border-top: 12px solid currentColor;
}}
.liquid {{ color: #45d6ff; }}
.vapor {{ color: #ffad78; }}
.column {{
  left: 180px; top: 112px; width: 138px; height: 420px;
  border-radius: 62px;
  overflow: hidden;
  background: linear-gradient(135deg, rgba(217,251,255,0.20), rgba(69,214,255,0.08), rgba(3,24,36,0.62));
}}
.column::before {{
  content: "";
  position: absolute; inset: 22px 18px;
  border-radius: 44px;
  background: rgba(217,251,255,0.045);
}}
.tray {{
  position: absolute; left: 22px; right: 22px; height: 2px;
  background: rgba(217,251,255,0.58);
}}
.tray::after {{
  content: "";
  position: absolute; left: 12px; right: 12px; top: -10px; height: 20px;
  border-radius: 999px;
  border: 1px solid rgba(191,246,255,0.42);
  background: rgba(191,246,255,0.08);
}}
.layer-band {{
  position: absolute;
  left: 24px;
  right: 24px;
  height: 38px;
  z-index: 1;
  border-top: 1px solid rgba(191,246,255,0.58);
  border-bottom: 1px solid rgba(191,246,255,0.22);
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(69,214,255,0.10), rgba(255,173,120,0.08));
  color: #dffcff;
  font: 700 10px Consolas, monospace;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px;
}}
.layer-band span {{
  color: #86d7e5;
}}
.layer-band b {{
  color: #ffffff;
  font-size: 10px;
}}
.feed-tank {{
  left: 58px; top: 328px; width: 96px; height: 122px;
  border-radius: 18px;
}}
.feed-valve {{
  position: absolute;
  left: 150px;
  top: 375px;
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
.tank-fill {{
  position: absolute; left: 14px; right: 14px; bottom: 12px;
  height: {feed_level:.1f}%;
  max-height: 92px;
  border-radius: 12px;
  background: linear-gradient(#45d6ff, #2479c8);
  opacity: 0.38;
}}
.condenser {{
  left: 620px; top: 100px; width: 168px; height: 58px;
  border-radius: 34px;
}}
.condenser::before, .condenser::after {{
  content: ""; position: absolute; top: -2px; width: 20px; height: 58px;
  border-radius: 999px; border: 1px solid rgba(174,247,255,0.72);
  background: rgba(217,251,255,0.07);
}}
.condenser::before {{ left: -8px; }}
.condenser::after {{ right: -8px; }}
.coil {{
  position: absolute; top: 12px; bottom: 12px; width: 2px;
  background: rgba(174,247,255,0.55);
}}
.storage {{
  left: 636px; top: 258px; width: 112px; height: 92px;
  border-radius: 30px;
}}
.storage-fill {{
  position: absolute; left: 16px; right: 16px; bottom: 12px;
  height: {reflux_level:.1f}%;
  max-height: 64px;
  border-radius: 18px;
  background: linear-gradient(#45d6ff, #2479c8);
  opacity: 0.32;
}}
.preheater {{
  left: 406px; top: 468px; width: 140px; height: 54px;
  border-radius: 22px;
}}
.preheater::after {{
  content: "";
  position: absolute; left: 20px; right: 20px; top: 25px;
  height: 2px; background: rgba(174,247,255,0.76);
  box-shadow: 20px -12px 0 -1px rgba(255,173,120,0.55), 52px 12px 0 -1px rgba(255,173,120,0.55);
  transform: skewX(-22deg);
}}
.pump {{
  left: 576px; top: 494px; width: 134px; height: 72px;
  border: none; background: transparent; box-shadow: none;
}}
.pump .wheel {{
  position: absolute; top: 8px; width: 58px; height: 58px;
  border-radius: 50%;
  border: 2px solid rgba(174,247,255,0.75);
  background: rgba(8,43,56,0.72);
}}
.pump .wheel.left {{ left: 0; }}
.pump .wheel.right {{ right: 0; }}
.pump .motor {{
  position: absolute; left: 52px; top: 20px; width: 48px; height: 34px;
  border-radius: 8px; border: 2px solid rgba(174,247,255,0.62);
  background: rgba(8,43,56,0.78);
}}
.reboiler {{
  left: 838px; top: 458px; width: 198px; height: 82px;
  border-color: rgba(255,173,120,0.70);
  border-radius: 42px;
}}
.flame {{
  position: absolute; left: 36px; right: 36px; top: 30px; height: 22px;
  border-radius: 999px;
  background: linear-gradient(90deg, #45d6ff, #ff7e5f 45%, #ffe29a);
  box-shadow: 0 0 18px rgba(255,126,95,0.52);
}}
.product-box {{
  position: absolute;
  width: 92px; height: 62px;
  border: 2px solid rgba(174,247,255,0.62);
  border-radius: 14px;
  background: rgba(8,43,56,0.66);
}}
.section-marker {{
  position: absolute; left: 30px; top: 120px; width: 124px; height: 420px;
  border-left: 2px solid rgba(191,246,255,0.58);
}}
.section-marker::before, .section-marker::after {{
  content: ""; position: absolute; left: 0; width: 18px;
  border-top: 2px solid rgba(191,246,255,0.58);
}}
.section-marker::before {{ top: 0; }}
.section-marker::after {{ bottom: 0; }}
.dt101-footer {{
  position: absolute; left: 34px; right: 34px; bottom: 22px;
  color: #b7edf5;
  font: 700 12px Consolas, monospace;
  display: flex; justify-content: space-between; gap: 16px;
  border-top: 1px solid rgba(124,226,255,0.16);
  padding-top: 14px;
}}
</style>

<div class="dt101-board">
  <div class="dt101-stage">
  <div class="dt101-title">DT101 DISTILLATION DIGITAL TWIN</div>
  <div class="dt101-status"><div class="dt101-dot"></div><div>{'Alarm active' if alarm_active else 'Connected'}</div></div>

  <div class="section-marker"></div>
  <div class="dt101-label" style="left:54px; top:102px;">Distillation<br/>Column</div>
  <div class="dt101-label" style="left:54px; top:238px;">Rectifying<br/>section</div>
  <div class="dt101-label" style="left:72px; top:354px;">Feed tray</div>
  <div class="dt101-label" style="left:62px; top:470px;">Stripping<br/>section</div>

  <div class="dt101-equipment feed-tank{selected('Feed system')}">
    <div class="tank-fill"></div>
  </div>
  <div class="dt101-label" style="left:70px; top:462px;">Feed tank</div>
  <div class="dt101-small" style="left:74px; top:486px;">LT-100 {state.feed_tank_level:04.1f}%</div>

  <div class="dt101-equipment column{selected('Column')}">
{layer_band_html}
  </div>

  <div class="pipe h liquid right{'' if feed_valve_open else ' closed'}" style="left:154px; top:388px; width:28px;"></div>
  <div class="feed-valve{feed_valve_class}" title="Feed valve V-100 {feed_valve_status}">
    <div class="valve-line"></div>
    <div class="valve-stem"></div>
    <div class="valve-left"></div>
    <div class="valve-right"></div>
  </div>
  <div class="dt101-label" style="left:66px; top:368px;">Feed</div>
  <div class="dt101-small" style="left:56px; top:410px;">FT-101 {state.feed_flow:04.1f} L/min</div>
  <div class="dt101-small" style="left:148px; top:414px;">V-100 {feed_valve_status} {feed_valve_command:04.1f}%</div>

  <div class="pipe v vapor down" style="left:246px; top:86px; height:30px;"></div>
  <div class="pipe h vapor right" style="left:249px; top:86px; width:370px;"></div>
  <div class="dt101-label" style="left:418px; top:58px; color:#ffd1b7;">Vapor</div>

  <div class="dt101-equipment condenser{selected('Condenser')}">
    <div class="coil" style="left:38px;"></div>
    <div class="coil" style="left:70px;"></div>
    <div class="coil" style="left:102px;"></div>
    <div class="coil" style="left:134px;"></div>
  </div>
  <div class="dt101-label" style="left:654px; top:64px;">Total condenser</div>
  <div class="pipe h liquid right" style="left:788px; top:128px; width:275px;"></div>
  <div class="dt101-label" style="left:972px; top:100px;">Top product</div>
  <div class="product-box" style="left:1050px; top:108px;"></div>

  <div class="pipe v liquid down" style="left:698px; top:158px; height:92px;"></div>
  <div class="pipe h liquid right" style="left:630px; top:250px; width:68px; transform:rotate(180deg);"></div>
  <div class="dt101-equipment storage{selected('Reflux drum')}">
    <div class="storage-fill"></div>
  </div>
  <div class="dt101-label" style="left:650px; top:360px;">Storage tank</div>
  <div class="dt101-small" style="left:654px; top:386px;">LT-101 {state.reflux_drum_level:04.1f}%</div>

  <div class="pipe h liquid" style="left:544px; top:304px; width:92px;"></div>
  <div class="pipe v liquid down" style="left:544px; top:304px; height:198px;"></div>
  <div class="pipe h liquid right" style="left:360px; top:502px; width:184px; transform:rotate(180deg);"></div>
  <div class="dt101-equipment preheater{selected('Feed system')}"></div>
  <div class="dt101-label" style="left:414px; top:438px;">Feed preheater</div>

  <div class="dt101-equipment pump{selected('Reflux valve')}">
    <div class="wheel left"></div>
    <div class="motor"></div>
    <div class="wheel right"></div>
  </div>
  <div class="dt101-label" style="left:572px; top:468px;">Reflux pump</div>
  <div class="dt101-small" style="left:574px; top:574px;">V-101 FB {reflux_feedback:04.1f}%</div>

  <div class="pipe h liquid right" style="left:258px; top:556px; width:760px;"></div>
  <div class="pipe v liquid down" style="left:258px; top:532px; height:24px;"></div>
  <div class="dt101-label" style="left:386px; top:578px;">Liquid</div>

  <div class="pipe h liquid right" style="left:318px; top:432px; width:110px;"></div>
  <div class="pipe v liquid down" style="left:428px; top:432px; height:70px;"></div>
  <div class="pipe h vapor right" style="left:318px; top:314px; width:190px;"></div>
  <div class="pipe v vapor down" style="left:508px; top:314px; height:166px;"></div>
  <div class="pipe h vapor right" style="left:508px; top:480px; width:330px;"></div>
  <div class="dt101-label" style="left:410px; top:288px; color:#ffd1b7;">Vapor</div>

  <div class="dt101-equipment reboiler{selected('Reboiler')}">
    <div class="flame"></div>
  </div>
  <div class="dt101-label" style="left:920px; top:430px;">Reboiler</div>
  <div class="pipe h vapor right" style="left:1034px; top:486px; width:76px;"></div>
  <div class="dt101-label" style="left:1076px; top:462px; color:#ffd1b7;">Steam</div>
  <div class="pipe h liquid right" style="left:1034px; top:526px; width:54px; transform:rotate(180deg);"></div>
  <div class="dt101-label" style="left:1052px; top:548px;">Condensate</div>
  <div class="dt101-label" style="left:1014px; top:582px;">Bottom<br/>product</div>

  <div class="dt101-chip" style="left:294px; top:92px;"><span>TT-101</span>{state.top_temperature:04.1f} C</div>
  <div class="dt101-chip" style="left:360px; top:334px;"><span>PT-101</span><span style="color:{pressure_color}; font-size:13px; font-weight:800;">{state.column_pressure:05.1f} kPa</span></div>
  <div class="dt101-chip" style="left:820px; top:176px;"><span>QI-101 quality</span><span style="color:{purity_color}; font-size:13px; font-weight:800;">{state.purity_proxy:05.1f} %</span></div>
  <div class="dt101-chip" style="left:760px; top:382px;"><span>FR-101 reflux</span>{state.reflux_flow:05.2f} L/min</div>
  <div class="dt101-chip" style="left:850px; top:552px;"><span>REB-101 duty</span>{reboiler_duty:04.1f} %</div>
  </div>

  <div class="dt101-footer">
    <div>Mode: {mode}</div>
    <div>Focus: {selected_equipment}</div>
    <div>Faults: {active_faults}</div>
    <div>Alarms: {alarms_text}</div>
  </div>
</div>
</div>
"""


def equipment_profile(state: ProcessState, selected: str) -> dict[str, object]:
    feed_valve_command = float(
        st.session_state.bus.tags.get(
            "DT101.CMD.FEED_VALVE",
            50.0 if st.session_state.feed_valve_open else 0.0,
        )
    )
    feed_valve_feedback = bool(
        st.session_state.bus.tags.get(
            "DT101.FB.FEED_VALVE_OPEN",
            st.session_state.feed_valve_open,
        )
    )
    profiles: dict[str, dict[str, object]] = {
        "Feed system": {
            "role": "Supplies the binary mixture into the column and creates the main process load.",
            "watch": {
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
            "watch": column_layer_watch_values(state),
            "control": "Pressure, reflux, and reboiler duty shape the temperature profile.",
            "fault_link": "Top temperature sensor drift is detected by inconsistency with pressure, reflux flow, and purity proxy.",
        },
        "Condenser": {
            "role": "Removes overhead heat, condenses vapor, and helps control column pressure.",
            "watch": {
                "Cooling water flow": f"{state.cooling_water_flow:.2f} L/min",
                "Column pressure": f"{state.column_pressure:.1f} kPa",
            },
            "control": "PIC101 adjusts condenser valve opening to stabilize pressure.",
            "fault_link": "Insufficient cooling would raise pressure and can lead to safety interlock action.",
        },
        "Reflux drum": {
            "role": "Buffers condensed liquid before splitting it into distillate product and reflux return.",
            "watch": {
                "Reflux drum level": f"{state.reflux_drum_level:.1f} %",
                "Distillate flow": f"{state.distillate_flow:.2f} L/min",
                "Reflux flow": f"{state.reflux_flow:.2f} L/min",
            },
            "control": "LIC101 adjusts distillate valve to keep reflux drum level near setpoint.",
            "fault_link": "A level excursion can indicate imbalance between condensation, reflux, and product withdrawal.",
        },
        "Reflux valve": {
            "role": "Returns liquid to the column top to improve separation quality.",
            "watch": {
                "Reflux flow": f"{state.reflux_flow:.2f} L/min",
                "Purity proxy": f"{state.purity_proxy:.1f} %",
            },
            "control": "TIC102 adjusts reflux valve based on top temperature / purity proxy.",
            "fault_link": "Reflux valve stuck is detected by command-feedback mismatch and low reflux flow.",
        },
        "Reboiler": {
            "role": "Adds heat at the column bottom to generate boil-up vapor.",
            "watch": {
                "Bottom temperature": f"{state.bottom_temperature:.1f} degC",
                "Bottom sump level": f"{state.bottom_sump_level:.1f} %",
                "Pressure": f"{state.column_pressure:.1f} kPa",
            },
            "control": "TIC101 adjusts reboiler duty; safety interlock cuts duty on high-high pressure or low-low bottom level.",
            "fault_link": "Excess duty can increase pressure; dry heating must be prevented by PLC interlock.",
        },
        "Products": {
            "role": "Collects distillate overhead product and bottoms heavy product.",
            "watch": {
                "Distillate tank": f"{state.distillate_tank_level:.1f} %",
                "Bottoms tank": f"{state.bottoms_tank_level:.1f} %",
                "Purity proxy": f"{state.purity_proxy:.1f} %",
            },
            "control": "Distillate and bottoms valves balance inventory while product quality is monitored.",
            "fault_link": "Off-spec product can be inferred from purity proxy and temperature profile deviation.",
        },
    }
    return profiles[selected]


init_session()

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
    feed_valve_status = "OPEN" if st.session_state.feed_valve_open else "CLOSED"
    st.caption(f"Feed valve V-100: {feed_valve_status}")
    st.button(
        "Close feed valve V-100" if st.session_state.feed_valve_open else "Open feed valve V-100",
        use_container_width=True,
        on_click=toggle_feed_valve,
    )
    if st.button("Run selected ticks", type="primary", use_container_width=True):
        for _ in range(ticks):
            simulation_tick()
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
equipment_options = ["Feed system", "Column", "Condenser", "Reflux drum", "Reflux valve", "Reboiler", "Products"]
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
