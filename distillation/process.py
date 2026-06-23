from __future__ import annotations

from dataclasses import dataclass, fields, replace
from math import exp

from .config import (
    BOTTOMS_TANK_MAX_CAPACITY_L,
    BOTTOM_TEMP_SETPOINT,
    DISTILLATE_TANK_MAX_CAPACITY_L,
    FEED_TANK_MAX_CAPACITY_L,
    FEED_SUPPLY_FLOW_LPM,
    PRODUCT_EXPORT_FLOW_LPM,
    TOP_TEMP_SETPOINT,
)


MIDDLE_LAYER_TIME_CONSTANT_SECONDS = 20.0
PROCESS_MODEL_REVISION = 3


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def interpolate_column_layer_temperatures(bottom_temperature: float, top_temperature: float) -> tuple[float, ...]:
    step = (float(top_temperature) - float(bottom_temperature)) / 6.0
    return tuple(float(bottom_temperature) + step * index for index in range(7))


def derive_column_layer_temperatures(state: object) -> tuple[float, ...]:
    layer_method = getattr(state, "column_layer_temperatures", None)
    if callable(layer_method):
        return tuple(float(value) for value in layer_method())
    return interpolate_column_layer_temperatures(
        float(getattr(state, "bottom_temperature")),
        float(getattr(state, "top_temperature")),
    )


def normalize_process_state(state: object) -> "ProcessState":
    middle_layers = getattr(state, "middle_layer_temperatures", None)
    if isinstance(state, ProcessState) and isinstance(middle_layers, tuple) and len(middle_layers) == 5:
        return state
    values = {
        field.name: getattr(state, field.name)
        for field in fields(ProcessState)
        if field.name != "middle_layer_temperatures" and hasattr(state, field.name)
    }
    return ProcessState(**values)


