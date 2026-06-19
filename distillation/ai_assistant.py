from __future__ import annotations

import os
from typing import Any

import httpx

from .config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


FAULT_CATALOG = {
    "DT101.ALARM.TOP_TEMP_SENSOR_DRIFT": "Top temperature sensor drift: compare top temperature with pressure, reflux flow, and purity proxy.",
    "DT101.ALARM.REFLUX_VALVE_STUCK": "Reflux valve stuck: command-feedback mismatch, low reflux flow, rising top temperature.",
    "DT101.ALARM.FEED_COMPOSITION_DISTURBANCE": "Feed composition disturbance: purity loss, temperature profile deviation, PID compensation.",
    "DT101.ALARM.DATA_STALE": "Infrastructure outage: heartbeat missing or tag timestamps stale.",
    "DT101.ALARM.HIGH_HIGH_PRESSURE": "Safety trip: pressure high-high requires local PLC shutdown actions.",
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
            "keep fast safety actions in the PLC/edge layer.\n\n"
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
        if "DT101.ALARM.REFLUX_VALVE_STUCK" in alarms:
            cause = "The reflux valve is not following its command, which can reduce separation quality."
            action = "Reduce feed load and ask a technician to inspect valve air supply, positioner, and feedback."
        elif "DT101.ALARM.DATA_STALE" in alarms:
            cause = "The upstream data path is stale, so frozen dashboard values may not represent stable operation."
            action = "Verify local PLC/HMI status and restore broker or historian connectivity."
        elif "DT101.ALARM.FEED_COMPOSITION_DISTURBANCE" in alarms:
            cause = "Feed composition appears to have shifted, causing purity and temperature deviation."
            action = "Reduce feed rate, monitor product quality, and verify the upstream feed source."
        elif "DT101.ALARM.TOP_TEMP_SENSOR_DRIFT" in alarms:
            cause = "The top temperature signal is inconsistent with related pressure, reflux, and purity evidence."
            action = "Cross-check the transmitter before changing reflux aggressively."
        else:
            cause = "The plant is outside normal operation or has no classified active fault."
            action = "Review live trends, confirm alarm status, and keep PLC safety interlocks active."
        return (
            f"Fault summary:\n{alarm_text}\n\n"
            f"Evidence:\nReview active alarms, recent tag trends, and command-feedback consistency.\n\n"
            f"Likely cause:\n{cause}\n\n"
            f"Immediate operator action:\n{action}\n\n"
            f"Follow-up check:\nConfirm pressure, temperature profile, reflux flow, and data freshness recover to normal.\n\n"
            f"Safety caution:\nDo not bypass high-pressure interlocks or let the AI directly control actuators."
        )

