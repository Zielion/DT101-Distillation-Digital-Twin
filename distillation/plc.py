from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    BOTTOM_LEVEL_SETPOINT,
    BOTTOM_LOW_LOW,
    BOTTOM_TEMP_SETPOINT,
    FEED_TANK_HIGH_HIGH,
    FEED_TANK_LOW_LOW,
    PRESSURE_HIGH_HIGH,
    PRESSURE_SETPOINT,
    PRODUCT_EXPORT_START_LEVEL,
    PRODUCT_EXPORT_STOP_LEVEL,
    REFLUX_DRUM_HIGH_HIGH,
    TANK_CAPACITY_TRIP_LEVEL,
    REFLUX_DRUM_LEVEL_SETPOINT,
    TOP_TEMP_SETPOINT,
)
from .process import clamp


PLC_CONTROL_REVISION = 5

AUTO = "AUTO"
FORCE_ON = "FORCE_ON"
FORCE_OFF = "FORCE_OFF"

DEVICE_OVERRIDE_COMMANDS: dict[str, tuple[str, bool | float, bool | float]] = {
    "DT101.HMI.P100_OVERRIDE": ("feed_supply_pump", True, False),
    "DT101.HMI.V099_OVERRIDE": ("feed_supply_valve", True, False),
    "DT101.HMI.V100_OVERRIDE": ("feed_valve", 100.0, 0.0),
    "DT101.HMI.P201_OVERRIDE": ("distillate_export_pump", True, False),
    "DT101.HMI.V201_OVERRIDE": ("distillate_export_valve", True, False),
    "DT101.HMI.P202_OVERRIDE": ("bottoms_export_pump", True, False),
    "DT101.HMI.V202_OVERRIDE": ("bottoms_export_valve", True, False),
}

CAPACITY_ALARM_FIELDS: dict[str, str] = {
    "DT101.PV.FEED_TANK_LEVEL": "DT101.ALARM.FEED_TANK_OVERFILL",
    "DT101.PV.DISTILLATE_TANK_LEVEL": "DT101.ALARM.DISTILLATE_TANK_OVERFILL",
    "DT101.PV.BOTTOMS_TANK_LEVEL": "DT101.ALARM.BOTTOMS_TANK_OVERFILL",
}
CAPACITY_ALARM_TAGS = frozenset(CAPACITY_ALARM_FIELDS.values())
PRODUCT_CAPACITY_ALARM_TAGS = frozenset(
    {
        "DT101.ALARM.DISTILLATE_TANK_OVERFILL",
        "DT101.ALARM.BOTTOMS_TANK_OVERFILL",
    }
)


def capacity_alarm_tags(snapshot: dict[str, float | str | bool]) -> list[str]:
    return [
        alarm
        for level_tag, alarm in CAPACITY_ALARM_FIELDS.items()
        if float(snapshot.get(level_tag, 0.0)) >= TANK_CAPACITY_TRIP_LEVEL
    ]

