from __future__ import annotations

import os
from typing import Any

import httpx

from .config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


FAULT_CATALOG = {
    "DT101.ALARM.FEED_TANK_HIGH_HIGH": "Automatic feed cycle transition: feed tank reached the high level used to stop P-100/V-099 and open V-100 after feedback is safe.",
    "DT101.ALARM.FEED_TANK_OVERFILL": "Feed tank capacity trip: feed tank reached 90% capacity and locks P-100/V-099 while allowing controlled V-100 drainage.",
    "DT101.ALARM.DISTILLATE_TANK_OVERFILL": "Distillate product capacity trip: product tank reached 90% capacity and locks the feed path including V-100.",
    "DT101.ALARM.BOTTOMS_TANK_OVERFILL": "Bottom product capacity trip: product tank reached 90% capacity and locks the feed path including V-100.",
    "DT101.ALARM.TOP_TEMP_SENSOR_DRIFT": "Top temperature sensor drift: compare top temperature with pressure, reflux flow, and purity proxy.",
    "DT101.ALARM.REFLUX_VALVE_STUCK": "Reflux valve stuck: command-feedback mismatch, low reflux flow, rising top temperature.",
    "DT101.ALARM.FEED_COMPOSITION_DISTURBANCE": "Feed composition disturbance: purity loss, temperature profile deviation, PID compensation.",
    "DT101.ALARM.DATA_STALE": "Infrastructure outage: heartbeat missing or tag timestamps stale.",
    "DT101.ALARM.HIGH_HIGH_PRESSURE": "Safety trip: pressure high-high requires local PLC shutdown actions.",
}

AUTOMATIC_PROCESS_ALARMS = {
    "DT101.ALARM.FEED_TANK_HIGH_HIGH": (
        "Feed tank reached the automatic high-level transition point. "
        "The PLC will stop P-100, close V-099, wait for feedback, and open V-100 to feed the column."
    ),
    "DT101.ALARM.FEED_TANK_LOW_LOW": (
        "Feed tank reached the low-low refill transition point. "
        "The PLC will close V-100, wait for feedback, and refill through P-100 and V-099."
    ),
    "DT101.ALARM.REFLUX_DRUM_HIGH_HIGH": (
        "Reflux drum level is high. The PLC will increase distillate draw to bring the drum level down."
    ),
}

LOCKING_ALARMS = {
    "DT101.ALARM.FEED_TANK_OVERFILL",
    "DT101.ALARM.DISTILLATE_TANK_OVERFILL",
    "DT101.ALARM.BOTTOMS_TANK_OVERFILL",
    "DT101.ALARM.HIGH_HIGH_PRESSURE",
}


