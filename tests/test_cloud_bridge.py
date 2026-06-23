from __future__ import annotations

from datetime import datetime, timezone

import httpx

from distillation import visualization
from distillation.cloud_bridge import (
    MqttThingsBoardCloudBridge,
    ThingsBoardCloudBridge,
    build_thingsboard_payload,
    cloud_trend_tags,
)


def test_cloud_trend_tags_match_the_four_historian_charts():
    assert cloud_trend_tags() == (
        *visualization.PROCESS_TREND_TAGS,
        *visualization.EQUIPMENT_STATE_TREND_TAGS,
        *visualization.TANK_LEVEL_TREND_TAGS,
        *visualization.LAYER_TEMPERATURE_TAGS,
    )


def test_payload_groups_historian_rows_by_timestamp_and_converts_equipment_states_to_one_zero():
    rows = [
        {
            "timestamp": "2026-06-23T01:02:03+00:00",
            "tick": 7,
            "tag": "DT101.PV.TOP_TEMP",
            "value": 78.5,
        },
        {
            "timestamp": "2026-06-23T01:02:03+00:00",
            "tick": 7,
            "tag": "DT101.FB.FEED_SUPPLY_PUMP_RUNNING",
            "value": True,
        },
        {
            "timestamp": "2026-06-23T01:02:04+00:00",
            "tick": 8,
            "tag": "DT101.FB.FEED_SUPPLY_PUMP_RUNNING",
            "value": False,
        },
        {
            "timestamp": "2026-06-23T01:02:04+00:00",
            "tick": 8,
            "tag": "DT101.PV.LAYER_07_TEMP",
            "value": 79.0,
        },
        {
            "timestamp": "2026-06-23T01:02:04+00:00",
            "tick": 8,
            "tag": "DT101.STATE.MODE",
            "value": "NORMAL_OPERATION",
        },
    ]

    payload = build_thingsboard_payload(rows)

    assert payload == [
        {
            "ts": 1782176523000,
            "values": {
                "DT101.PV.TOP_TEMP": 78.5,
                "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": 1,
            },
        },
        {
            "ts": 1782176524000,
            "values": {
                "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": 0,
                "DT101.PV.LAYER_07_TEMP": 79.0,
            },
        },
    ]


def test_payload_accepts_datetime_rows_and_skips_empty_values():
    rows = [
        {
            "timestamp": datetime(2026, 6, 23, 1, 2, 3, tzinfo=timezone.utc),
            "tick": 7,
            "tag": "DT101.PV.BOTTOM_TEMP",
            "value": None,
        },
        {
            "timestamp": datetime(2026, 6, 23, 1, 2, 3, tzinfo=timezone.utc),
            "tick": 7,
            "tag": "DT101.PV.BOTTOM_TEMP",
            "value": 101.0,
        },
    ]

    assert build_thingsboard_payload(rows) == [
        {"ts": 1782176523000, "values": {"DT101.PV.BOTTOM_TEMP": 101.0}}
    ]


def test_thingsboard_bridge_posts_batch_payload_to_access_token_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={})

    bridge = ThingsBoardCloudBridge(
        host="https://thingsboard.cloud/",
        access_token="device-token",
        transport=httpx.MockTransport(handler),
    )

    result = bridge.upload_rows(
        [
            {
                "timestamp": "2026-06-23T01:02:03+00:00",
                "tick": 7,
                "tag": "DT101.PV.TOP_TEMP",
                "value": 78.5,
            }
        ]
    )

    assert result.sent is True
    assert result.points == 1
    assert captured["method"] == "POST"
    assert captured["url"] == "https://thingsboard.cloud/api/v1/device-token/telemetry"
    assert '"DT101.PV.TOP_TEMP":78.5' in captured["body"]


def test_mqtt_bridge_publishes_batch_payload_with_access_token_username():
    events = []

    class FakePublishInfo:
        rc = 0

        def wait_for_publish(self, timeout=None):
            events.append(("wait_for_publish", timeout))

    class FakeMqttClient:
        def username_pw_set(self, username, password=None):
            events.append(("username_pw_set", username, password))

        def connect(self, host, port, keepalive):
            events.append(("connect", host, port, keepalive))
            return 0

        def publish(self, topic, payload, qos=0):
            events.append(("publish", topic, payload, qos))
            return FakePublishInfo()

        def disconnect(self):
            events.append(("disconnect",))

    bridge = MqttThingsBoardCloudBridge(
        host="mqtt.thingsboard.cloud",
        access_token="device-token",
        client_factory=FakeMqttClient,
    )

    result = bridge.upload_rows(
        [
            {
                "timestamp": "2026-06-23T01:02:03+00:00",
                "tick": 7,
                "tag": "DT101.PV.TOP_TEMP",
                "value": 78.5,
            }
        ]
    )

    assert result.sent is True
    assert result.points == 1
    assert result.status_code is None
    assert events[0] == ("username_pw_set", "device-token", None)
    assert events[1] == ("connect", "mqtt.thingsboard.cloud", 1883, 60)
    publish_event = events[2]
    assert publish_event[0] == "publish"
    assert publish_event[1] == "v1/devices/me/telemetry"
    assert publish_event[3] == 1
    assert '"DT101.PV.TOP_TEMP":78.5' in publish_event[2]
    assert events[-1] == ("disconnect",)