FILLING_FEED_TANK = "FILLING_FEED_TANK"
FEEDING_COLUMN = "FEEDING_COLUMN"


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
    feed_cycle_phase: str = FILLING_FEED_TANK
    distillate_export_running: bool = False
    bottoms_export_running: bool = False
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
        distillate_tank_level = float(snapshot.get("DT101.PV.DISTILLATE_TANK_LEVEL", 20.0))
        bottoms_tank_level = float(snapshot.get("DT101.PV.BOTTOMS_TANK_LEVEL", 20.0))
        top_temp = float(snapshot.get("DT101.PV.TOP_TEMP", 79.0))
        bottom_temp = float(snapshot.get("DT101.PV.BOTTOM_TEMP", 100.0))
        feed_valve_open_request = bool(snapshot.get("DT101.HMI.FEED_VALVE_OPEN_REQUEST", True))
        feed_supply_run_request = bool(snapshot.get("DT101.HMI.FEED_SUPPLY_RUN_REQUEST", True))
        feed_valve_open_feedback = bool(snapshot.get("DT101.FB.FEED_VALVE_OPEN", False))
        feed_supply_pump_running_feedback = bool(
            snapshot.get("DT101.FB.FEED_SUPPLY_PUMP_RUNNING", False)
        )
        feed_supply_valve_open_feedback = bool(
            snapshot.get("DT101.FB.FEED_SUPPLY_VALVE_OPEN", False)
        )
        if feed_tank_level >= FEED_TANK_HIGH_HIGH:
            self.feed_cycle_phase = FEEDING_COLUMN
        elif feed_tank_level <= FEED_TANK_LOW_LOW:
            self.feed_cycle_phase = FILLING_FEED_TANK
        feed_supply_run = (
            self.feed_cycle_phase == FILLING_FEED_TANK
            and feed_supply_run_request
            and not feed_valve_open_feedback
        )
        feed_valve_open = (
            self.feed_cycle_phase == FEEDING_COLUMN
            and feed_valve_open_request
            and not feed_supply_pump_running_feedback
            and not feed_supply_valve_open_feedback
        )
        self.distillate_export_running = self._product_export_state(
            self.distillate_export_running,
            distillate_tank_level,
        )
        self.bottoms_export_running = self._product_export_state(
            self.bottoms_export_running,
            bottoms_tank_level,
        )
        self.top_temp_setpoint = float(snapshot.get("DT101.SP.TOP_TEMP", self.top_temp_setpoint))
        self.bottom_temp_setpoint = float(snapshot.get("DT101.SP.BOTTOM_TEMP", self.bottom_temp_setpoint))

        commands: dict[str, float | bool] = {
            "feed_supply_pump": feed_supply_run,
            "feed_supply_valve": feed_supply_run,
            "distillate_export_pump": self.distillate_export_running,
            "distillate_export_valve": self.distillate_export_running,
            "bottoms_export_pump": self.bottoms_export_running,
            "bottoms_export_valve": self.bottoms_export_running,
            "feed_pump": True,
            "feed_valve": 100.0 if feed_valve_open else 0.0,
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

        if feed_tank_level >= FEED_TANK_HIGH_HIGH:
            alarms.append("DT101.ALARM.FEED_TANK_HIGH_HIGH")

        if pressure > PRESSURE_HIGH_HIGH:
            alarms.append("DT101.ALARM.HIGH_HIGH_PRESSURE")
            commands["reboiler_duty"] = 0.0
            commands["condenser_valve"] = 100.0
            commands["feed_pump"] = False
            commands["feed_valve"] = 0.0
            commands["esd_shutdown"] = True
            self.mode = "SHUTDOWN"

        if feed_tank_level <= FEED_TANK_LOW_LOW:
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

        self._apply_manual_overrides(commands, snapshot)

        # A manual V-100 request may bypass sequencing and the low-low cutoff,
        # but the emergency pressure trip remains authoritative.
        if pressure > PRESSURE_HIGH_HIGH:
            commands["feed_pump"] = False
            commands["feed_valve"] = 0.0

        capacity_alarms = capacity_alarm_tags(snapshot)
        alarms.extend(capacity_alarms)
        self._apply_capacity_trip(commands, snapshot, capacity_alarms)

        # With no column feed there is no vapor load in this simplified model.
        # High-high pressure remains authoritative and keeps maximum cooling.
        if float(commands["feed_valve"]) <= 0.0 and pressure <= PRESSURE_HIGH_HIGH:
            commands["condenser_valve"] = 0.0

        return ControlOutput(commands=commands, alarms=alarms, mode=self.mode)

    @staticmethod
    def _apply_capacity_trip(
        commands: dict[str, float | bool],
        snapshot: dict[str, float | str | bool],
        capacity_alarms: list[str],
    ) -> None:
        commands["capacity_trip_active"] = bool(capacity_alarms)
        if not capacity_alarms:
            return

        commands["feed_supply_pump"] = False
        commands["feed_supply_valve"] = False

        product_trip_active = bool(PRODUCT_CAPACITY_ALARM_TAGS.intersection(capacity_alarms))
        v100_forced_on = str(snapshot.get("DT101.HMI.V100_OVERRIDE", AUTO)).upper() == FORCE_ON
        if product_trip_active or not v100_forced_on:
            commands["feed_pump"] = False
            commands["feed_valve"] = 0.0

        for tag, command in (
            ("DT101.HMI.P201_OVERRIDE", "distillate_export_pump"),
            ("DT101.HMI.V201_OVERRIDE", "distillate_export_valve"),
            ("DT101.HMI.P202_OVERRIDE", "bottoms_export_pump"),
            ("DT101.HMI.V202_OVERRIDE", "bottoms_export_valve"),
        ):
            if str(snapshot.get(tag, AUTO)).upper() != FORCE_ON:
                commands[command] = False

    @staticmethod
    def _apply_manual_overrides(
        commands: dict[str, float | bool],
        snapshot: dict[str, float | str | bool],
    ) -> None:
        for tag, (command, on_value, off_value) in DEVICE_OVERRIDE_COMMANDS.items():
            override = str(snapshot.get(tag, AUTO)).upper()
            if override == FORCE_ON:
                commands[command] = on_value
            elif override == FORCE_OFF:
                commands[command] = off_value

        if str(snapshot.get("DT101.HMI.V100_OVERRIDE", AUTO)).upper() == FORCE_ON:
            commands["feed_pump"] = True

    @staticmethod
    def _product_export_state(running: bool, level: float) -> bool:
        if level > PRODUCT_EXPORT_START_LEVEL:
            return True
        if level < PRODUCT_EXPORT_STOP_LEVEL:
            return False
        return running

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
