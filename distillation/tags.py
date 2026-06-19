from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TagMeta:
    name: str
    description: str
    unit: str
    normal_range: str
    alarm_limits: str
    source: str
    sample_rate: str = "1 s"


TAG_DICTIONARY: dict[str, TagMeta] = {
    "DT101.PV.TOP_TEMP": TagMeta("DT101.PV.TOP_TEMP", "Top tray temperature", "degC", "76-82", "High > 85", "simulator"),
    "DT101.PV.BOTTOM_TEMP": TagMeta("DT101.PV.BOTTOM_TEMP", "Bottom/reboiler temperature", "degC", "96-104", "High > 110", "simulator"),
    "DT101.PV.COLUMN_PRESSURE": TagMeta("DT101.PV.COLUMN_PRESSURE", "Column pressure", "kPa", "95-115", "High > 125, HH > 140", "simulator"),
    "DT101.PV.REFLUX_DRUM_LEVEL": TagMeta("DT101.PV.REFLUX_DRUM_LEVEL", "Reflux drum level", "%", "40-60", "Low < 20, High > 80", "simulator"),
    "DT101.PV.BOTTOM_LEVEL": TagMeta("DT101.PV.BOTTOM_LEVEL", "Bottom sump level", "%", "40-65", "Low < 20, High > 85", "simulator"),
    "DT101.PV.FEED_FLOW": TagMeta("DT101.PV.FEED_FLOW", "Feed flow", "L/min", "8-12", "High > 15", "simulator"),
    "DT101.HMI.FEED_VALVE_OPEN_REQUEST": TagMeta("DT101.HMI.FEED_VALVE_OPEN_REQUEST", "Operator feed valve V-100 open request", "bool", "True/False", "", "operator/HMI"),
    "DT101.CMD.FEED_VALVE": TagMeta("DT101.CMD.FEED_VALVE", "Feed valve V-100 command", "%", "0-50", "", "PLC"),
    "DT101.FB.FEED_VALVE_OPEN": TagMeta("DT101.FB.FEED_VALVE_OPEN", "Feed valve V-100 open feedback", "bool", "True/False", "", "PLC"),
    "DT101.SP.TOP_TEMP": TagMeta("DT101.SP.TOP_TEMP", "Top temperature setpoint", "degC", "-20-80", "", "operator/HMI"),
    "DT101.SP.BOTTOM_TEMP": TagMeta("DT101.SP.BOTTOM_TEMP", "Bottom temperature setpoint", "degC", "30-150", "", "operator/HMI"),
    "DT101.CMD.REBOILER_DUTY": TagMeta("DT101.CMD.REBOILER_DUTY", "Reboiler duty command", "%", "0-100", "", "PLC"),
    "DT101.CMD.CONDENSER_VALVE": TagMeta("DT101.CMD.CONDENSER_VALVE", "Condenser valve command", "%", "0-100", "", "PLC"),
    "DT101.CMD.REFLUX_VALVE": TagMeta("DT101.CMD.REFLUX_VALVE", "Reflux valve command", "%", "0-100", "", "PLC"),
    "DT101.STATE.MODE": TagMeta("DT101.STATE.MODE", "PLC operating mode", "state", "NORMAL_OPERATION", "", "PLC"),
}

for index in range(1, 8):
    layer_role = "Bottom" if index == 1 else "Top" if index == 7 else f"Middle {index - 1}"
    tag_name = f"DT101.PV.LAYER_{index:02d}_TEMP"
    TAG_DICTIONARY[tag_name] = TagMeta(
        tag_name,
        f"Column layer {index} temperature ({layer_role})",
        "degC",
        "-20-150",
        "",
        "simulator",
    )


def coerce_tag_value(value: Any) -> float | str | bool:
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)
