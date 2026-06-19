from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    BOTTOM_LEVEL_SETPOINT,
    BOTTOM_LOW_LOW,
    BOTTOM_TEMP_SETPOINT,
    FEED_TANK_LOW_LOW,
    PRESSURE_HIGH_HIGH,
    PRESSURE_SETPOINT,
    REFLUX_DRUM_HIGH_HIGH,
    REFLUX_DRUM_LEVEL_SETPOINT,
    TOP_TEMP_SETPOINT,
)
from .process import clamp


@dataclass
class PID:
    kp: float
    ki: float
    kd: float = 0.0
    bias: float = 50.0
    reverse: bool = False
    integral: float = 0.0
    previous_error: float = 0.0

    def compute(self, setpoint: float, measured: float, dt: float) -> float:
        error = setpoint - measured
        if self.reverse:
            error = -error
        self.integral = clamp(self.integral + error * dt, -100.0, 100.0)
        derivative = (error - self.previous_error) / dt if dt else 0.0
        self.previous_error = error
        return clamp(self.bias + self.kp * error + self.ki * self.integral + self.kd * derivative, 0.0, 100.0)


@dataclass
class ControlOutput:
    commands: dict[str, float | bool]
    alarms: list[str]
    mode: str


@dataclass
class PLCController:
    mode: str = "IDLE"
    top_temp_setpoint: float = TOP_TEMP_SETPOINT
    bottom_temp_setpoint: float = BOTTOM_TEMP_SETPOINT
    stable_seconds: float = 0.0
    pids: dict[str, PID] = field(
        default_factory=lambda: {
            "PIC101": PID(kp=2.0, ki=0.05, bias=55.0, reverse=False),
            "TIC101": PID(kp=1.2, ki=0.03, bias=50.0, reverse=False),
            "TIC102": PID(kp=2.1, ki=0.04, bias=50.0, reverse=True),
            "LIC101": PID(kp=1.0, ki=0.02, bias=50.0, reverse=False),
            "LIC102": PID(kp=1.0, ki=0.02, bias=50.0, reverse=False),
        }
    )

    def scan(self, snapshot: dict[str, float | str | bool], dt: float) -> ControlOutput:
        alarms: list[str] = []
        was_idle = self.mode == "IDLE"
        pressure = float(snapshot.get("DT101.PV.COLUMN_PRESSURE", 105.0))
        feed_tank_level = float(snapshot.get("DT101.PV.FEED_TANK_LEVEL", 80.0))
        bottom_level = float(snapshot.get("DT101.PV.BOTTOM_LEVEL", 55.0))
        reflux_drum_level = float(snapshot.get("DT101.PV.REFLUX_DRUM_LEVEL", 50.0))
        top_temp = float(snapshot.get("DT101.PV.TOP_TEMP", 79.0))
        bottom_temp = float(snapshot.get("DT101.PV.BOTTOM_TEMP", 100.0))
        feed_valve_open_request = bool(snapshot.get("DT101.HMI.FEED_VALVE_OPEN_REQUEST", True))
        self.top_temp_setpoint = float(snapshot.get("DT101.SP.TOP_TEMP", self.top_temp_setpoint))
        self.bottom_temp_setpoint = float(snapshot.get("DT101.SP.BOTTOM_TEMP", self.bottom_temp_setpoint))

        commands: dict[str, float | bool] = {
            "feed_pump": True,
            "feed_valve": 50.0 if feed_valve_open_request else 0.0,
            "top_temp_setpoint": self.top_temp_setpoint,
            "bottom_temp_setpoint": self.bottom_temp_setpoint,
            "reboiler_duty": self.pids["TIC101"].compute(self.bottom_temp_setpoint, bottom_temp, dt),
            "condenser_valve": self.pids["PIC101"].compute(PRESSURE_SETPOINT, pressure, dt),
            "reflux_valve": self.pids["TIC102"].compute(self.top_temp_setpoint, top_temp, dt),
            "distillate_valve": self.pids["LIC101"].compute(REFLUX_DRUM_LEVEL_SETPOINT, reflux_drum_level, dt),
            "bottoms_valve": self.pids["LIC102"].compute(BOTTOM_LEVEL_SETPOINT, bottom_level, dt),
            "esd_shutdown": False,
        }

        self._advance_mode(snapshot, dt)

        if pressure > PRESSURE_HIGH_HIGH:
            alarms.append("DT101.ALARM.HIGH_HIGH_PRESSURE")
            commands["reboiler_duty"] = 0.0
            commands["condenser_valve"] = 100.0
            commands["feed_pump"] = False
            commands["feed_valve"] = 0.0
            commands["esd_shutdown"] = True
            self.mode = "SHUTDOWN"

        if feed_tank_level < FEED_TANK_LOW_LOW:
            alarms.append("DT101.ALARM.FEED_TANK_LOW_LOW")
            commands["feed_pump"] = False
            commands["feed_valve"] = 0.0

        if bottom_level < BOTTOM_LOW_LOW:
            alarms.append("DT101.ALARM.BOTTOM_LEVEL_LOW_LOW")
            commands["reboiler_duty"] = 0.0

        if reflux_drum_level > REFLUX_DRUM_HIGH_HIGH:
            alarms.append("DT101.ALARM.REFLUX_DRUM_HIGH_HIGH")
            commands["distillate_valve"] = 100.0

        if was_idle or self.mode == "IDLE":
            commands.update({"feed_pump": False, "feed_valve": 0.0, "reboiler_duty": 0.0})

        return ControlOutput(commands=commands, alarms=alarms, mode=self.mode)

    def _advance_mode(self, snapshot: dict[str, float | str | bool], dt: float) -> None:
        if self.mode == "IDLE":
            self.mode = "FILLING"
        elif self.mode == "FILLING" and float(snapshot.get("DT101.PV.BOTTOM_LEVEL", 0.0)) > 35.0:
            self.mode = "STARTUP_HEATING"
        elif self.mode == "STARTUP_HEATING" and float(snapshot.get("DT101.PV.BOTTOM_TEMP", 0.0)) > 92.0:
            self.mode = "STABILIZING"
        elif self.mode == "STABILIZING":
            top_ok = abs(float(snapshot.get("DT101.PV.TOP_TEMP", 0.0)) - self.top_temp_setpoint) < 2.5
            bottom_ok = abs(float(snapshot.get("DT101.PV.BOTTOM_TEMP", 0.0)) - self.bottom_temp_setpoint) < 3.5
            self.stable_seconds = self.stable_seconds + dt if top_ok and bottom_ok else 0.0
            if self.stable_seconds >= 5.0:
                self.mode = "NORMAL_OPERATION"
