from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Iterable

import httpx

from .visualization import (
    EQUIPMENT_STATE_TREND_TAGS,
    LAYER_TEMPERATURE_TAGS,
    PROCESS_TREND_TAGS,
    TANK_LEVEL_TREND_TAGS,
)


DEFAULT_THINGSBOARD_HOST = "https://thingsboard.cloud"
DEFAULT_THINGSBOARD_MQTT_HOST = "mqtt.thingsboard.cloud"
DEFAULT_THINGSBOARD_MQTT_PORT = 1883
DEFAULT_THINGSBOARD_MQTT_TOPIC = "v1/devices/me/telemetry"


@dataclass(frozen=True)
class CloudUploadResult:
    sent: bool
    points: int = 0
    status_code: int | None = None
    message: str = ""


def cloud_trend_tags() -> tuple[str, ...]:
    return (
        *PROCESS_TREND_TAGS,
        *EQUIPMENT_STATE_TREND_TAGS,
        *TANK_LEVEL_TREND_TAGS,
        *LAYER_TEMPERATURE_TAGS,
    )


def _timestamp_ms(timestamp: str | datetime) -> int:
    if isinstance(timestamp, datetime):
        parsed = timestamp
    else:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _telemetry_value(tag: str, value: Any) -> int | float | bool | str | None:
    if value is None:
        return None
    if tag in EQUIPMENT_STATE_TREND_TAGS:
        return 1 if bool(value) else 0
    if isinstance(value, (int, float, bool, str)):
        return value
    return None


def build_thingsboard_payload(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    trend_tags = set(cloud_trend_tags())
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        tag = str(row.get("tag", ""))
        if tag not in trend_tags:
            continue
        value = _telemetry_value(tag, row.get("value"))
        if value is None:
            continue
        tick = int(row.get("tick", 0))
        ts = _timestamp_ms(row["timestamp"])
        grouped.setdefault((tick, ts), {})[tag] = value
    return [
        {"ts": ts, "values": grouped[(tick, ts)]}
        for tick, ts in sorted(grouped)
        if grouped[(tick, ts)]
    ]


class ThingsBoardCloudBridge:
    def __init__(
        self,
        *,
        host: str = DEFAULT_THINGSBOARD_HOST,
        access_token: str,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.access_token = access_token.strip()
        self.timeout = timeout
        self.transport = transport

    @property
    def telemetry_url(self) -> str:
        return f"{self.host}/api/v1/{self.access_token}/telemetry"

    def upload_rows(self, rows: Iterable[dict[str, Any]]) -> CloudUploadResult:
        payload = build_thingsboard_payload(rows)
        if not payload:
            return CloudUploadResult(sent=False, points=0, message="No telemetry rows to upload.")

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(self.telemetry_url, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return CloudUploadResult(
                sent=False,
                points=0,
                status_code=exc.response.status_code,
                message=str(exc),
            )
        except httpx.HTTPError as exc:
            return CloudUploadResult(sent=False, points=0, message=str(exc))

        point_count = sum(len(item["values"]) for item in payload)
        return CloudUploadResult(sent=True, points=point_count, status_code=response.status_code)


def _default_mqtt_client_factory():
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError(
            "paho-mqtt is required for MQTT upload. Install dependencies from requirements.txt."
        ) from exc
    return mqtt.Client()


class MqttThingsBoardCloudBridge:
    def __init__(
        self,
        *,
        host: str = DEFAULT_THINGSBOARD_MQTT_HOST,
        access_token: str,
        port: int = DEFAULT_THINGSBOARD_MQTT_PORT,
        topic: str = DEFAULT_THINGSBOARD_MQTT_TOPIC,
        qos: int = 1,
        keepalive: int = 60,
        timeout: float = 5.0,
        client_factory: Any | None = None,
    ) -> None:
        self.host = host.strip()
        self.access_token = access_token.strip()
        self.port = int(port)
        self.topic = topic
        self.qos = int(qos)
        self.keepalive = int(keepalive)
        self.timeout = float(timeout)
        self.client_factory = client_factory or _default_mqtt_client_factory

    def upload_rows(self, rows: Iterable[dict[str, Any]]) -> CloudUploadResult:
        payload = build_thingsboard_payload(rows)
        if not payload:
            return CloudUploadResult(sent=False, points=0, message="No telemetry rows to upload.")

        try:
            client = self.client_factory()
            client.username_pw_set(self.access_token)
            connect_result = client.connect(self.host, self.port, self.keepalive)
            if connect_result not in (0, None):
                return CloudUploadResult(
                    sent=False,
                    points=0,
                    message=f"MQTT connect failed with result code {connect_result}.",
                )
            publish_info = client.publish(
                self.topic,
                json.dumps(payload, separators=(",", ":")),
                qos=self.qos,
            )
            if hasattr(publish_info, "wait_for_publish"):
                publish_info.wait_for_publish(timeout=self.timeout)
            publish_result = getattr(publish_info, "rc", 0)
            if publish_result not in (0, None):
                return CloudUploadResult(
                    sent=False,
                    points=0,
                    message=f"MQTT publish failed with result code {publish_result}.",
                )
            client.disconnect()
        except Exception as exc:
            return CloudUploadResult(sent=False, points=0, message=str(exc))

        point_count = sum(len(item["values"]) for item in payload)
        return CloudUploadResult(sent=True, points=point_count, message="MQTT")
