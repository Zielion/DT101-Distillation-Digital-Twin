from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FaultManager:
    active_faults: set[str] = field(default_factory=set)

    def inject(self, name: str) -> None:
        self.active_faults.add(name)

    def clear(self, name: str) -> None:
        self.active_faults.discard(name)

    def clear_all(self) -> None:
        self.active_faults.clear()

    def apply(self) -> dict[str, bool | float]:
        modifiers: dict[str, bool | float] = {}
        if "top_temp_drift" in self.active_faults:
            modifiers["top_temp_drift"] = 8.0
        if "reflux_valve_stuck" in self.active_faults:
            modifiers["reflux_valve_stuck_position"] = 20.0
        if "feed_composition_disturbance" in self.active_faults:
            modifiers["feed_composition_light"] = 0.64
        if "data_stale" in self.active_faults:
            modifiers["data_stale"] = True
        return modifiers

    def detect(
        self,
        tags: dict[str, float | str | bool],
        controls: dict[str, float | bool | datetime],
        now: datetime | None = None,
    ) -> list[str]:
        now = now or datetime.now(timezone.utc)
        alarms: list[str] = []
        top_temp = float(tags.get("DT101.PV.TOP_TEMP", 79.0))
        pressure = float(tags.get("DT101.PV.COLUMN_PRESSURE", 105.0))
        reflux_flow = float(tags.get("DT101.PV.REFLUX_FLOW", 5.5))
        purity = float(tags.get("DT101.PV.PURITY_PROXY", 96.0))
        feed_comp = float(tags.get("DT101.PV.FEED_X_LIGHT", 0.50))

        if top_temp > 85.0 and 95.0 <= pressure <= 115.0 and reflux_flow >= 4.0 and purity >= 94.0:
            alarms.append("DT101.ALARM.TOP_TEMP_SENSOR_DRIFT")

        reflux_command = float(controls.get("reflux_valve", controls.get("DT101.CMD.REFLUX_VALVE", 50.0)))
        reflux_feedback = float(controls.get("reflux_valve_feedback", controls.get("DT101.FB.REFLUX_VALVE_POSITION", reflux_command)))
        if reflux_command - reflux_feedback > 25.0 and reflux_flow < 2.5:
            alarms.append("DT101.ALARM.REFLUX_VALVE_STUCK")

        if abs(feed_comp - 0.50) > 0.10 and purity < 90.0 and abs(top_temp - 79.0) > 4.0:
            alarms.append("DT101.ALARM.FEED_COMPOSITION_DISTURBANCE")

        heartbeat = controls.get("last_heartbeat")
        if isinstance(heartbeat, datetime) and (now - heartbeat).total_seconds() > 30.0:
            alarms.append("DT101.ALARM.DATA_STALE")

        return alarms