class AIAssistant:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def build_prompt(self, alarm_context: dict[str, Any], recent_history: list[dict[str, Any]]) -> str:
        return (
            "You are an industrial operator assistant for a simulated binary distillation column.\n"
            "Use the evidence to recommend safe operator actions in plain English.\n"
            "Safety rules: never recommend bypassing high-pressure interlock; do not directly control actuators; "
            "keep fast safety actions in the PLC/edge layer. If an alarm is part of an automatic process transition, "
            "briefly explain the cause and say no operator intervention is necessary because the PLC will resolve it. "
            "If an alarm is overcapacity, overpressure, or locks equipment, briefly explain the cause and give operator "
            "recovery steps for the locked equipment.\n\n"
            f"Current context:\n{alarm_context}\n\n"
            f"Recent tag evidence:\n{recent_history[-80:]}\n\n"
            f"Fault catalog:\n{FAULT_CATALOG}\n\n"
            "Return exactly these sections: Fault summary, Evidence, Likely cause, Immediate operator action, "
            "Follow-up check, Safety caution."
        )

    def recommend(self, alarm_context: dict[str, Any], recent_history: list[dict[str, Any]]) -> str:
        prompt = self.build_prompt(alarm_context, recent_history)
        if not self.api_key:
            return self._fallback(alarm_context)

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "You are a safe industrial operations assistant."},
                            {"role": "user", "content": prompt},
                        ],
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            fallback = self._fallback(alarm_context)
            return f"{fallback}\n\nDeepSeek call failed; deterministic fallback used. Error: {exc}"

    def _fallback(self, alarm_context: dict[str, Any]) -> str:
        alarms = alarm_context.get("active_alarms", [])
        alarm_text = ", ".join(alarms) if alarms else "No active alarm"
        alarm_set = set(alarms)
        if alarm_set.intersection(LOCKING_ALARMS):
            cause, action, follow_up = self._locking_alarm_guidance(alarm_set)
        elif automatic_alarm := next((alarm for alarm in alarms if alarm in AUTOMATIC_PROCESS_ALARMS), None):
            cause = AUTOMATIC_PROCESS_ALARMS[automatic_alarm]
            action = (
                "No operator intervention is necessary. Continue monitoring; this alarm is expected to resolve itself "
                "as the automatic PLC sequence completes."
            )
            follow_up = "Confirm the related level returns below the alarm threshold and equipment feedback matches the PLC command."
        elif "DT101.ALARM.REFLUX_VALVE_STUCK" in alarms:
            cause = "The reflux valve is not following its command, which can reduce separation quality."
            action = "Reduce feed load and ask a technician to inspect valve air supply, positioner, and feedback."
            follow_up = "Confirm pressure, temperature profile, reflux flow, and data freshness recover to normal."
        elif "DT101.ALARM.DATA_STALE" in alarms:
            cause = "The upstream data path is stale, so frozen dashboard values may not represent stable operation."
            action = "Verify local PLC/HMI status and restore broker or historian connectivity."
            follow_up = "Confirm pressure, temperature profile, reflux flow, and data freshness recover to normal."
        elif "DT101.ALARM.FEED_COMPOSITION_DISTURBANCE" in alarms:
            cause = "Feed composition appears to have shifted, causing purity and temperature deviation."
            action = "Reduce feed rate, monitor product quality, and verify the upstream feed source."
            follow_up = "Confirm pressure, temperature profile, reflux flow, and data freshness recover to normal."
        elif "DT101.ALARM.TOP_TEMP_SENSOR_DRIFT" in alarms:
            cause = "The top temperature signal is inconsistent with related pressure, reflux, and purity evidence."
            action = "Cross-check the transmitter before changing reflux aggressively."
            follow_up = "Confirm pressure, temperature profile, reflux flow, and data freshness recover to normal."
        else:
            cause = "The plant is outside normal operation or has no classified active fault."
            action = "Review live trends, confirm alarm status, and keep PLC safety interlocks active."
            follow_up = "Confirm pressure, temperature profile, reflux flow, and data freshness recover to normal."
        return (
            f"Fault summary:\n{alarm_text}\n\n"
            f"Evidence:\nReview active alarms, recent tag trends, and command-feedback consistency.\n\n"
            f"Likely cause:\n{cause}\n\n"
            f"Immediate operator action:\n{action}\n\n"
            f"Follow-up check:\n{follow_up}\n\n"
            f"Safety caution:\nDo not bypass high-pressure interlocks or let the AI directly control actuators."
        )

    @staticmethod
    def _locking_alarm_guidance(alarms: set[str]) -> tuple[str, str, str]:
        if "DT101.ALARM.HIGH_HIGH_PRESSURE" in alarms:
            return (
                "Column pressure exceeded the high-high trip limit, so the PLC shutdown interlock is protecting the column.",
                "Do not bypass the pressure interlock. Keep feed closed, verify condenser cooling is available, and wait for pressure to return to a safe range before restarting.",
                "Confirm pressure is stable below the trip point, then restart using the normal automatic sequence.",
            )
        if "DT101.ALARM.FEED_TANK_OVERFILL" in alarms:
            return (
                "Feed tank reached the 90% capacity trip, which locks P-100 and V-099 to prevent additional upstream filling.",
                "To resolve the issue, manually open V-100 while keeping the distillate product and bottom product valves and pumps on Auto or manually forced open until the feed tank alarm is removed.",
                "After the alarm clears, carefully resume automatic operation on the distillate product and bottom product valves and pumps before resuming automatic operation of P-100, V-099, and V-100.",
            )
        if "DT101.ALARM.DISTILLATE_TANK_OVERFILL" in alarms:
            return (
                "Distillate product tank reached the 90% capacity trip, which locks P-100, V-099, and V-100 to stop adding material to the products.",
                "To resolve the issue, manually force open P-201 and V-201 to drain the distillate product tank while keeping the feed supply path and V-100 stopped until the alarm is removed.",
                "After the alarm clears, return P-201 and V-201 to Auto, verify product tank level is stable, then carefully resume automatic operation of P-100, V-099, and V-100.",
            )
        if "DT101.ALARM.BOTTOMS_TANK_OVERFILL" in alarms:
            return (
                "Bottom product tank reached the 90% capacity trip, which locks P-100, V-099, and V-100 to stop adding material to the products.",
                "To resolve the issue, manually force open P-202 and V-202 to drain the bottom product tank while keeping the feed supply path and V-100 stopped until the alarm is removed.",
                "After the alarm clears, return P-202 and V-202 to Auto, verify product tank level is stable, then carefully resume automatic operation of P-100, V-099, and V-100.",
            )
        return (
            "An active alarm is locking equipment to protect the process.",
            "Keep the locked equipment in its safe state, identify the level or pressure source of the lock, and use the appropriate manual drain or cooldown path until the alarm is removed.",
            "After the alarm clears, return affected equipment to Auto in a controlled sequence and verify feedback matches commands.",
        )
