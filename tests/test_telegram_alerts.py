from __future__ import annotations

import httpx

from distillation.telegram_alerts import (
    AlarmEdgeDetector,
    TelegramAlertSender,
    format_alarm_message,
    send_fresh_alarm_alerts,
)


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_alarm_edge_detector_reports_only_rising_edges_and_respects_per_tag_throttle():
    clock = FakeClock()
    detector = AlarmEdgeDetector(throttle_seconds=60, clock=clock)

    first = detector.fresh_trips(
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A", "ALARM.B"],
    )
    repeated = detector.fresh_trips(
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A", "ALARM.B"],
    )
    detector.fresh_trips(active_alarms=[], watched_alarms=["ALARM.A", "ALARM.B"])
    clock.t = 30
    throttled = detector.fresh_trips(
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A", "ALARM.B"],
    )
    clock.t = 61
    detector.fresh_trips(active_alarms=[], watched_alarms=["ALARM.A", "ALARM.B"])
    refired = detector.fresh_trips(
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A", "ALARM.B"],
    )

    assert first == ["ALARM.A"]
    assert repeated == []
    assert throttled == []
    assert refired == ["ALARM.A"]


def test_detector_baseline_suppresses_alarms_already_active_on_startup():
    detector = AlarmEdgeDetector()
    detector.seed({"ALARM.A": True, "ALARM.B": False})

    assert detector.fresh_trips(["ALARM.A"], ["ALARM.A", "ALARM.B"]) == []
    assert detector.fresh_trips(["ALARM.A", "ALARM.B"], ["ALARM.A", "ALARM.B"]) == ["ALARM.B"]


def test_telegram_sender_posts_to_bot_api_with_chat_id_and_plain_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    sender = TelegramAlertSender(
        bot_token="bot-token",
        chat_id="123456",
        transport=httpx.MockTransport(handler),
    )

    result = sender.send("hello alarm")

    assert result.sent is True
    assert result.status_code == 200
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.telegram.org/botbot-token/sendMessage"
    assert '"chat_id":"123456"' in captured["body"]
    assert '"text":"hello alarm"' in captured["body"]


def test_sender_dry_run_succeeds_without_token_or_chat_id():
    sender = TelegramAlertSender(bot_token="", chat_id="", dry_run=True)

    result = sender.send("ALARM.A")

    assert result.sent is True
    assert result.dry_run is True
    assert result.message == "dry-run"


def test_send_fresh_alarm_alerts_formats_and_sends_only_new_trips():
    sent_messages: list[str] = []

    class FakeSender:
        def send(self, text: str):
            sent_messages.append(text)
            return object()

    detector = AlarmEdgeDetector()
    labels = {"ALARM.A": "Feed tank high-high"}

    send_fresh_alarm_alerts(
        detector=detector,
        sender=FakeSender(),
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A"],
        labels=labels,
        mode="NORMAL_OPERATION",
        timestamp="2026-06-24T12:00:00+08:00",
    )
    send_fresh_alarm_alerts(
        detector=detector,
        sender=FakeSender(),
        active_alarms=["ALARM.A"],
        watched_alarms=["ALARM.A"],
        labels=labels,
        mode="NORMAL_OPERATION",
        timestamp="2026-06-24T12:00:01+08:00",
    )

    assert sent_messages == [
        format_alarm_message(
            "ALARM.A",
            label="Feed tank high-high",
            mode="NORMAL_OPERATION",
            timestamp="2026-06-24T12:00:00+08:00",
        )
    ]


def test_send_fresh_alarm_alerts_ignores_active_alarms_outside_the_watch_list():
    sent_messages: list[str] = []

    class FakeSender:
        def send(self, text: str):
            sent_messages.append(text)
            return object()

    send_fresh_alarm_alerts(
        detector=AlarmEdgeDetector(),
        sender=FakeSender(),
        active_alarms=["DT101.ALARM.TOP_TEMP_SENSOR_DRIFT"],
        watched_alarms=["DT101.ALARM.FEED_TANK_OVERFILL"],
    )

    assert sent_messages == []
