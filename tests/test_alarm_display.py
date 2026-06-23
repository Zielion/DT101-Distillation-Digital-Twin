from datetime import datetime, timedelta, timezone

from distillation.alarm_display import update_alarm_texts


def test_alarm_text_remains_for_five_wall_clock_seconds_after_a_transient_alarm():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry, visible = update_alarm_texts({}, ["ALARM.A"], started)

    registry, visible = update_alarm_texts(registry, [], started + timedelta(seconds=4.999))
    assert visible == ["ALARM.A"]

    registry, visible = update_alarm_texts(registry, [], started + timedelta(seconds=5))
    assert registry == {}
    assert visible == []


def test_active_alarm_remains_visible_after_five_seconds_and_clears_on_resolution():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry, _ = update_alarm_texts({}, ["ALARM.A"], started)

    registry, visible = update_alarm_texts(registry, ["ALARM.A"], started + timedelta(seconds=10))
    assert visible == ["ALARM.A"]

    registry, visible = update_alarm_texts(registry, [], started + timedelta(seconds=10, microseconds=1))
    assert registry == {}
    assert visible == []


def test_repeated_scans_do_not_restart_an_alarms_first_trigger_clock():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry, _ = update_alarm_texts({}, ["ALARM.A"], started)
    registry, _ = update_alarm_texts(registry, ["ALARM.A"], started + timedelta(seconds=4))

    assert registry["ALARM.A"] == started