@dataclass(frozen=True)
class ProcessState:
    feed_tank_level: float = 10.0
    feed_inlet_flow: float = 0.0
    feed_composition_light: float = 0.50
    feed_flow: float = 10.0
    feed_temperature: float = 30.0
    top_temperature: float = 79.0
    mid_temperature: float = 88.0
    bottom_temperature: float = 100.0
    column_pressure: float = 105.0
    reflux_drum_level: float = 50.0
    bottom_sump_level: float = 55.0
    reflux_flow: float = 5.5
    distillate_flow: float = 4.5
    bottoms_flow: float = 4.5
    distillate_outlet_flow: float = 0.0
    bottoms_outlet_flow: float = 0.0
    cooling_water_flow: float = 20.0
    purity_proxy: float = 96.0
    separation_efficiency: float = 0.92
    distillate_tank_level: float = 20.0
    bottoms_tank_level: float = 20.0
    middle_layer_temperatures: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        middle_layers = self.middle_layer_temperatures
        if not middle_layers:
            middle_layers = interpolate_column_layer_temperatures(
                self.bottom_temperature,
                self.top_temperature,
            )[1:-1]
            object.__setattr__(self, "middle_layer_temperatures", middle_layers)
        elif len(middle_layers) != 5:
            raise ValueError("middle_layer_temperatures must contain Layers 2 through 6")
        object.__setattr__(self, "mid_temperature", float(middle_layers[2]))

    def step(self, commands: dict[str, float | bool], faults: dict[str, bool | float], dt: float) -> "ProcessState":
        feed_supply_pump = bool(commands.get("feed_supply_pump", False))
        feed_supply_valve = bool(commands.get("feed_supply_valve", False))
        feed_pump = bool(commands.get("feed_pump", True))
        feed_valve = float(commands.get("feed_valve", 50.0))
        reflux_valve_cmd = float(commands.get("reflux_valve", 50.0))
        reflux_valve = float(faults.get("reflux_valve_stuck_position", reflux_valve_cmd))
        reboiler_duty = float(commands.get("reboiler_duty", 50.0))
        condenser_valve = float(commands.get("condenser_valve", 55.0))
        distillate_valve = float(commands.get("distillate_valve", 50.0))
        bottoms_valve = float(commands.get("bottoms_valve", 50.0))
        distillate_export_pump = bool(commands.get("distillate_export_pump", False))
        distillate_export_valve = bool(commands.get("distillate_export_valve", False))
        bottoms_export_pump = bool(commands.get("bottoms_export_pump", False))
        bottoms_export_valve = bool(commands.get("bottoms_export_valve", False))
        manual_top_temperature = commands.get("top_temperature")
        manual_bottom_temperature = commands.get("bottom_temperature")

        feed_comp = float(faults.get("feed_composition_light", self.feed_composition_light))
        feed_inlet_flow = FEED_SUPPLY_FLOW_LPM if feed_supply_pump and feed_supply_valve else 0.0
        requested_feed_flow = (feed_valve / 50.0) * 10.0 if feed_pump else 0.0
        feed_inventory_liters = self.feed_tank_level / 100.0 * FEED_TANK_MAX_CAPACITY_L
        available_feed_flow = (
            feed_inlet_flow + feed_inventory_liters / dt
            if dt > 0.0
            else requested_feed_flow
        )
        feed_flow = min(requested_feed_flow, available_feed_flow)
        reflux_flow = (reflux_valve / 50.0) * 5.5
        distillate_weight = max(0.0, distillate_valve)
        bottoms_weight = max(0.0, bottoms_valve)
        product_weight = distillate_weight + bottoms_weight
        recovered_feed_flow = feed_flow * 0.95 if product_weight > 0.0 else 0.0
        distillate_flow = recovered_feed_flow * distillate_weight / product_weight if product_weight > 0.0 else 0.0
        bottoms_flow = recovered_feed_flow * bottoms_weight / product_weight if product_weight > 0.0 else 0.0
        distillate_outlet_flow = (
            PRODUCT_EXPORT_FLOW_LPM
            if distillate_export_pump and distillate_export_valve and self.distillate_tank_level > 0.0
            else 0.0
        )
        bottoms_outlet_flow = (
            PRODUCT_EXPORT_FLOW_LPM
            if bottoms_export_pump and bottoms_export_valve and self.bottoms_tank_level > 0.0
            else 0.0
        )
        condensate_flow = clamp(3.0 + condenser_valve * 0.055 + reboiler_duty * 0.025, 1.0, 12.0)
        liquid_downflow = clamp(feed_flow * 0.45 + reflux_flow * 0.30, 0.0, 10.0)

        feed_tank_level = clamp(
            self.feed_tank_level
            + (feed_inlet_flow - feed_flow) * dt / FEED_TANK_MAX_CAPACITY_L * 100.0,
            0.0,
            100.0,
        )
        reflux_drum_level = clamp(
            self.reflux_drum_level + (condensate_flow - reflux_flow - distillate_flow) * dt * 0.22,
            0.0,
            100.0,
        )
        bottom_sump_level = clamp(
            self.bottom_sump_level + (liquid_downflow - bottoms_flow) * dt * 0.22,
            0.0,
            100.0,
        )
        distillate_tank_level = clamp(
            self.distillate_tank_level
            + (distillate_flow - distillate_outlet_flow)
            * dt
            / DISTILLATE_TANK_MAX_CAPACITY_L
            * 100.0,
            0.0,
            100.0,
        )
        bottoms_tank_level = clamp(
            self.bottoms_tank_level
            + (bottoms_flow - bottoms_outlet_flow)
            * dt
            / BOTTOMS_TANK_MAX_CAPACITY_L
            * 100.0,
            0.0,
            100.0,
        )

        comp_deviation = (feed_comp - 0.50) * 100.0
        feed_flow_deviation = feed_flow - 10.0
        pressure_deviation = self.column_pressure - 105.0

        target_top = (
            TOP_TEMP_SETPOINT
            + 0.12 * feed_flow_deviation
            - 0.35 * (reflux_flow - 5.5)
            + 0.04 * pressure_deviation
            + 0.06 * comp_deviation
        )
        target_bottom = BOTTOM_TEMP_SETPOINT + 0.12 * (reboiler_duty - 50.0) + 0.06 * feed_flow_deviation + 0.03 * comp_deviation
        target_pressure = 105.0 + 0.22 * (reboiler_duty - 50.0) + 0.18 * feed_flow_deviation - 0.32 * (condenser_valve - 55.0)

        top_temperature = self.top_temperature + (target_top - self.top_temperature) * dt / 18.0
        bottom_temperature = self.bottom_temperature + (target_bottom - self.bottom_temperature) * dt / 22.0
        if manual_top_temperature is not None:
            top_temperature = float(manual_top_temperature)
        if manual_bottom_temperature is not None:
            bottom_temperature = float(manual_bottom_temperature)
        target_layers = interpolate_column_layer_temperatures(bottom_temperature, top_temperature)[1:-1]
        response_fraction = 1.0 - exp(-max(0.0, dt) / MIDDLE_LAYER_TIME_CONSTANT_SECONDS)
        middle_layer_temperatures = tuple(
            current + response_fraction * (target - current)
            for current, target in zip(self.middle_layer_temperatures, target_layers)
        )
        mid_temperature = middle_layer_temperatures[2]
        column_pressure = clamp(self.column_pressure + (target_pressure - self.column_pressure) * dt / 16.0, 80.0, 160.0)

        quality_penalty = abs(top_temperature - TOP_TEMP_SETPOINT) * 1.8 + max(0.0, 4.0 - reflux_flow) * 2.5 + abs(feed_comp - 0.50) * 35.0
        purity_proxy = clamp(97.0 - quality_penalty, 60.0, 99.0)
        separation_efficiency = clamp(purity_proxy / 100.0, 0.0, 1.0)
        cooling_water_flow = condenser_valve * 0.32

        return replace(
            self,
            feed_tank_level=feed_tank_level,
            feed_inlet_flow=feed_inlet_flow,
            feed_composition_light=feed_comp,
            feed_flow=feed_flow,
            top_temperature=top_temperature,
            mid_temperature=mid_temperature,
            bottom_temperature=bottom_temperature,
            column_pressure=column_pressure,
            reflux_drum_level=reflux_drum_level,
            bottom_sump_level=bottom_sump_level,
            reflux_flow=reflux_flow,
            distillate_flow=distillate_flow,
            bottoms_flow=bottoms_flow,
            distillate_outlet_flow=distillate_outlet_flow,
            bottoms_outlet_flow=bottoms_outlet_flow,
            cooling_water_flow=cooling_water_flow,
            purity_proxy=purity_proxy,
            separation_efficiency=separation_efficiency,
            distillate_tank_level=distillate_tank_level,
            bottoms_tank_level=bottoms_tank_level,
            middle_layer_temperatures=middle_layer_temperatures,
        )

    def column_layer_temperatures(self) -> tuple[float, ...]:
        return (
            float(self.bottom_temperature),
            *(float(value) for value in self.middle_layer_temperatures),
            float(self.top_temperature),
        )

    def to_tags(self) -> dict[str, float]:
        tags = {
            "DT101.PV.FEED_TANK_LEVEL": self.feed_tank_level,
            "DT101.PV.FEED_INLET_FLOW": self.feed_inlet_flow,
            "DT101.PV.FEED_X_LIGHT": self.feed_composition_light,
            "DT101.PV.FEED_FLOW": self.feed_flow,
            "DT101.PV.FEED_TEMP": self.feed_temperature,
            "DT101.PV.TOP_TEMP": self.top_temperature,
            "DT101.PV.MID_TEMP": self.mid_temperature,
            "DT101.PV.BOTTOM_TEMP": self.bottom_temperature,
            "DT101.PV.COLUMN_PRESSURE": self.column_pressure,
            "DT101.PV.REFLUX_DRUM_LEVEL": self.reflux_drum_level,
            "DT101.PV.BOTTOM_LEVEL": self.bottom_sump_level,
            "DT101.PV.REFLUX_FLOW": self.reflux_flow,
            "DT101.PV.DISTILLATE_FLOW": self.distillate_flow,
            "DT101.PV.BOTTOMS_FLOW": self.bottoms_flow,
            "DT101.PV.DISTILLATE_OUTLET_FLOW": self.distillate_outlet_flow,
            "DT101.PV.BOTTOMS_OUTLET_FLOW": self.bottoms_outlet_flow,
            "DT101.PV.COOLING_WATER_FLOW": self.cooling_water_flow,
            "DT101.PV.PURITY_PROXY": self.purity_proxy,
            "DT101.PV.SEPARATION_EFFICIENCY": self.separation_efficiency,
            "DT101.PV.DISTILLATE_TANK_LEVEL": self.distillate_tank_level,
            "DT101.PV.BOTTOMS_TANK_LEVEL": self.bottoms_tank_level,
        }
        for index, temperature in enumerate(self.column_layer_temperatures(), start=1):
            tags[f"DT101.PV.LAYER_{index:02d}_TEMP"] = temperature
        return tags
