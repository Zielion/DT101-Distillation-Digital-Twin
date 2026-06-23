from __future__ import annotations

from datetime import datetime, timedelta


ALARM_TEXT_MINIMUM_DURATION = timedelta(seconds=5)


def update_alarm_texts(
    first_triggered_at: dict[str, datetime],
    active_alarms: list[str],
    now: datetime,
) -> tuple[dict[str, datetime], list[str]]:
    """Track first trigger times and return alarms whose banner text is visible."""
    active = set(active_alarms)
    registry = dict(first_triggered_at)
    for alarm in active:
        registry.setdefault(alarm, now)

    registry = {
        alarm: started
        for alarm, started in registry.items()
        if alarm in active or now - started < ALARM_TEXT_MINIMUM_DURATION
    }
    return registry, sorted(registry)
